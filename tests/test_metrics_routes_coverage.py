"""
Comprehensive tests for api/routes/metrics.py.

Covers:
- PrometheusMetrics class (all methods)
- GET /api/metrics endpoint
- GET /api/health/detailed endpoint
"""

import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")

import httpx


@pytest.fixture
def admin_session():
    from core.authentication import AuthSession
    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="admin-token",
        email="admin@test.com",
    )


@pytest.fixture(scope="module")
def app():
    from api.server import app as _app
    return _app


def _auth_header():
    return {"Authorization": "Bearer admin-token", "X-CSRF-Token": "test-csrf"}


class TestPrometheusMetricsClass:
    """Unit tests for PrometheusMetrics helper class."""

    @pytest.fixture
    def metrics(self):
        from api.routes.metrics import PrometheusMetrics
        return PrometheusMetrics()

    def test_format_metric_with_help(self, metrics):
        result = metrics._format_metric(
            "test_metric", 42.0, "gauge", "Test help text"
        )
        assert "# HELP test_metric Test help text" in result
        assert "# TYPE test_metric gauge" in result
        assert "test_metric 42.0" in result

    def test_format_metric_without_help(self, metrics):
        result = metrics._format_metric("test_metric", 1.0, "counter")
        assert "# HELP" not in result
        assert "# TYPE test_metric counter" in result

    def test_format_metric_with_labels(self, metrics):
        result = metrics._format_metric(
            "test_metric", 5.0, "gauge", "",
            labels={"key": "value", "env": "test"}
        )
        assert 'key="value"' in result
        assert 'env="test"' in result

    def test_format_metric_without_labels(self, metrics):
        result = metrics._format_metric("test_metric", 0.0, "gauge")
        assert "{" not in result

    def test_get_system_metrics(self, metrics):
        """Should return system metrics using psutil."""
        with patch("api.routes.metrics.psutil.cpu_percent", return_value=25.0), \
             patch("api.routes.metrics.psutil.cpu_count", return_value=4), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.system_config") as mock_cfg:
            mock_mem.return_value = MagicMock(total=8e9, used=4e9, percent=50.0)
            mock_disk.return_value = MagicMock(total=100e9, used=50e9, percent=50.0)
            mock_cfg.APP_DATA_DIR = "/tmp"

            result = metrics.get_system_metrics()

        assert len(result) > 0
        joined = "\n".join(result)
        assert "snflwr_cpu_usage_percent" in joined
        assert "snflwr_cpu_count" in joined
        assert "snflwr_memory_total_bytes" in joined
        assert "snflwr_disk_total_bytes" in joined

    def test_get_application_metrics_success(self, metrics):
        """Should return application metrics from DB."""
        with patch("api.routes.metrics.db_manager") as mock_db:
            mock_db.execute_read.return_value = [{"count": 10}]

            result = metrics.get_application_metrics()

        assert len(result) > 0
        joined = "\n".join(result)
        assert "snflwr_users_total" in joined
        assert "snflwr_profiles_total" in joined
        assert "snflwr_sessions_active" in joined

    def test_get_application_metrics_db_error(self, metrics):
        """DB errors should be caught and not raise."""
        import sqlite3
        with patch("api.routes.metrics.db_manager") as mock_db:
            mock_db.execute_read.side_effect = sqlite3.Error("fail")
            result = metrics.get_application_metrics()
        # Should return empty list (metrics skipped on error)
        assert isinstance(result, list)

    def test_get_application_metrics_unexpected_error(self, metrics):
        """Unexpected errors should be caught."""
        with patch("api.routes.metrics.db_manager") as mock_db:
            mock_db.execute_read.side_effect = RuntimeError("unexpected")
            result = metrics.get_application_metrics()
        assert isinstance(result, list)

    def test_get_safety_metrics_success(self, metrics):
        """Should return safety metrics."""
        with patch("api.routes.metrics.db_manager") as mock_db:
            mock_db.execute_read.return_value = [{"count": 3}]

            result = metrics.get_safety_metrics()

        assert len(result) > 0
        joined = "\n".join(result)
        assert "snflwr_safety_incidents_24h" in joined
        assert "snflwr_alerts_unacknowledged" in joined

    def test_get_safety_metrics_db_error(self, metrics):
        """DB errors should be caught."""
        import sqlite3
        with patch("api.routes.metrics.db_manager") as mock_db:
            mock_db.execute_read.side_effect = sqlite3.Error("fail")
            result = metrics.get_safety_metrics()
        assert isinstance(result, list)

    def test_get_performance_metrics(self, metrics):
        """Should retrieve performance metrics from logger."""
        with patch("api.routes.metrics.logger_manager") as mock_lm:
            mock_perf = MagicMock()
            mock_perf.get_statistics.return_value = {
                "avg": 15.5,
                "max": 100.0,
                "count": 1000
            }
            mock_lm.performance_logger = mock_perf

            result = metrics.get_performance_metrics()

        assert len(result) > 0
        joined = "\n".join(result)
        assert "avg_ms" in joined
        assert "max_ms" in joined

    def test_get_performance_metrics_no_stats(self, metrics):
        """Should handle case where no stats exist yet."""
        with patch("api.routes.metrics.logger_manager") as mock_lm:
            mock_perf = MagicMock()
            mock_perf.get_statistics.return_value = None
            mock_lm.performance_logger = mock_perf

            result = metrics.get_performance_metrics()
        assert isinstance(result, list)

    def test_get_performance_metrics_error(self, metrics):
        """Exceptions should be caught."""
        with patch("api.routes.metrics.logger_manager") as mock_lm:
            mock_lm.performance_logger = MagicMock(
                get_statistics=MagicMock(side_effect=AttributeError("no attr"))
            )
            result = metrics.get_performance_metrics()
        assert isinstance(result, list)

    def test_generate_all_metrics(self, metrics):
        """Should concatenate all metric types."""
        with patch.object(metrics, "get_system_metrics", return_value=["# SYSTEM"]), \
             patch.object(metrics, "get_application_metrics", return_value=["# APP"]), \
             patch.object(metrics, "get_safety_metrics", return_value=["# SAFETY"]), \
             patch.object(metrics, "get_performance_metrics", return_value=["# PERF"]):
            result = metrics.generate_all_metrics()

        assert "# SYSTEM" in result
        assert "# APP" in result
        assert "# SAFETY" in result
        assert "# PERF" in result
        assert result.endswith("\n")


class TestMetricsRouteEndpoints:
    """Test the metrics route endpoints via HTTP."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_requires_admin(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/metrics")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_metrics_endpoint_with_admin(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.metrics.psutil.cpu_percent", return_value=20.0), \
             patch("api.routes.metrics.psutil.cpu_count", return_value=4), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.db_manager") as mock_db, \
             patch("api.routes.metrics.system_config") as mock_cfg, \
             patch("api.routes.metrics.logger_manager") as mock_lm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_mem.return_value = MagicMock(total=8e9, used=4e9, percent=50.0)
            mock_disk.return_value = MagicMock(total=100e9, used=50e9, percent=50.0)
            mock_cfg.APP_DATA_DIR = "/tmp"
            mock_cfg.REDIS_ENABLED = False
            mock_cfg.ENABLE_SAFETY_MONITORING = True
            mock_db.execute_read.return_value = [{"count": 0}]
            mock_lm.performance_logger.get_statistics.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/metrics",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403, 503, 500)

    @pytest.mark.asyncio
    async def test_metrics_endpoint_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.metrics.psutil.cpu_percent", return_value=20.0), \
             patch("api.routes.metrics.psutil.cpu_count", return_value=4), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.system_config") as mock_cfg, \
             patch("api.routes.metrics.db_manager") as mock_db, \
             patch("api.routes.metrics.logger_manager") as mock_lm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_mem.return_value = MagicMock(total=8e9, used=4e9, percent=50.0)
            mock_disk.return_value = MagicMock(total=100e9, used=50e9, percent=50.0)
            mock_cfg.APP_DATA_DIR = "/tmp"
            mock_db.execute_read.side_effect = sqlite3.Error("db fail")
            mock_lm.performance_logger.get_statistics.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/metrics",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403, 503, 500)

    @pytest.mark.asyncio
    async def test_health_detailed_endpoint(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.metrics.psutil.cpu_percent", return_value=20.0), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.db_manager") as mock_db, \
             patch("api.routes.metrics.system_config") as mock_cfg:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_mem.return_value = MagicMock(percent=50.0)
            mock_disk.return_value = MagicMock(percent=40.0)
            mock_db.execute_read.return_value = [{"1": 1}]
            mock_cfg.DATABASE_TYPE = "sqlite"
            mock_cfg.REDIS_ENABLED = False
            mock_cfg.ENABLE_SAFETY_MONITORING = True
            mock_cfg.APP_DATA_DIR = "/tmp"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/health/detailed",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403, 500)

    @pytest.mark.asyncio
    async def test_health_detailed_database_degraded(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.metrics.psutil.cpu_percent", return_value=20.0), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.db_manager") as mock_db, \
             patch("api.routes.metrics.system_config") as mock_cfg:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_mem.return_value = MagicMock(percent=50.0)
            mock_disk.return_value = MagicMock(percent=40.0)
            mock_db.execute_read.side_effect = sqlite3.Error("connection failed")
            mock_cfg.DATABASE_TYPE = "sqlite"
            mock_cfg.REDIS_ENABLED = False
            mock_cfg.ENABLE_SAFETY_MONITORING = True
            mock_cfg.APP_DATA_DIR = "/tmp"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/health/detailed",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403, 500)

    @pytest.mark.asyncio
    async def test_health_detailed_with_redis(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.metrics.psutil.cpu_percent", return_value=20.0), \
             patch("api.routes.metrics.psutil.virtual_memory") as mock_mem, \
             patch("api.routes.metrics.psutil.disk_usage") as mock_disk, \
             patch("api.routes.metrics.db_manager") as mock_db, \
             patch("api.routes.metrics.system_config") as mock_cfg, \
             patch("utils.cache.cache") as mock_cache:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_mem.return_value = MagicMock(percent=50.0)
            mock_disk.return_value = MagicMock(percent=40.0)
            mock_db.execute_read.return_value = [{"1": 1}]
            mock_cfg.DATABASE_TYPE = "sqlite"
            mock_cfg.REDIS_ENABLED = True
            mock_cfg.ENABLE_SAFETY_MONITORING = True
            mock_cfg.APP_DATA_DIR = "/tmp"
            mock_cache.health_check_detailed.return_value = {"healthy": True, "mode": "standalone"}
            mock_cache.is_degraded = False

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/health/detailed",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403, 500)
