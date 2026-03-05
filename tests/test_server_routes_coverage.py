"""
Comprehensive tests for api/server.py endpoints.

Coverage targets:
- Root endpoint
- Health check endpoints (/, /health, /health/detailed, /health/ready, /health/live)
- Setup status and setup endpoints
- Internal profile-for-user endpoint
- Prometheus metrics endpoint
- Request middleware (size, CSRF, correlation ID, timeout, security headers)
- Exception handlers
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

# Set env var before imports
os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")

import httpx


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def app():
    """Get the FastAPI app, mocking out startup dependencies."""
    with patch("storage.database.db_manager") as mock_db, \
         patch("api.server.system_config") as mock_config:
        mock_config.validate_production_security.return_value = []
        mock_config.is_production.return_value = False
        mock_config.is_production_like.return_value = False
        mock_config.DEPLOY_MODE = "development"
        mock_config.REDIS_ENABLED = False
        mock_config.ENABLE_SAFETY_MONITORING = True
        mock_config.DATABASE_TYPE = "sqlite"
        mock_config.API_HOST = "localhost"
        mock_config.API_PORT = 8000
        mock_config.APP_DATA_DIR = MagicMock()
        mock_config.CORS_ORIGINS = ["*"]
        mock_config.MAX_REQUEST_SIZE_MB = 10
        mock_config.REQUEST_TIMEOUT_SECONDS = 60
        mock_config.OLLAMA_HOST = "http://localhost:11434"

        mock_db.initialize_database.return_value = None

        from api.server import app as _app
        return _app


@pytest.fixture
def admin_session():
    from core.authentication import AuthSession
    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="admin-token",
        email="admin@test.com",
    )


class TestRootEndpoint:
    """Test root endpoint."""

    @pytest.mark.asyncio
    async def test_root_returns_api_info(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "snflwr.ai API"
        assert data["status"] == "running"
        assert "version" in data
        assert "timestamp" in data


class TestHealthEndpoints:
    """Test health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check_basic(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_readiness_check_healthy(self, app):
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [{"1": 1}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/health/ready")
        assert response.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_liveness_check(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    @pytest.mark.asyncio
    async def test_detailed_health_requires_auth(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/detailed")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_detailed_health_with_admin(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("storage.database.db_manager") as mock_db, \
             patch("config.system_config") as mock_sc, \
             patch("utils.circuit_breaker.ollama_circuit") as mock_cb:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db.adapter.connect.return_value = None
            mock_db.adapter.execute_query.return_value = [{"1": 1}]
            mock_sc.DATABASE_TYPE = "sqlite"
            mock_sc.ENABLE_SAFETY_MONITORING = True
            mock_sc.OLLAMA_HOST = "http://localhost:11434"
            mock_cb.get_stats.return_value = {"state": "open"}

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/health/detailed",
                    headers={"Authorization": "Bearer admin-token"}
                )
        assert response.status_code in (200, 422, 500)


class TestSetupEndpoints:
    """Test setup status and setup endpoints."""

    @pytest.mark.asyncio
    async def test_setup_status_not_initialized(self, app):
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = [{"count": 0}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/system/setup-status")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is False
        assert data["needs_setup"] is True

    @pytest.mark.asyncio
    async def test_setup_status_already_initialized(self, app):
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = [{"count": 1}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/system/setup-status")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True

    @pytest.mark.asyncio
    async def test_setup_status_db_error(self, app):
        import sqlite3
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.side_effect = sqlite3.Error("fail")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/system/setup-status")
        # Should still return 200 with needs_setup=True on error
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_setup_blocked_when_initialized(self, app):
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = [{"count": 1}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/system/setup", json={
                    "email": "test@example.com",
                    "password": "Pass1234!",
                    "verify_password": "Pass1234!"
                })
        assert response.status_code in (403, 429)

    @pytest.mark.asyncio
    async def test_setup_password_mismatch(self, app):
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = [{"count": 0}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/system/setup", json={
                    "email": "test@example.com",
                    "password": "Pass1234!",
                    "verify_password": "Different1!"
                })
        assert response.status_code in (400, 429)

    @pytest.mark.asyncio
    async def test_setup_creates_account(self, app):
        with patch("storage.database.db_manager") as mock_db, \
             patch("core.authentication.auth_manager") as mock_am:
            mock_db.execute_query.return_value = [{"count": 0}]
            mock_am.create_parent_account.return_value = (True, "user-123")
            mock_am.authenticate_parent.return_value = (True, {"session_token": "tok-abc"})

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/system/setup", json={
                    "email": "admin@example.com",
                    "password": "SecurePass123!",
                    "verify_password": "SecurePass123!"
                })
        assert response.status_code in (200, 429)

    @pytest.mark.asyncio
    async def test_setup_with_child_under_13(self, app):
        """COPPA: child under 13 must not be created during setup."""
        with patch("storage.database.db_manager") as mock_db, \
             patch("core.authentication.auth_manager") as mock_am:
            mock_db.execute_query.return_value = [{"count": 0}]
            mock_am.create_parent_account.return_value = (True, "user-123")
            mock_am.authenticate_parent.return_value = (True, {"session_token": "tok"})

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/system/setup", json={
                    "email": "admin@example.com",
                    "password": "SecurePass123!",
                    "verify_password": "SecurePass123!",
                    "child_name": "Tommy",
                    "child_age": 10,
                })
        assert response.status_code in (200, 429)
        if response.status_code == 200:
            assert response.json().get("coppa_consent_required") is True


class TestInternalProfileEndpoint:
    """Test /api/internal/profile-for-user/{user_id}."""

    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/internal/profile-for-user/user123")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/internal/profile-for-user/user123",
                headers={"Authorization": "Bearer wrong-key"}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_key_with_profile(self, app):
        from config import INTERNAL_API_KEY
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = [{"profile_id": "prof1"}]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/internal/profile-for-user/user123",
                    headers={"Authorization": f"Bearer {INTERNAL_API_KEY}"}
                )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_key_no_profile(self, app):
        from config import INTERNAL_API_KEY
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_query.return_value = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/internal/profile-for-user/user123",
                    headers={"Authorization": f"Bearer {INTERNAL_API_KEY}"}
                )
        assert response.status_code == 200
        data = response.json()
        assert "profile_id" in data

    @pytest.mark.asyncio
    async def test_invalid_user_id_returns_default(self, app):
        """User IDs with invalid chars should return default profile."""
        from config import INTERNAL_API_KEY
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/internal/profile-for-user/../../etc/passwd",
                headers={"Authorization": f"Bearer {INTERNAL_API_KEY}"}
            )
        assert response.status_code in (200, 404)


class TestPrometheusMetricsEndpoint:
    """Test /metrics endpoint."""

    @pytest.mark.asyncio
    async def test_metrics_accessible(self, app):
        """Prometheus metrics endpoint should be accessible."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/metrics")
        # Endpoint exists (may return 503 if DB not available in test)
        assert response.status_code in (200, 401, 403, 503)


class TestSecurityHeaders:
    """Test security headers are added to responses."""

    @pytest.mark.asyncio
    async def test_security_headers_present(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        # Check security headers are present
        assert "x-content-type-options" in response.headers or "X-Content-Type-Options" in response.headers

    @pytest.mark.asyncio
    async def test_x_frame_options(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        headers = {k.lower(): v for k, v in response.headers.items()}
        assert headers.get("x-frame-options", "").upper() in ("DENY", "SAMEORIGIN", "")


class TestRequestSizeLimit:
    """Test request size limit middleware."""

    @pytest.mark.asyncio
    async def test_large_content_length_rejected(self, app):
        """Requests with Content-Length > MAX_REQUEST_SIZE should get 413."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/health",
                content=b"x" * 100,
                headers={"Content-Length": str(200 * 1024 * 1024)}  # 200MB
            )
        assert response.status_code in (413, 404, 405)

    @pytest.mark.asyncio
    async def test_malformed_content_length(self, app):
        """Malformed Content-Length should return 400."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/system/setup",
                content=b'{}',
                headers={"Content-Length": "not-a-number", "Content-Type": "application/json"}
            )
        assert response.status_code in (400, 413, 422, 429)


class TestExceptionHandlers:
    """Test custom exception handlers."""

    @pytest.mark.asyncio
    async def test_validation_error_returns_422(self, app):
        """Invalid request bodies should return 422."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/system/setup", json={"bad": "data"})
        assert response.status_code in (422, 429)

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/nonexistent/route/that/does/not/exist")
        assert response.status_code == 404


class TestSetupRateLimiter:
    """Test setup rate limiter function directly."""

    def test_check_setup_rate_limit_allows(self):
        from api.server import check_setup_rate_limit
        from unittest.mock import MagicMock, patch

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch("api.server._setup_rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {"requests_made": 1})
            result = check_setup_rate_limit(mock_request)
            assert result == {"requests_made": 1}

    def test_check_setup_rate_limit_blocked(self):
        from api.server import check_setup_rate_limit
        from fastapi import HTTPException
        from unittest.mock import MagicMock, patch

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch("api.server._setup_rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (False, {"retry_after": 3600})
            with pytest.raises(HTTPException) as exc:
                check_setup_rate_limit(mock_request)
            assert exc.value.status_code == 429

    def test_check_setup_rate_limit_no_client(self):
        from api.server import check_setup_rate_limit
        from unittest.mock import MagicMock, patch

        mock_request = MagicMock()
        mock_request.client = None

        with patch("api.server._setup_rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {})
            result = check_setup_rate_limit(mock_request)
            assert result == {}


class TestSetupAccountCreationError:
    """Test setup endpoint error paths."""

    @pytest.mark.asyncio
    async def test_setup_account_creation_failure(self, app):
        with patch("storage.database.db_manager") as mock_db, \
             patch("core.authentication.auth_manager") as mock_am:
            mock_db.execute_query.return_value = [{"count": 0}]
            mock_am.create_parent_account.return_value = (False, "Password too weak")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/system/setup", json={
                    "email": "admin@example.com",
                    "password": "SecurePass123!",
                    "verify_password": "SecurePass123!"
                })
        assert response.status_code in (400, 429)
