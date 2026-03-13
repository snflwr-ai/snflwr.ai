"""
Prometheus Metrics Endpoint
Exposes application metrics in Prometheus format for monitoring
"""

from fastapi import APIRouter, Response, Depends
from core.authentication import AuthSession
from api.middleware.auth import require_admin
from datetime import datetime, timezone
import psutil
import time
from typing import Any, Dict, List

from utils.logger import get_logger, logger_manager, sanitize_log_value
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from config import system_config

logger = get_logger(__name__)

router = APIRouter()


class PrometheusMetrics:
    """Generate Prometheus-formatted metrics"""

    def __init__(self):
        self.start_time = time.time()

    def _format_metric(
        self,
        name: str,
        value: float,
        metric_type: str = "gauge",
        help_text: str = "",
        labels: Dict[str, str] = None,
    ) -> str:
        """
        Format a single metric in Prometheus format

        Args:
            name: Metric name
            value: Metric value
            metric_type: Type (counter, gauge, histogram, summary)
            help_text: Help description
            labels: Optional labels dict

        Returns:
            Prometheus-formatted metric string
        """
        lines = []

        # Add TYPE and HELP
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")

        # Format labels
        if labels:
            label_str = ",".join([f'{k}="{v}"' for k, v in labels.items()])
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")

        return "\n".join(lines)

    def get_system_metrics(self) -> List[str]:
        """Get system-level metrics (CPU, memory, disk)"""
        metrics = []

        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        metrics.append(
            self._format_metric(
                "snflwr_cpu_usage_percent", cpu_percent, "gauge", "CPU usage percentage"
            )
        )

        cpu_count = psutil.cpu_count()
        metrics.append(
            self._format_metric(
                "snflwr_cpu_count", cpu_count, "gauge", "Number of CPU cores"
            )
        )

        # Memory metrics
        memory = psutil.virtual_memory()
        metrics.append(
            self._format_metric(
                "snflwr_memory_total_bytes",
                memory.total,
                "gauge",
                "Total system memory in bytes",
            )
        )

        metrics.append(
            self._format_metric(
                "snflwr_memory_used_bytes",
                memory.used,
                "gauge",
                "Used system memory in bytes",
            )
        )

        metrics.append(
            self._format_metric(
                "snflwr_memory_usage_percent",
                memory.percent,
                "gauge",
                "Memory usage percentage",
            )
        )

        # Disk metrics
        disk = psutil.disk_usage(str(system_config.APP_DATA_DIR))
        metrics.append(
            self._format_metric(
                "snflwr_disk_total_bytes",
                disk.total,
                "gauge",
                "Total disk space in bytes",
            )
        )

        metrics.append(
            self._format_metric(
                "snflwr_disk_used_bytes", disk.used, "gauge", "Used disk space in bytes"
            )
        )

        metrics.append(
            self._format_metric(
                "snflwr_disk_usage_percent",
                disk.percent,
                "gauge",
                "Disk usage percentage",
            )
        )

        return metrics

    def get_application_metrics(self) -> List[str]:
        """Get application-level metrics"""
        metrics = []

        # Uptime
        uptime_seconds = time.time() - self.start_time
        metrics.append(
            self._format_metric(
                "snflwr_uptime_seconds",
                uptime_seconds,
                "counter",
                "Application uptime in seconds",
            )
        )

        # Database metrics
        try:
            # Count users
            users = db_manager.execute_read("SELECT COUNT(*) as count FROM accounts")
            user_count = users[0]["count"] if users else 0

            metrics.append(
                self._format_metric(
                    "snflwr_users_total", user_count, "gauge", "Total number of users"
                )
            )

            # Count active users
            active_users = db_manager.execute_read(
                "SELECT COUNT(*) as count FROM accounts WHERE is_active = 1"
            )
            active_user_count = active_users[0]["count"] if active_users else 0

            metrics.append(
                self._format_metric(
                    "snflwr_users_active",
                    active_user_count,
                    "gauge",
                    "Number of active users",
                )
            )

            # Count child profiles
            profiles = db_manager.execute_read(
                "SELECT COUNT(*) as count FROM child_profiles"
            )
            profile_count = profiles[0]["count"] if profiles else 0

            metrics.append(
                self._format_metric(
                    "snflwr_profiles_total",
                    profile_count,
                    "gauge",
                    "Total number of child profiles",
                )
            )

            # Count active profiles
            active_profiles = db_manager.execute_read(
                "SELECT COUNT(*) as count FROM child_profiles WHERE is_active = 1"
            )
            active_profile_count = active_profiles[0]["count"] if active_profiles else 0

            metrics.append(
                self._format_metric(
                    "snflwr_profiles_active",
                    active_profile_count,
                    "gauge",
                    "Number of active child profiles",
                )
            )

            # Count active sessions
            active_sessions = db_manager.execute_read(
                "SELECT COUNT(*) as count FROM sessions WHERE ended_at IS NULL"
            )
            session_count = active_sessions[0]["count"] if active_sessions else 0

            metrics.append(
                self._format_metric(
                    "snflwr_sessions_active",
                    session_count,
                    "gauge",
                    "Number of active conversation sessions",
                )
            )

            # Count messages (last 24 hours)
            messages_24h = db_manager.execute_read(
                """
                SELECT COUNT(*) as count FROM messages
                WHERE timestamp > datetime('now', '-24 hours')
                """
            )
            message_count = messages_24h[0]["count"] if messages_24h else 0

            metrics.append(
                self._format_metric(
                    "snflwr_messages_24h",
                    message_count,
                    "gauge",
                    "Number of messages in last 24 hours",
                )
            )

        except DB_ERRORS as e:
            logger.error(f"Database error collecting application metrics: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error collecting application metrics: {e}")

        return metrics

    def get_safety_metrics(self) -> List[str]:
        """Get safety monitoring metrics"""
        metrics = []

        try:
            # Count safety incidents (last 24 hours)
            incidents_24h = db_manager.execute_read(
                """
                SELECT COUNT(*) as count FROM safety_incidents
                WHERE timestamp > datetime('now', '-24 hours')
                """
            )
            incident_count = incidents_24h[0]["count"] if incidents_24h else 0

            metrics.append(
                self._format_metric(
                    "snflwr_safety_incidents_24h",
                    incident_count,
                    "gauge",
                    "Number of safety incidents in last 24 hours",
                )
            )

            # Count by severity (last 24 hours)
            for severity in ["minor", "major", "critical"]:
                severity_incidents = db_manager.execute_read(
                    """
                    SELECT COUNT(*) as count FROM safety_incidents
                    WHERE timestamp > datetime('now', '-24 hours')
                    AND severity = ?
                    """,
                    (severity,),
                )
                count = severity_incidents[0]["count"] if severity_incidents else 0

                metrics.append(
                    self._format_metric(
                        "snflwr_safety_incidents_by_severity",
                        count,
                        "gauge",
                        "Safety incidents by severity level",
                        labels={"severity": severity},
                    )
                )

            # Count unacknowledged alerts
            unack_alerts = db_manager.execute_read(
                "SELECT COUNT(*) as count FROM parent_alerts WHERE acknowledged = 0"
            )
            alert_count = unack_alerts[0]["count"] if unack_alerts else 0

            metrics.append(
                self._format_metric(
                    "snflwr_alerts_unacknowledged",
                    alert_count,
                    "gauge",
                    "Number of unacknowledged parent alerts",
                )
            )

        except DB_ERRORS as e:
            logger.error(f"Database error collecting safety metrics: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error collecting safety metrics: {e}")

        return metrics

    def get_performance_metrics(self) -> List[str]:
        """Get performance metrics from logger"""
        metrics = []

        try:
            # Get statistics from performance logger
            perf_logger = logger_manager.performance_logger

            # Common metrics to track
            metric_names = [
                "model_response_time",
                "safety_filter_time",
                "database_query_time",
                "api_response_time",
            ]

            for metric_name in metric_names:
                stats = perf_logger.get_statistics(metric_name)
                if stats:
                    # Average
                    metrics.append(
                        self._format_metric(
                            f"snflwr_performance_{metric_name}_avg_ms",
                            stats["avg"],
                            "gauge",
                            f"Average {metric_name} in milliseconds",
                        )
                    )

                    # Max
                    metrics.append(
                        self._format_metric(
                            f"snflwr_performance_{metric_name}_max_ms",
                            stats["max"],
                            "gauge",
                            f"Maximum {metric_name} in milliseconds",
                        )
                    )

                    # Count
                    metrics.append(
                        self._format_metric(
                            f"snflwr_performance_{metric_name}_count",
                            stats["count"],
                            "counter",
                            f"Total {metric_name} measurements",
                        )
                    )

        except Exception as e:
            logger.exception(f"Unexpected error collecting performance metrics: {e}")

        return metrics

    def generate_all_metrics(self) -> str:
        """Generate all metrics in Prometheus format"""
        all_metrics = []

        # Collect all metric types
        all_metrics.extend(self.get_system_metrics())
        all_metrics.extend(self.get_application_metrics())
        all_metrics.extend(self.get_safety_metrics())
        all_metrics.extend(self.get_performance_metrics())

        # Join with double newlines
        return "\n\n".join(all_metrics) + "\n"


# Global metrics instance
_metrics = PrometheusMetrics()


@router.get("/metrics")
async def metrics_endpoint(session: AuthSession = Depends(require_admin)):
    """
    Prometheus metrics endpoint

    Returns metrics in Prometheus text format for scraping

    Example:
        curl http://localhost:8000/api/metrics
    """
    try:
        metrics_text = _metrics.generate_all_metrics()

        return Response(content=metrics_text, media_type="text/plain; version=0.0.4")

    except DB_ERRORS as e:
        logger.error(f"Database error generating metrics: {e}")
        return Response(
            content="# Database error generating metrics\n",
            media_type="text/plain",
            status_code=503,
        )
    except Exception as e:
        logger.exception(f"Unexpected error generating metrics: {e}")
        return Response(
            content="# Error generating metrics\n",
            media_type="text/plain",
            status_code=500,
        )


@router.get("/health/detailed")
async def detailed_health(session: AuthSession = Depends(require_admin)):
    """
    Detailed health check with component status

    Returns JSON with health status of all components
    """
    health_status: Dict[str, Any] = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {},
    }

    # Database health
    try:
        db_manager.execute_read("SELECT 1")
        health_status["components"]["database"] = {
            "status": "healthy",
            "type": system_config.DATABASE_TYPE,
        }
    except DB_ERRORS as e:
        logger.error(f"Database health check failed: {e}")
        health_status["components"]["database"] = {
            "status": "unhealthy",
            "error": "Database connection failed",
        }
        health_status["status"] = "degraded"
    except Exception as e:
        logger.exception(f"Unexpected error in database health check: {e}")
        health_status["components"]["database"] = {
            "status": "unhealthy",
            "error": "Database check failed",
        }
        health_status["status"] = "degraded"

    # System resources
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage(str(system_config.APP_DATA_DIR)).percent

    health_status["components"]["system"] = {
        "status": "healthy" if cpu_percent < 90 and memory_percent < 90 else "degraded",
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "disk_percent": disk_percent,
    }

    # Redis health
    try:
        from utils.cache import cache

        if system_config.REDIS_ENABLED:
            redis_detail = cache.health_check_detailed()
            redis_status = (
                "healthy"
                if redis_detail.get("healthy")
                else ("degraded" if cache.is_degraded else "unhealthy")
            )
            health_status["components"]["redis"] = {
                "status": redis_status,
                "mode": redis_detail.get("mode", "unknown"),
                "degraded": cache.is_degraded,
            }
            if redis_detail.get("error"):
                health_status["components"]["redis"]["error"] = redis_detail["error"]
            if redis_status != "healthy":
                health_status["status"] = "degraded"
        else:
            health_status["components"]["redis"] = {
                "status": "disabled",
            }
    except Exception as e:
        logger.warning(f"Could not check Redis health: {e}")
        health_status["components"]["redis"] = {
            "status": "unknown",
            "error": sanitize_log_value(str(e)),
        }

    # Safety monitoring
    health_status["components"]["safety_monitoring"] = {
        "status": "enabled" if system_config.ENABLE_SAFETY_MONITORING else "disabled"
    }

    return health_status


# Export router
__all__ = ["router"]
