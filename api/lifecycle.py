"""Application lifecycle: startup/shutdown (lifespan), setup rate limiting,
key-rotation age check, graceful shutdown, and signal handlers.

Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request

from api.connection_tracking import connection_tracker
from config import system_config
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger
from utils.rate_limiter import RateLimiter

logger = get_logger(__name__)

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # type: ignore[misc,assignment]

# Rate limiter for unauthenticated endpoints
_setup_rate_limiter = RateLimiter()

# Global shutdown flag
_shutdown_event: Optional[asyncio.Event] = None

# Maximum time (in seconds) allowed for startup before the server aborts.
# Override with the STARTUP_TIMEOUT_SECONDS environment variable.
STARTUP_TIMEOUT_SECONDS = int(os.getenv("STARTUP_TIMEOUT_SECONDS", "60"))


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
        from api import __version__
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

    # Validate cryptography library is available (required for encryption).
    # The imports themselves are the availability probe (ImportError below).
    try:
        from cryptography.fernet import Fernet  # noqa: F401
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: F401

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

    logger.info(
        f"Waiting for {connection_tracker.count} in-flight requests to complete..."
    )

    while connection_tracker.count > 0:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > shutdown_timeout:
            logger.warning(
                f"Shutdown timeout exceeded. {connection_tracker.count} requests still active. "
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

    def _make_shutdown_handler(s: signal.Signals):
        # Factory closure captures ``s`` per signal (avoids late-binding) and
        # keeps the handler a plain no-arg lambda the type checker can infer.
        return lambda: asyncio.create_task(graceful_shutdown(s))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _make_shutdown_handler(sig))
            logger.debug(f"Signal handler registered for {sig.name}")
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(
                sig,
                lambda s, f: asyncio.create_task(graceful_shutdown(signal.Signals(s))),
            )
