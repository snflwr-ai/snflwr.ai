"""
snflwr.ai API Server
FastAPI backend for safety monitoring and profile management

Composition root: creates and wires ``app``.  Lifecycle/shutdown logic lives
in api/lifecycle.py; re-exports listed in ``__all__`` keep existing callers
(api.routes.system, tests) working without changes.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import __version__
from api.exception_handlers import register_exception_handlers
from api.lifecycle import (  # re-exported for api.routes.system + tests
    check_key_rotation_age,
    check_setup_rate_limit,
    graceful_shutdown,
    lifespan,
    setup_signal_handlers,
)
from api.middleware.correlation import CorrelationIDMiddleware
from api.middleware.csrf import CSRFMiddleware
from api.middleware.request_limits import RequestSizeLimitMiddleware
from api.middleware.security_headers import SecurityHeadersMiddleware
from api.middleware.timeout import RequestTimeoutMiddleware
from config import system_config
from storage.encryption import is_encryption_available
from utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "app",
    "check_setup_rate_limit",
    "graceful_shutdown",
    "check_key_rotation_age",
    "setup_signal_handlers",
    "lifespan",
    "_needs_first_run_setup",
]


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

app.add_middleware(CSRFMiddleware)


app.add_middleware(CorrelationIDMiddleware)


app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=60.0)


app.add_middleware(SecurityHeadersMiddleware)

# Import routers after app creation to avoid circular imports
from api.routes import (
    admin,
    admin_dashboard,
    analytics,
    auth,
    billing,
    chat,
    dashboard,
    health,
    metrics,
    parental_consent,
    profiles,
    safety,
    system,
    thin_client,
    websocket,
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


register_exception_handlers(app)


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
    import subprocess
    from pathlib import Path

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
