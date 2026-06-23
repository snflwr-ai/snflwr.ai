"""
snflwr.ai API Server
FastAPI backend for safety monitoring and profile management
"""

import os
import sys
import signal
import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
from datetime import datetime, timezone

from api import __version__
from config import system_config
from storage.encryption import is_encryption_available
from storage.db_adapters import DB_ERRORS
from utils.logger import (
    get_logger,
    correlation_id_var,
    set_correlation_id,
    get_correlation_id,
    sanitize_log_value,
)
from api.middleware.auth import require_admin

logger = get_logger(__name__)

# Conditional Redis error import
try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # type: ignore[misc,assignment]

# Rate limiter for unauthenticated endpoints
from utils.rate_limiter import RateLimiter

_setup_rate_limiter = RateLimiter()


def check_setup_rate_limit(request: Request):
    """
    Rate limiting for system setup endpoints.
    Conservative: 5 requests per hour per IP to prevent brute-force
    account creation on fresh deployments.
    """
    client_ip = request.client.host if request.client else "unknown"
    allowed, info = _setup_rate_limiter.check_rate_limit(
        identifier=client_ip,
        max_requests=5,
        window_seconds=3600,
        limit_type="setup",
        fail_closed=True,
    )
    if not allowed:
        retry_after = info.get("retry_after", 3600) if isinstance(info, dict) else 3600
        logger.warning(f"Setup rate limit exceeded for IP {client_ip}")
        raise HTTPException(
            status_code=429,
            detail=f"Too many setup attempts. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    return info


# ===========================================================================
# STARTUP SECURITY VALIDATION
# Automatically checks configuration before the app can serve any requests.
# In production, the app will refuse to start if security settings are wrong.
# In development, it logs warnings so you know what to fix before going live.
# ===========================================================================
try:
    _startup_errors = system_config.validate_production_security()
    if _startup_errors:
        for _err in _startup_errors:
            logger.warning(f"Security check: {_err}")
except RuntimeError as _security_error:
    logger.critical(f"STARTUP BLOCKED: {_security_error}")
    raise SystemExit(
        f"STARTUP BLOCKED — the app cannot start with the current configuration.\n\n"
        f"{_security_error}\n\n"
        f"To fix this, run the setup script:\n"
        f"    python scripts/setup_production.py\n\n"
        f"It will walk you through the setup step by step (takes ~2 minutes)."
    )

if not is_encryption_available():
    if system_config.is_production() or system_config.is_production_like():
        raise SystemExit(
            "STARTUP BLOCKED: 'cryptography' package is not installed.\n"
            "Child data CANNOT be encrypted without it.\n\n"
            "To fix this, run:  pip install cryptography\n"
            "Then re-run:       python scripts/setup_production.py"
        )
    else:
        logger.warning(
            "WARNING: 'cryptography' package not installed — encryption is DISABLED. "
            "This is acceptable for local development only."
        )

# Global shutdown flag
_shutdown_event: Optional[asyncio.Event] = None
_active_connections: int = 0
_connections_lock = asyncio.Lock()

# Request body size limit (10MB default, configurable)
MAX_REQUEST_SIZE = getattr(system_config, "MAX_REQUEST_SIZE_MB", 10) * 1024 * 1024


# Maximum time (in seconds) allowed for startup before the server aborts.
# Override with the STARTUP_TIMEOUT_SECONDS environment variable.
STARTUP_TIMEOUT_SECONDS = int(os.getenv("STARTUP_TIMEOUT_SECONDS", "60"))


async def check_key_rotation_age() -> None:
    """Warn operator if INTERNAL_API_KEY is overdue for rotation."""
    from datetime import datetime, timezone

    # Read rotation config — use getattr to avoid CodeQL taint propagation
    # from config module's API key namespace.
    import config as _cfg

    created_at = getattr(_cfg, "INTERNAL_API_KEY_CREATED_AT", None)
    max_age = int(getattr(_cfg, "INTERNAL_API_KEY_MAX_AGE_DAYS", 90))

    if created_at is None:
        logger.info(
            "INTERNAL_API_KEY_CREATED_AT not set — "
            "set it to enable key rotation age warnings."
        )
        return

    age_days = (datetime.now(timezone.utc) - created_at).days
    if age_days > max_age:
        logger.warning(
            "API key rotation overdue: %d days old (max %d)",
            age_days,
            max_age,
        )
        try:
            from core.email_service import email_service

            email_service.send_operator_alert(
                subject="API key rotation overdue",
                description=(
                    f"Internal API key is {age_days} days old "
                    f"(max recommended: {max_age}). "
                    f"Rotate it with: python -c "
                    f"'import secrets; print(secrets.token_hex(32))'"
                ),
            )
        except Exception:
            pass  # Alert is best-effort; the warning is logged


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown logic."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    startup_start = asyncio.get_event_loop().time()

    logger.info("=" * 60)
    logger.info("snflwr.ai API Server Starting")
    logger.info(f"Startup timeout: {STARTUP_TIMEOUT_SECONDS}s")
    logger.info("=" * 60)

    # Set up graceful shutdown handlers
    try:
        setup_signal_handlers()
        logger.info("Graceful shutdown handlers registered")
    except Exception as e:
        logger.warning(f"Could not register signal handlers: {e}")

    # Initialize Prometheus metrics
    try:
        from utils.metrics import init_app_info

        init_app_info(
            version=__version__, environment=os.getenv("ENVIRONMENT", "development")
        )
        logger.info("Prometheus metrics initialized")
    except ImportError:
        logger.warning(
            "Prometheus metrics not available (prometheus_client not installed)"
        )

    # Ensure database schema exists (creates tables if missing)
    try:
        from storage.database import db_manager

        db_manager.initialize_database()
        logger.info(f"Database schema initialized ({system_config.DATABASE_TYPE})")
    except Exception as e:
        logger.error(f"Database schema initialization failed: {e}")
        raise RuntimeError(f"Cannot start without database: {e}")

    # Validate cryptography library is available (required for encryption)
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        logger.info("Cryptography library available")
    except ImportError as e:
        if system_config.is_production() or system_config.is_production_like():
            logger.error("=" * 60)
            logger.error("STARTUP FAILED: 'cryptography' package not available")
            logger.error("The cryptography library is REQUIRED for:")
            logger.error("   - AES-256 encryption of sensitive data")
            logger.error("   - Secure password hashing (PBKDF2)")
            logger.error("   - Email encryption")
            logger.error("")
            logger.error("To fix:")
            logger.error("   pip install cryptography==43.0.3")
            logger.error("=" * 60)
            raise RuntimeError(f"Cryptography library required but not available: {e}")
        else:
            logger.warning(
                "Cryptography library not installed — encryption DISABLED. "
                "Acceptable for local development only."
            )

    # Validate Redis connection if enabled
    if system_config.REDIS_ENABLED:
        from utils.cache import cache

        if not cache.health_check():
            if system_config.is_production():
                # Production: hard failure — Redis is a security requirement
                logger.error("=" * 60)
                logger.error("STARTUP FAILED: Redis connection unavailable")
                logger.error("Redis is REQUIRED in production for:")
                logger.error(
                    "   - Authentication rate limiting (brute force protection)"
                )
                logger.error("   - Distributed caching")
                logger.error("   - Celery task queue")
                logger.error("")
                logger.error("To fix:")
                logger.error("   1. Start Redis: docker-compose up redis")
                logger.error("   2. Check REDIS_HOST / REDIS_PORT / REDIS_PASSWORD")
                logger.error("=" * 60)
                raise RuntimeError("Redis connection required but unavailable")
            else:
                # Non-production: warn and continue in degraded mode
                logger.warning("=" * 60)
                logger.warning(
                    "Redis connection unavailable — running in DEGRADED MODE"
                )
                logger.warning(
                    "Rate limiting will use in-memory fallback (per-process only)."
                )
                logger.warning("Session caching will use in-memory fallback.")
                logger.warning("Celery background tasks will NOT be available.")
                logger.warning("")
                logger.warning(
                    "The cache will auto-reconnect every %ds if Redis comes back.",
                    cache.RECONNECT_INTERVAL,
                )
                logger.warning("=" * 60)
        else:
            logger.info(
                f"Redis connected: {system_config.REDIS_HOST}:{system_config.REDIS_PORT}"
            )

        # Start WebSocket Redis Pub/Sub for horizontal scaling
        try:
            from api.websocket_server import websocket_manager

            await websocket_manager.start_pubsub()
            logger.info("WebSocket Redis Pub/Sub started")
        except Exception as e:
            logger.warning(f"WebSocket Pub/Sub not started: {e}")
    else:
        logger.warning("Redis is DISABLED - authentication rate limiting unavailable")

    logger.info(f"Host: {system_config.API_HOST}:{system_config.API_PORT}")
    logger.info(f"Database: {system_config.DATABASE_TYPE}")
    logger.info(f"Safety Monitoring: {system_config.ENABLE_SAFETY_MONITORING}")

    # Log detected hardware and auto-tuned configuration
    try:
        from resource_detection import get_resource_profile

        profile = get_resource_profile()
        logger.info("-" * 60)
        logger.info("Detected Resources & Auto-Tuned Configuration")
        for line in profile.summary_lines():
            logger.info(f"  {line}")
        logger.info("  (override any value via its env var, e.g. API_WORKERS=8)")
        logger.info("-" * 60)
    except Exception as e:
        logger.warning(f"Could not log resource profile: {e}")

    # Enforce startup timeout
    elapsed = asyncio.get_event_loop().time() - startup_start
    if elapsed > STARTUP_TIMEOUT_SECONDS:
        logger.critical(
            f"Startup took {elapsed:.1f}s, exceeding the {STARTUP_TIMEOUT_SECONDS}s limit. "
            "Aborting. Check database and Redis connectivity, or raise STARTUP_TIMEOUT_SECONDS."
        )
        raise RuntimeError(
            f"Startup timeout exceeded ({elapsed:.1f}s > {STARTUP_TIMEOUT_SECONDS}s)"
        )

    logger.info(f"Startup completed in {elapsed:.1f}s")
    logger.info("=" * 60)

    # Check API key rotation age
    await check_key_rotation_age()

    # Start classifier health probe
    classifier_probe_task = None
    try:
        from safety.pipeline import safety_pipeline

        if hasattr(safety_pipeline, "_classifier"):
            clf = safety_pipeline._classifier
            # Loud, not silent: if the ML safety layer didn't come up (e.g. the
            # safety model isn't pulled), alert the operator now.
            clf.alert_if_unavailable()
            classifier_probe_task = asyncio.create_task(clf.run_health_probe())
            logger.info("Classifier health probe started (state=%s)", clf._state)
    except Exception as exc:
        logger.warning("Could not start classifier health probe: %s", exc)

    # Start email alert worker thread
    try:
        from utils.email_alerts import email_alert_system

        email_alert_system.start_worker()
        logger.info("Email alert worker started")
    except Exception as e:
        logger.warning(f"Email alert worker could not start: {e}")

    # License refresh background task (only when a license server is configured)
    try:
        if system_config.LICENSE_SERVER_URL and system_config.LICENSE_ENFORCED:
            from tasks.license_refresh import run_refresh_loop

            app.state.license_stop = asyncio.Event()
            app.state.license_task = asyncio.create_task(
                run_refresh_loop(app.state.license_stop)
            )
            logger.info("License refresh task started")
    except Exception as exc:
        logger.warning("License refresh task could not start: %s", exc)

    yield

    # Stop license refresh task
    _lic_stop = getattr(app.state, "license_stop", None)
    if _lic_stop is not None:
        _lic_stop.set()
        _lic_task = getattr(app.state, "license_task", None)
        if _lic_task is not None:
            try:
                await asyncio.wait_for(_lic_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        logger.info("License refresh task stopped")

    # Cancel classifier probe
    if classifier_probe_task:
        classifier_probe_task.cancel()
        try:
            await classifier_probe_task
        except asyncio.CancelledError:
            pass
        logger.info("Classifier health probe stopped")

    # Stop WebSocket Redis Pub/Sub
    try:
        from api.websocket_server import websocket_manager

        await websocket_manager.stop_pubsub()
        logger.info("WebSocket Redis Pub/Sub stopped")
    except Exception as e:
        logger.warning(f"WebSocket Pub/Sub could not stop cleanly: {e}")

    # Stop email alert worker thread
    try:
        from utils.email_alerts import email_alert_system

        email_alert_system.stop_worker()
        logger.info("Email alert worker stopped")
    except Exception as e:
        logger.warning(f"Email alert worker could not stop cleanly: {e}")

    # Shutdown
    # Close PostgreSQL connection pool if active
    try:
        from storage.database import db_manager

        adapter = db_manager._get_adapter()
        if hasattr(adapter, "shutdown_pool"):
            adapter.shutdown_pool()
            logger.info("Database connection pool closed")
    except Exception as e:
        logger.warning(f"Could not close database connection pool: {e}")

    logger.info("snflwr.ai API Server Shutdown Complete")


# Create FastAPI app — disable OpenAPI schema in production to prevent
# unauthenticated API reconnaissance.  Set ENABLE_API_DOCS=true to override.
_enable_docs = os.getenv("ENABLE_API_DOCS", "").lower() in ("1", "true", "yes")
_is_production = system_config.is_production() or system_config.is_production_like()

app = FastAPI(
    title="snflwr.ai API",
    description="K-12 Safe AI Learning Platform Backend",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if (_enable_docs or not _is_production) else None,
    redoc_url="/redoc" if (_enable_docs or not _is_production) else None,
    openapi_url="/openapi.json" if (_enable_docs or not _is_production) else None,
)


# Request Body Size Limit Middleware
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce request body size limits.

    Prevents denial-of-service attacks via oversized request bodies.
    Default limit: 10MB (configurable via MAX_REQUEST_SIZE_MB)
    """

    async def dispatch(self, request, call_next):
        # Check Content-Length header if present
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_SIZE:
                    request_id = get_correlation_id() or "unknown"
                    logger.warning(
                        f"Request body too large: {size} bytes (limit: {MAX_REQUEST_SIZE})",
                        extra={"request_id": request_id},
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request body too large",
                            "max_size_bytes": MAX_REQUEST_SIZE,
                            "received_bytes": size,
                        },
                    )
            except ValueError:
                logger.warning(f"Malformed Content-Length header: {content_length}")
                return JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length header"}
                )
        elif request.method in ("POST", "PUT", "PATCH"):
            # No Content-Length header (e.g. chunked transfer encoding).
            # Read body with size cap to prevent unbounded memory use.
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_REQUEST_SIZE:
                    logger.warning(
                        f"Chunked request body exceeded limit: >{MAX_REQUEST_SIZE} bytes"
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request body too large",
                            "max_size_bytes": MAX_REQUEST_SIZE,
                        },
                    )
            # Re-inject the body so downstream handlers can read it
            request._body = body

        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=system_config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Request-ID",
        "Accept",
    ],
)

# CSRF Protection Middleware
from api.middleware.csrf_protection import validate_csrf_token


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware to validate CSRF tokens on state-changing requests"""

    async def dispatch(self, request, call_next):
        # Validate CSRF token before processing request.
        # BaseHTTPMiddleware does not propagate HTTPException to FastAPI's
        # exception handlers, so we must catch and return a JSONResponse.
        try:
            await validate_csrf_token(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "detail": exc.detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Process request
        response = await call_next(request)
        return response


app.add_middleware(CSRFMiddleware)


# Request Correlation ID Middleware
class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add correlation IDs to all requests for distributed tracing.

    - Generates or propagates X-Request-ID header
    - Stores in context variable for access in handlers and logs
    - Integrates with utils.logger for automatic log correlation
    - Returns correlation ID in response headers
    """

    async def dispatch(self, request, call_next):
        # Get or generate correlation ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in context variable (integrates with logger)
        token = set_correlation_id(request_id)

        # Track active connections for graceful shutdown
        global _active_connections
        async with _connections_lock:
            _active_connections += 1

        try:
            # Process request
            response = await call_next(request)

            # Add correlation ID to response
            response.headers["X-Request-ID"] = request_id

            return response
        finally:
            # Reset context
            correlation_id_var.reset(token)

            # Decrement active connections
            async with _connections_lock:
                _active_connections -= 1


app.add_middleware(CorrelationIDMiddleware)


# Request Timeout Middleware
class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce global request timeouts.

    Prevents runaway requests from consuming resources indefinitely.
    Default timeout: 60 seconds (configurable via REQUEST_TIMEOUT_SECONDS)
    """

    def __init__(self, app, timeout_seconds: float = 60.0):
        super().__init__(app)
        self.timeout = getattr(
            system_config, "REQUEST_TIMEOUT_SECONDS", timeout_seconds
        )

    async def dispatch(self, request, call_next):
        # Skip timeout for WebSocket connections and streaming endpoints
        if request.url.path.startswith("/api/ws"):
            return await call_next(request)

        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except asyncio.TimeoutError:
            request_id = get_correlation_id() or "unknown"
            logger.error(
                f"Request timeout after {self.timeout}s",
                extra={"request_id": request_id, "path": request.url.path},
            )
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "Request timeout",
                    "request_id": request_id,
                    "timeout_seconds": self.timeout,
                },
            )


app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=60.0)


# Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Content-Security-Policy
        # Relax CSP for Swagger/ReDoc docs pages which load JS/CSS from CDN
        path = request.url.path
        if path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "font-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        elif path.startswith("/admin"):
            # Admin dashboard loads Google Fonts
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "img-src 'self' data:; "
                "font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "font-src 'self' data:; "
                "connect-src 'self' wss:; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )

        # X-Content-Type-Options
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options
        response.headers["X-Frame-Options"] = "DENY"

        # X-XSS-Protection (legacy but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer-Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HSTS - Only enable when running behind HTTPS (not on localhost/USB)
        if os.getenv("ENABLE_HSTS", "false").lower() == "true":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Permissions-Policy
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )

        return response


app.add_middleware(SecurityHeadersMiddleware)

# Import routers after app creation to avoid circular imports
from api.routes import (
    chat,
    profiles,
    safety,
    auth,
    analytics,
    admin,
    metrics,
    websocket,
    parental_consent,
    dashboard,
    admin_dashboard,
    thin_client,
    billing,
    health,
    system,
)

# Register routes
app.include_router(admin.router)  # Admin routes have prefix in router definition
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(profiles.router, prefix="/api/profiles", tags=["profiles"])
app.include_router(safety.router, prefix="/api/safety", tags=["safety"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(metrics.router, prefix="/api", tags=["monitoring"])
app.include_router(websocket.router, prefix="/api/ws", tags=["websocket"])
app.include_router(
    parental_consent.router, prefix="/api/parental-consent", tags=["coppa"]
)
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(admin_dashboard.router, tags=["admin-dashboard"])
app.include_router(thin_client.router, prefix="/api/thin-client", tags=["thin-client"])
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])

# Operational endpoints (health/metrics) and system endpoints (root, favicon,
# internal profile lookup, first-run setup) — registered with no prefix so
# their paths are unchanged. Handlers live in api/routes/{health,system}.py.
app.include_router(health.router)
app.include_router(system.router)

# Ollama-compatible proxy — OWU sends requests here instead of directly to Ollama
from api.routes.ollama_proxy import router as ollama_proxy_router

app.include_router(ollama_proxy_router)

# Serve dashboard static assets (JS, CSS)
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles

app.mount(
    "/dashboard/static",
    StaticFiles(directory=str(_Path(__file__).parent / "static" / "dashboard")),
    name="dashboard-static",
)
app.mount(
    "/admin/static",
    StaticFiles(directory=str(_Path(__file__).parent / "static" / "admin")),
    name="admin-static",
)


async def graceful_shutdown(sig: signal.Signals):
    """
    Handle graceful shutdown on SIGTERM/SIGINT.

    Shutdown sequence:
    1. Stop accepting new connections
    2. Wait for in-flight requests to complete (max 30s)
    3. Close all WebSocket connections gracefully
    4. Close database connections
    5. Close Redis connections
    6. Exit
    """
    global _shutdown_event

    logger.info(f"Received shutdown signal: {sig.name}")
    logger.info("=" * 60)
    logger.info("GRACEFUL SHUTDOWN INITIATED")
    logger.info("=" * 60)

    # Set shutdown event
    if _shutdown_event:
        _shutdown_event.set()

    # Step 1: Wait for in-flight requests (max 30 seconds)
    shutdown_timeout = 30.0
    start_time = asyncio.get_event_loop().time()

    logger.info(f"Waiting for {_active_connections} in-flight requests to complete...")

    while _active_connections > 0:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > shutdown_timeout:
            logger.warning(
                f"Shutdown timeout exceeded. {_active_connections} requests still active. "
                "Forcing shutdown."
            )
            break
        await asyncio.sleep(0.1)

    # Step 2: Close WebSocket connections gracefully
    try:
        from api.websocket_server import websocket_manager

        connection_count = websocket_manager.get_active_connections()

        if connection_count > 0:
            logger.info(f"Closing {connection_count} WebSocket connections...")

            # Notify all connected clients about shutdown
            await websocket_manager.broadcast_all(
                {
                    "type": "server_shutdown",
                    "message": "Server is shutting down for maintenance",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            # Give clients a moment to receive the message
            await asyncio.sleep(0.5)

            # Close all connections (with per-connection timeout to avoid stalling shutdown)
            for connections in list(websocket_manager.parent_connections.values()):
                for ws in list(connections):
                    try:
                        await asyncio.wait_for(
                            ws.close(code=1001, reason="Server shutdown"), timeout=2.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

            logger.info("WebSocket connections closed")

        # Stop Redis Pub/Sub listener
        await websocket_manager.stop_pubsub()
        logger.info("WebSocket Pub/Sub stopped")
    except (ConnectionError, OSError, RuntimeError) as e:
        logger.error(f"Connection error closing WebSocket: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error closing WebSocket connections: {e}")

    # Step 3: Close Redis connections
    try:
        from utils.cache import cache

        if cache.enabled and cache._client:
            cache._client.close()
            logger.info("Redis connection closed")
    except (ConnectionError, OSError, RuntimeError) as e:
        logger.error(f"Connection error closing Redis: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error closing Redis: {e}")

    # Step 4: Close database connections
    try:
        from storage.database import db_manager

        if hasattr(db_manager.adapter, "close"):
            db_manager.adapter.close()
        elif hasattr(db_manager.adapter, "disconnect"):
            db_manager.adapter.disconnect()
        logger.info("Database connection closed")
    except DB_ERRORS as e:
        logger.error(f"Database error during shutdown: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error closing database: {e}")

    logger.info("=" * 60)
    logger.info("GRACEFUL SHUTDOWN COMPLETE")
    logger.info("=" * 60)


def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown"""
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(graceful_shutdown(s))
            )
            logger.debug(f"Signal handler registered for {sig.name}")
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(
                sig,
                lambda s, f: asyncio.create_task(graceful_shutdown(signal.Signals(s))),
            )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation errors so 422s are diagnosable in the server log.

    NOTE: Pydantic v2 error entries for `value_error` carry the raw
    `ValueError` instance in `ctx.error`, which is not JSON-serializable.
    Passing `exc.errors()` straight to `JSONResponse` raises
    `TypeError: Object of type ValueError is not JSON serializable`,
    which then propagates to the generic exception handler and turns
    every 422 into a 500 with the bland "An internal error occurred"
    message — hiding the real cause from API clients.

    `jsonable_encoder` recursively coerces non-JSON types (including
    exception instances) to strings, matching what FastAPI's default
    validation handler does. Apply it to BOTH the detail and the whole
    content dict so any future non-serializable field also round-trips.
    """
    errors = exc.errors()
    logger.warning(
        f"422 Unprocessable Entity: {request.method} {request.url.path} — {errors}"
    )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {
                "detail": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """
    Generic exception handler to prevent stack trace leakage.
    Logs the full error internally but returns a safe message to users.
    """
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _needs_first_run_setup() -> bool:
    """Check if this is a first run with no production config."""
    from pathlib import Path

    project_root = Path(__file__).parent.parent
    env_production = project_root / ".env.production"

    # If .env.production exists, setup has already been completed
    if env_production.exists():
        return False

    # In production mode without a config file, setup is needed
    environment = os.getenv("ENVIRONMENT", "development").lower()
    if environment in ("production", "prod", "staging"):
        return True

    # If JWT is still the default placeholder, setup hasn't been done
    jwt = os.getenv("JWT_SECRET_KEY", "")
    if not jwt or jwt == "change-this-secret-key-in-production":
        # Only trigger for production — dev is fine with defaults
        return environment in ("production", "prod", "staging")

    return False


def _run_interactive_setup():
    """Launch the interactive setup script and reload env afterwards."""
    from pathlib import Path
    import subprocess

    setup_script = Path(__file__).parent.parent / "scripts" / "setup_production.py"
    if not setup_script.exists():
        return False

    logger.info("=" * 64)
    logger.info("  Welcome to snflwr.ai!")
    logger.info("  No configuration found -- starting first-time setup...")
    logger.info("=" * 64)

    result = subprocess.run([sys.executable, str(setup_script)])
    if result.returncode != 0:
        return False

    # Reload environment from the newly created .env.production
    env_production = Path(__file__).parent.parent / ".env.production"
    if env_production.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_production, override=True)
        except ImportError:
            pass

    return True


def main():
    """Run the API server — auto-triggers setup on first run."""
    # First-run detection: if no config exists, walk the user through setup
    if _needs_first_run_setup():
        if sys.stdin.isatty():
            success = _run_interactive_setup()
            if not success:
                logger.error("Setup was not completed. Run it manually with:")
                logger.error("    python scripts/setup_production.py")
                sys.exit(1)
        else:
            # Non-interactive (e.g. Docker) — can't prompt, so give clear instructions
            logger.critical(
                "STARTUP BLOCKED: No production configuration found. "
                "Run the setup script first: python scripts/setup_production.py "
                "Or mount an existing .env.production file into the container."
            )
            sys.exit(1)

    uvicorn.run(
        "api.server:app",
        host=system_config.API_HOST,
        port=system_config.API_PORT,
        reload=system_config.API_RELOAD,
        workers=1 if system_config.API_RELOAD else system_config.API_WORKERS,
        log_level=system_config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
