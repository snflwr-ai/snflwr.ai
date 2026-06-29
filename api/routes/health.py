"""Health, readiness, liveness and Prometheus metrics endpoints.

Extracted verbatim from ``api/server.py`` (behavior-preserving refactor).
These are app-level operational endpoints registered on the app with no
prefix, so the paths are unchanged:

    GET /health
    GET /health/detailed   (admin-only)
    GET /health/ready
    GET /health/live
    GET /metrics           (Prometheus, Bearer-token gated)

All heavyweight dependencies (database, Redis, Celery, Ollama, metrics) are
imported lazily inside the handlers exactly as before, so importing this
module stays cheap and free of import cycles.
"""

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.middleware.auth import require_admin
from config import system_config
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["monitoring"])


@router.get("/health")
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    from config import system_config as _cfg

    # Determine rate limiter backend
    if _cfg.REDIS_ENABLED:
        rate_backend = "redis"
    else:
        rate_backend = "sqlite"  # SQLite fallback is default for home mode

    # Determine classifier state
    classifier_state = "disabled"
    classifier_since = None
    try:
        from safety.pipeline import safety_pipeline

        if hasattr(safety_pipeline, "_classifier"):
            clf = safety_pipeline._classifier
            classifier_state = getattr(clf, "_state", "disabled")
            _since = getattr(clf, "_state_since", None)
            if _since:
                classifier_since = _since.isoformat()
    except Exception:
        pass

    return {
        "status": "healthy",
        "rate_limiter": rate_backend,
        "rate_limiter_healthy": True,
        "safety_classifier": classifier_state,
        "safety_classifier_since": classifier_since,
    }


@router.get("/health/detailed")
async def health_check_detailed(session=Depends(require_admin)):
    """
    Comprehensive health check for all dependencies.
    Returns detailed status of database, Redis, Celery, and Ollama.
    Use this for monitoring dashboards and alerting.
    """
    health: Dict[str, Any] = {"status": "healthy", "checks": {}}
    unhealthy_count = 0

    # Database check
    try:
        from storage.database import db_manager

        db_manager.adapter.connect()
        # Try a simple query (the call is the health probe; result unused)
        db_manager.adapter.execute_query("SELECT 1")
        health["checks"]["database"] = {
            "status": "healthy",
            "type": system_config.DATABASE_TYPE,
            "message": "Connection successful",
        }
    except DB_ERRORS as e:
        logger.error(f"Database health check failed: {e}")
        health["checks"]["database"] = {
            "status": "unhealthy",
            "type": system_config.DATABASE_TYPE,
            "error": "Database connection failed",
        }
        unhealthy_count += 1
    except Exception as e:
        logger.exception(f"Unexpected error in database health check: {e}")
        health["checks"]["database"] = {
            "status": "unhealthy",
            "type": system_config.DATABASE_TYPE,
            "error": "Database check failed",
        }
        unhealthy_count += 1

    # Redis check (with Sentinel support)
    try:
        from utils.cache import cache

        if cache.enabled and cache._client:
            detailed_health = cache.health_check_detailed()
            if detailed_health.get("healthy"):
                health["checks"]["redis"] = {
                    "status": "healthy",
                    "mode": detailed_health.get("mode", "standalone"),
                    "hit_rate": f"{cache.get_stats().get('hit_rate', 0):.1f}%",
                }
                # Add Sentinel info if available
                if detailed_health.get("mode") == "sentinel":
                    health["checks"]["redis"]["slave_count"] = detailed_health.get(
                        "slave_count", 0
                    )
                    health["checks"]["redis"]["sentinel_nodes"] = detailed_health.get(
                        "sentinel_nodes", 0
                    )
            else:
                health["checks"]["redis"] = {
                    "status": "unhealthy",
                    "mode": detailed_health.get("mode", "standalone"),
                    "error": "Redis connection failed",
                }
                unhealthy_count += 1
        else:
            health["checks"]["redis"] = {
                "status": "disabled",
                "message": "Redis caching is disabled",
            }
    except Exception as e:
        logger.exception(f"Unexpected error in Redis health check: {e}")
        health["checks"]["redis"] = {
            "status": "unhealthy",
            "error": "Redis check failed",
        }
        unhealthy_count += 1

    # Celery check
    try:
        from utils.celery_config import check_celery_health

        celery_health = check_celery_health()
        if celery_health.get("healthy"):
            health["checks"]["celery"] = {
                "status": "healthy",
                "worker_count": celery_health.get("worker_count", 0),
            }
        else:
            health["checks"]["celery"] = {
                "status": "unhealthy",
                "error": "No workers responding",
            }
            # Celery being down is a warning, not critical
    except Exception as e:
        logger.exception(f"Unexpected error in Celery health check: {e}")
        health["checks"]["celery"] = {
            "status": "unknown",
            "error": "Celery check failed",
        }

    # Ollama check with circuit breaker status
    try:
        from utils.circuit_breaker import ollama_circuit

        circuit_stats = ollama_circuit.get_stats()
        circuit_state = circuit_stats.get("state", "unknown")

        # If circuit is open, don't bother checking Ollama - it's known to be down
        if circuit_state == "open":
            health["checks"]["ollama"] = {
                "status": "circuit_open",
                "circuit_state": circuit_state,
                "message": "Circuit breaker is OPEN",
            }
            unhealthy_count += 1
        else:
            import httpx

            ollama_url = system_config.OLLAMA_HOST
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{ollama_url}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    health["checks"]["ollama"] = {
                        "status": "healthy",
                        "models_loaded": len(models),
                        "circuit_state": circuit_state,
                    }
                else:
                    health["checks"]["ollama"] = {
                        "status": "degraded",
                        "circuit_state": circuit_state,
                    }
    except (ConnectionError, OSError) as e:
        logger.error(f"Ollama connection error during health check: {e}")
        health["checks"]["ollama"] = {
            "status": "unhealthy",
            "error": "Ollama connection failed",
        }
        unhealthy_count += 1
    except Exception as e:
        logger.exception(f"Unexpected error in Ollama health check: {e}")
        health["checks"]["ollama"] = {
            "status": "unhealthy",
            "error": "Ollama check failed",
        }
        unhealthy_count += 1

    # Safety monitoring status
    health["checks"]["safety_monitoring"] = {
        "status": "enabled" if system_config.ENABLE_SAFETY_MONITORING else "disabled"
    }

    # Overall status
    if unhealthy_count > 0:
        health["status"] = "degraded" if unhealthy_count < 2 else "unhealthy"

    return health


@router.get("/health/ready")
async def readiness_check():
    """
    Kubernetes readiness probe.
    Returns 200 only if the service can accept traffic.
    """
    try:
        # Check database is accessible
        from storage.database import db_manager

        db_manager.adapter.connect()
        db_manager.adapter.execute_query("SELECT 1")

        return {"status": "ready"}
    except DB_ERRORS as e:
        logger.error(f"Database not ready: {e}")
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    except Exception as e:
        logger.exception(f"Unexpected error in readiness check: {e}")
        return JSONResponse(status_code=503, content={"status": "not_ready"})


@router.get("/health/live")
async def liveness_check():
    """
    Kubernetes liveness probe.
    Returns 200 if the process is alive (basic check).
    """
    return {"status": "alive"}


@router.get("/metrics")
async def prometheus_metrics(request: Request):
    """
    Prometheus metrics endpoint.
    Exposes application metrics in Prometheus format for scraping.

    Authentication:
        Set PROMETHEUS_METRICS_TOKEN env var to require Bearer token auth.
        Unauthenticated in dev when env var is unset.

    Usage:
        Configure Prometheus to scrape this endpoint:
        ```yaml
        scrape_configs:
          - job_name: 'snflwr-ai'
            static_configs:
              - targets: ['localhost:8000']
            metrics_path: '/metrics'
            authorization:
              credentials: '<PROMETHEUS_METRICS_TOKEN value>'
        ```
    """
    # Bearer token auth for Prometheus scraper
    import hmac as _hmac_metrics

    metrics_token = os.getenv("PROMETHEUS_METRICS_TOKEN")
    if not metrics_token:
        raise HTTPException(
            status_code=503,
            detail="Metrics endpoint disabled: set PROMETHEUS_METRICS_TOKEN to enable",
        )
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {metrics_token}"
    if not _hmac_metrics.compare_digest(auth_header, expected):
        raise HTTPException(status_code=401, detail="Invalid metrics token")

    from fastapi.responses import Response

    try:
        from utils.metrics import get_content_type, get_metrics

        return Response(content=get_metrics(), media_type=get_content_type())
    except ImportError:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Prometheus metrics not available. Install prometheus_client package."
            },
        )
