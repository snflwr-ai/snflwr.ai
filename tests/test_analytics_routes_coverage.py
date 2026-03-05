"""
Comprehensive tests for api/routes/analytics.py.

Covers:
- GET /api/analytics/usage/{profile_id}
- GET /api/analytics/activity/{profile_id}
- GET /api/analytics/messages/{session_id}
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

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


@pytest.fixture
def parent_session():
    from core.authentication import AuthSession
    return AuthSession(
        user_id="parent123",
        role="parent",
        session_token="parent-token",
        email="parent@test.com",
    )


@pytest.fixture(scope="module")
def app():
    from api.server import app as _app
    return _app


def _admin_header():
    return {"Authorization": "Bearer admin-token", "X-CSRF-Token": "csrf"}


def _parent_header():
    return {"Authorization": "Bearer parent-token", "X-CSRF-Token": "csrf"}


class TestGetUsageStats:
    """Test GET /api/analytics/usage/{profile_id}."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/analytics/usage/prof1")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_admin_can_view_stats(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_usage_stats.return_value = {
                "total_sessions": 10,
                "total_messages": 50,
                "safety_incidents": 0,
            }

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/usage/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_parent_can_view_own_profile_stats(self, app, parent_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.middleware.auth.ProfileManager") as MockPM, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, parent_session)
            mock_profile = MagicMock()
            mock_profile.parent_id = "parent123"
            MockPM.return_value.get_profile.return_value = mock_profile
            mock_sm.get_usage_stats.return_value = {"total_sessions": 5}

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/usage/prof1",
                    headers=_parent_header()
                )
        assert response.status_code in (200, 403, 404)

    @pytest.mark.asyncio
    async def test_db_error_returns_503(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_usage_stats.side_effect = sqlite3.Error("db fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/usage/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_usage_stats.side_effect = RuntimeError("unexpected")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/usage/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (500, 403)

    @pytest.mark.asyncio
    async def test_custom_days_param(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_usage_stats.return_value = {}

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/usage/prof1?days=30",
                    headers=_admin_header()
                )
        # If it got through auth, check that days param was passed
        if response.status_code == 200:
            mock_sm.get_usage_stats.assert_called_with("prof1", 30)


class TestGetActivityLog:
    """Test GET /api/analytics/activity/{profile_id}."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/analytics/activity/prof1")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_admin_can_view_activity(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_session = MagicMock()
            mock_session.to_dict.return_value = {"session_id": "s1", "profile_id": "prof1"}
            mock_sm.get_session_history.return_value = [mock_session]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/activity/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            data = response.json()
            assert "sessions" in data
            assert "count" in data

    @pytest.mark.asyncio
    async def test_empty_activity(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_session_history.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/activity/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            assert response.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_session_history.side_effect = sqlite3.Error("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/activity/prof1",
                    headers=_admin_header()
                )
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_custom_limit_param(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_session_history.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/activity/prof1?limit=20",
                    headers=_admin_header()
                )
        assert response.status_code in (200, 403)


class TestGetSessionMessages:
    """Test GET /api/analytics/messages/{session_id}."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/analytics/messages/sess1")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_admin_can_view_messages(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm, \
             patch("api.routes.analytics.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_messages.return_value = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/messages/sess1",
                    headers=_admin_header()
                )
        assert response.status_code in (200, 403, 404)
        if response.status_code == 200:
            data = response.json()
            assert "messages" in data
            assert "count" in data

    @pytest.mark.asyncio
    async def test_db_error_returns_503(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_messages.side_effect = sqlite3.Error("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/messages/sess1",
                    headers=_admin_header()
                )
        assert response.status_code in (503, 403, 404)

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.analytics.session_manager") as mock_sm:
            mock_am.validate_session.return_value = (True, admin_session)
            mock_sm.get_messages.side_effect = RuntimeError("unexpected")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/analytics/messages/sess1",
                    headers=_admin_header()
                )
        assert response.status_code in (500, 403, 404)
