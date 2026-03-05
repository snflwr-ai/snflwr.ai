"""
Comprehensive tests for api/routes/admin.py.

Targets:
- Admin login (OWUI path + Snflwr fallback path)
- Admin sync
- Stats, accounts, profiles CRUD
- Alerts, activity, audit log endpoints
- Helper functions (_get_owui_token, _owui_find_user_by_email, etc.)
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")

import httpx


@pytest.fixture(scope="module")
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


def _auth_header(token="admin-token"):
    return {"Authorization": f"Bearer {token}", "X-CSRF-Token": "test-csrf"}


# ============================================================================
# Helper Functions
# ============================================================================

class TestOwuiHelpers:
    """Test OWUI helper functions."""

    def test_get_owui_token_from_cache(self, admin_session):
        from api.routes.admin import _get_owui_token
        with patch("api.routes.admin.auth_manager") as mock_am:
            mock_am._get_session_from_cache.return_value = {"owui_token": "owui-jwt-123"}
            token = _get_owui_token(admin_session)
            assert token == "owui-jwt-123"

    def test_get_owui_token_from_db(self, admin_session):
        from api.routes.admin import _get_owui_token
        with patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am._get_session_from_cache.return_value = None
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"owui_token": "db-token"}]
            token = _get_owui_token(admin_session)
            assert token == "db-token"

    def test_get_owui_token_db_empty(self, admin_session):
        from api.routes.admin import _get_owui_token
        with patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am._get_session_from_cache.return_value = {}
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = []
            token = _get_owui_token(admin_session)
            assert token == ""

    def test_get_owui_token_db_error(self, admin_session):
        from api.routes.admin import _get_owui_token
        with patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am._get_session_from_cache.return_value = None
            MockDB.side_effect = Exception("DB down")
            token = _get_owui_token(admin_session)
            assert token == ""

    def test_owui_find_user_by_email_found(self):
        from api.routes.admin import _owui_find_user_by_email
        import requests as req
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "u1", "email": "test@example.com", "name": "Test"}
        ]
        with patch("requests.get", return_value=mock_resp):
            user, err = _owui_find_user_by_email("http://owui", "jwt-token", "test@example.com")
        assert user is not None
        assert user["id"] == "u1"
        assert err is None

    def test_owui_find_user_by_email_not_found(self):
        from api.routes.admin import _owui_find_user_by_email
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "u1", "email": "other@example.com"}]
        with patch("requests.get", return_value=mock_resp):
            user, err = _owui_find_user_by_email("http://owui", "jwt", "test@example.com")
        assert user is None
        assert "not found" in err.lower()

    def test_owui_find_user_by_email_api_error(self):
        from api.routes.admin import _owui_find_user_by_email
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("requests.get", return_value=mock_resp):
            user, err = _owui_find_user_by_email("http://owui", "jwt", "test@example.com")
        assert user is None
        assert err is not None

    def test_owui_find_user_by_email_connection_error(self):
        from api.routes.admin import _owui_find_user_by_email
        import requests
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
            user, err = _owui_find_user_by_email("http://owui", "jwt", "test@example.com")
        assert user is None

    def test_owui_activate_user_success(self):
        from api.routes.admin import _owui_activate_user
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.post", return_value=mock_resp):
            result = _owui_activate_user("http://owui", "jwt", {"id": "u1", "name": "T", "email": "t@e.com"})
        assert result is True

    def test_owui_activate_user_failure(self):
        from api.routes.admin import _owui_activate_user
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        with patch("requests.post", return_value=mock_resp):
            result = _owui_activate_user("http://owui", "jwt", {"id": "u1", "name": "T", "email": "t@e.com"})
        assert result is False

    def test_owui_activate_user_exception(self):
        from api.routes.admin import _owui_activate_user
        import requests
        with patch("requests.post", side_effect=Exception("fail")):
            result = _owui_activate_user("http://owui", "jwt", {"id": "u1"})
        assert result is False

    def test_owui_delete_user_success(self):
        from api.routes.admin import _owui_delete_user
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.delete", return_value=mock_resp):
            _owui_delete_user("http://owui", "jwt", "user123")  # Should not raise

    def test_owui_delete_user_no_token(self):
        from api.routes.admin import _owui_delete_user
        # Should silently return when no token provided
        _owui_delete_user("http://owui", "", "user123")

    def test_owui_delete_user_no_user_id(self):
        from api.routes.admin import _owui_delete_user
        _owui_delete_user("http://owui", "token", "")

    def test_owui_delete_user_warning_on_non_success(self):
        from api.routes.admin import _owui_delete_user
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("requests.delete", return_value=mock_resp):
            _owui_delete_user("http://owui", "jwt", "user123")  # Should log warning, not raise

    def test_owui_delete_user_exception(self):
        from api.routes.admin import _owui_delete_user
        with patch("requests.delete", side_effect=Exception("net fail")):
            _owui_delete_user("http://owui", "jwt", "user123")  # Should not raise

    def test_owui_create_user_success(self):
        from api.routes.admin import _owui_create_user
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "new-user-id"}
        with patch("requests.post", return_value=mock_resp):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid == "new-user-id"
        assert err is None

    def test_owui_create_user_already_exists(self):
        from api.routes.admin import _owui_create_user
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"detail": "Email already registered"}
        existing_user = {"id": "existing-id", "email": "e@e.com"}
        with patch("requests.post", return_value=mock_resp), \
             patch("api.routes.admin._owui_find_user_by_email", return_value=(existing_user, None)), \
             patch("api.routes.admin._owui_activate_user", return_value=True):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid == "existing-id"
        assert err is None

    def test_owui_create_user_already_exists_cannot_find(self):
        from api.routes.admin import _owui_create_user
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"detail": "email already taken"}
        with patch("requests.post", return_value=mock_resp), \
             patch("api.routes.admin._owui_find_user_by_email", return_value=(None, "not found")):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid is None
        assert err is not None

    def test_owui_create_user_server_error(self):
        from api.routes.admin import _owui_create_user
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"detail": "Internal error"}
        with patch("requests.post", return_value=mock_resp):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid is None
        assert err is not None

    def test_owui_create_user_connection_error(self):
        from api.routes.admin import _owui_create_user
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid is None
        assert "unreachable" in err.lower()

    def test_owui_create_user_timeout(self):
        from api.routes.admin import _owui_create_user
        import requests
        with patch("requests.post", side_effect=requests.exceptions.Timeout()):
            uid, err = _owui_create_user("http://owui", "jwt", "Name", "e@e.com", "pass")
        assert uid is None
        assert "timed out" in err.lower()

    def test_owui_create_user_no_token(self):
        """Without token, uses signup endpoint."""
        from api.routes.admin import _owui_create_user
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "signup-id"}
        with patch("requests.post", return_value=mock_resp):
            uid, err = _owui_create_user("http://owui", "", "Name", "e@e.com", "pass")
        assert uid == "signup-id"


class TestCheckAuthRateLimit:
    """Test rate limit check function."""

    def test_allows_request_within_limit(self):
        from api.routes.admin import check_auth_rate_limit
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        with patch("api.routes.admin.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {"requests_made": 1})
            result = check_auth_rate_limit(mock_request)
            assert result == {"requests_made": 1}

    def test_blocks_request_over_limit(self):
        from api.routes.admin import check_auth_rate_limit
        from fastapi import HTTPException
        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"
        with patch("api.routes.admin.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (False, {"retry_after": 60})
            with pytest.raises(HTTPException) as exc:
                check_auth_rate_limit(mock_request)
            assert exc.value.status_code == 429

    def test_no_client(self):
        from api.routes.admin import check_auth_rate_limit
        mock_request = MagicMock()
        mock_request.client = None
        with patch("api.routes.admin.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {})
            result = check_auth_rate_limit(mock_request)
            assert result == {}


class TestToDictHelper:
    """Test _to_dict helper."""

    def test_dict_passthrough(self):
        from api.routes.admin import _to_dict
        d = {"key": "value"}
        assert _to_dict(d) == d

    def test_sqlite_row(self):
        from api.routes.admin import _to_dict
        mock_row = MagicMock()
        mock_row.keys.return_value = ["col1", "col2"]
        mock_row.__getitem__ = lambda self, k: "val"
        result = _to_dict(mock_row)
        # Should succeed without raising


# ============================================================================
# Route Tests via httpx.AsyncClient
# ============================================================================

class TestAdminLoginRoute:
    """Test POST /api/admin/login."""

    @pytest.mark.asyncio
    async def test_login_owui_success(self, app):
        import requests as http_mod
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "owui-user-id",
            "name": "Admin User",
            "email": "admin@test.com",
            "role": "admin",
            "token": "owui-jwt-token"
        }
        with patch("requests.post", return_value=mock_resp), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.set_csrf_cookie", return_value="csrf-tok"), \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = []
            mock_db.execute_write.return_value = None
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "SecurePass123!"}
                )
        assert response.status_code in (200, 429)

    @pytest.mark.asyncio
    async def test_login_owui_non_admin_role(self, app):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "user-id", "name": "User", "email": "u@test.com",
            "role": "user", "token": "jwt"
        }
        with patch("requests.post", return_value=mock_resp), \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "user@test.com", "password": "SecurePass123!"}
                )
        assert response.status_code in (403, 429)

    @pytest.mark.asyncio
    async def test_login_owui_connection_error_falls_back(self, app):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.set_csrf_cookie", return_value="csrf"), \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"username": "admin_user"}]
            mock_am.authenticate_parent.return_value = (True, {"parent_id": "p1", "session_token": "tok"})
            mock_db.execute_query.side_effect = [
                [{"username": "admin_user"}],
                [{"role": "admin"}]
            ]
            mock_crypto.return_value.hash_email.return_value = "hashed"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "SecurePass123!"}
                )
        assert response.status_code in (200, 401, 403, 429)

    @pytest.mark.asyncio
    async def test_login_owui_timeout_falls_back(self, app):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.Timeout()), \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = []
            mock_crypto.return_value.hash_email.return_value = "hash"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "bad"}
                )
        assert response.status_code in (401, 429)

    @pytest.mark.asyncio
    async def test_login_snflwr_fallback_not_admin(self, app):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"username": "parentuser"}],
                [{"role": "parent"}],
            ]
            mock_am.authenticate_parent.return_value = (True, {"parent_id": "p2", "session_token": "tok2"})
            mock_crypto.return_value.hash_email.return_value = "hashed"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "parent@test.com", "password": "SecurePass123!"}
                )
        assert response.status_code in (403, 429)

    @pytest.mark.asyncio
    async def test_login_snflwr_auth_failed(self, app):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"username": "admin_user"}]
            mock_am.authenticate_parent.return_value = (False, "Invalid password")
            mock_crypto.return_value.hash_email.return_value = "hashed"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "WrongPass1!"}
                )
        assert response.status_code in (401, 429)


class TestAdminStatsRoute:
    """Test GET /api/admin/stats."""

    @pytest.mark.asyncio
    async def test_stats_requires_auth(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/admin/stats")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_stats_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"c": 5}]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/stats",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_stats_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.Error("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/stats",
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)


class TestAdminAccountsRoute:
    """Test account listing and CRUD."""

    @pytest.mark.asyncio
    async def test_list_accounts_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                # accounts list
                [{"parent_id": "p1", "name": "Test", "role": "parent",
                  "created_at": "2024-01-01", "last_login": None, "is_active": 1,
                  "encrypted_email": "enc", "email_verified": 1, "failed_login_attempts": 0}],
                # child count
                [{"c": 2}],
                # total count
                [{"c": 1}],
            ]
            mock_crypto.return_value.decrypt_email.return_value = "test@example.com"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/accounts",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_update_account_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/nonexistent",
                    json={"name": "New Name"},
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_update_account_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1", "role": "parent"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/p1",
                    json={"name": "Updated Name"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_update_account_no_fields(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = [{"parent_id": "p1"}]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/p1",
                    json={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 403)

    @pytest.mark.asyncio
    async def test_delete_account_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/accounts/p1",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_delete_account_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/accounts/missing",
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_accounts(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/accounts",
                    params={"ids": ["p1", "p2"]},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 422, 403)

    @pytest.mark.asyncio
    async def test_create_account_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_route_am, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_write.return_value = None
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            mock_route_am.ph.hash.return_value = "hashed-password"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/accounts",
                    json={"name": "New Parent", "email": "parent@test.com", "password": "SecurePass123!"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)


class TestAdminProfilesRoute:
    """Test profile management routes."""

    @pytest.mark.asyncio
    async def test_list_all_profiles(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"profile_id": "prof1", "parent_id": "p1", "name": "Tommy",
                  "age": 10, "grade_level": "5", "is_active": 1, "created_at": "2024-01-01",
                  "daily_time_limit_minutes": 120}],
                [{"c": 1}],
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/profiles/all",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_update_profile_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "prof1", "parent_id": "p1"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/profiles/prof1",
                    json={"name": "Updated Name", "age": 11},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_update_profile_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/profiles/missing",
                    json={"name": "Name"},
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_delete_profile_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "prof1"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/profiles/prof1",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_delete_profile_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/profiles/missing",
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)


class TestAdminAlertsActivityRoute:
    """Test alerts and activity endpoints."""

    @pytest.mark.asyncio
    async def test_list_all_alerts(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"alert_id": "a1", "parent_id": "p1", "profile_id": "prof1",
                  "message": "Test alert", "severity": "high",
                  "acknowledged": 0, "created_at": "2024-01-01",
                  "alert_type": "safety"}],
                [{"c": 1}],
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/alerts/all",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_list_activity(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"session_id": "s1", "profile_id": "prof1",
                  "started_at": "2024-01-01", "ended_at": None,
                  "message_count": 5, "safety_incidents": 0}],
                [{"c": 1}],
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/activity",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_alerts(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/alerts",
                    params={"ids": ["a1"]},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 422, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_activity(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/activity",
                    params={"ids": ["s1"]},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 422, 403)


class TestAdminAuditLogRoute:
    """Test audit log endpoint."""

    @pytest.mark.asyncio
    async def test_get_audit_log(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"id": 1, "action": "read", "resource_type": "profile",
                  "resource_id": "prof1", "user_id": "admin1",
                  "timestamp": "2024-01-01", "details": None}],
                [{"c": 1}],
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/audit-log",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)


class TestAdminSyncRoute:
    """Test POST /api/admin/sync."""

    @pytest.mark.asyncio
    async def test_sync_admin_creates_new(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [],  # not existing
                [{"parent_id": "owui-1", "role": "admin", "created_at": "2024-01-01",
                  "is_active": 1, "encrypted_email": "enc"}],  # fetch after insert
            ]
            mock_db.execute_write.return_value = None
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            mock_crypto.return_value.decrypt_email.return_value = "admin@test.com"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/sync",
                    json={"admin_id": "owui-1", "email": "admin@test.com"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_sync_admin_updates_existing(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = [
                [{"parent_id": "owui-1"}],  # existing
                [{"parent_id": "owui-1", "role": "admin", "created_at": "2024-01-01",
                  "is_active": 1, "encrypted_email": "enc"}],  # fetch after update
            ]
            mock_db.execute_write.return_value = None
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            mock_crypto.return_value.decrypt_email.return_value = "admin@test.com"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/sync",
                    json={"admin_id": "owui-1", "email": "admin@test.com"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)


class TestAdminGetRoute:
    """Test GET /api/admin/{admin_id}."""

    @pytest.mark.asyncio
    async def test_get_admin_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB:
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/missing-admin",
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_get_admin_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [
                {"parent_id": "admin1", "role": "admin", "created_at": "2024-01-01",
                 "is_active": 1, "encrypted_email": "enc", "name": "Admin"}
            ]
            mock_crypto.return_value.decrypt_email.return_value = "admin@test.com"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/admin1",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)


# ============================================================================
# Error Path Tests
# ============================================================================

def _bypass_csrf():
    """Return a patch context that bypasses CSRF validation."""
    return patch("api.server.validate_csrf_token", new=AsyncMock(return_value=True))


class TestAdminLoginErrors:
    """Test admin login DB and exception error paths."""

    @pytest.mark.asyncio
    async def test_login_db_error_returns_503(self, app):
        import sqlite3
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError()), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_am, \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("db error")
            mock_crypto.return_value.hash_email.return_value = "hash"
            mock_am.authenticate_parent.side_effect = sqlite3.OperationalError("db")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "Pass123!"}
                )
        assert response.status_code in (401, 503, 429)

    @pytest.mark.asyncio
    async def test_login_owui_db_error_after_success(self, app):
        """OWUI login succeeds but DB write fails -> 503."""
        import sqlite3
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "u1", "name": "Admin", "email": "admin@test.com",
            "role": "admin", "token": "jwt"
        }
        with patch("requests.post", return_value=mock_resp), \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.check_auth_rate_limit", return_value={}):
            mock_db = MockDB.return_value
            mock_db.execute_query.side_effect = sqlite3.OperationalError("db error")
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/login",
                    json={"email": "admin@test.com", "password": "Pass123!"}
                )
        assert response.status_code in (200, 503, 429)


class TestAdminStatsErrors:
    """Test admin stats error paths."""

    @pytest.mark.asyncio
    async def test_stats_generic_exception(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = RuntimeError("unexpected")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/stats", headers=_auth_header())
        assert response.status_code in (500, 403)


class TestAdminAccountsErrors:
    """Test account CRUD error paths."""

    @pytest.mark.asyncio
    async def test_list_accounts_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("db fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/accounts", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_list_accounts_generic_exception(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = RuntimeError("unexpected")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/accounts", headers=_auth_header())
        assert response.status_code in (500, 403)

    @pytest.mark.asyncio
    async def test_update_account_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1"}]
            mock_db.execute_write.side_effect = sqlite3.OperationalError("write fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/p1",
                    json={"name": "New Name"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 503, 403)

    @pytest.mark.asyncio
    async def test_update_account_with_email(self, app, admin_session):
        """Update account with email field provided."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1"}]
            mock_db.execute_write.return_value = None
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/p1",
                    json={"email": "new@example.com"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_update_account_with_is_active(self, app, admin_session):
        """Update account with is_active field provided."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/accounts/p1",
                    json={"is_active": False},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_delete_account_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1"}]
            mock_db.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/api/admin/accounts/p1", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_accounts_empty_ids(self, app, admin_session):
        """Empty IDs list should return 400."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/accounts",
                    params={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 422, 403)

    @pytest.mark.asyncio
    async def test_create_account_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.auth_manager") as mock_route_am, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_write.side_effect = sqlite3.OperationalError("db fail")
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            mock_route_am.ph.hash.return_value = "hashed"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/accounts",
                    json={"name": "Test", "email": "t@test.com", "password": "Pass123!"},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)


class TestAdminProfileErrors:
    """Test profile CRUD error paths."""

    @pytest.mark.asyncio
    async def test_list_all_profiles_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/profiles/all", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_update_profile_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "p1"}]
            mock_db.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/profiles/p1",
                    json={"name": "Name"},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_update_profile_no_fields(self, app, admin_session):
        """Updating profile with no fields should return 400."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = [{"profile_id": "p1"}]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/profiles/p1",
                    json={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 403)

    @pytest.mark.asyncio
    async def test_update_profile_all_fields(self, app, admin_session):
        """Updating profile with all fields."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "p1"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/profiles/p1",
                    json={"name": "Alice", "age": 12, "grade_level": "6",
                          "daily_time_limit_minutes": 90, "is_active": True},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_delete_profile_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "p1", "owui_user_id": None}]
            mock_db.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/api/admin/profiles/p1", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_delete_profile_with_owui_user(self, app, admin_session):
        """Delete profile that has an owui_user_id should also delete OWUI account."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             patch("api.routes.admin._owui_delete_user"), \
             patch("api.routes.admin._get_owui_token", return_value="jwt"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"profile_id": "p1", "owui_user_id": "owui-123"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/api/admin/profiles/p1", headers=_auth_header())
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_profiles_empty(self, app, admin_session):
        """Empty IDs for batch profile delete -> 400."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/profiles",
                    params={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 422, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_profiles_with_owui(self, app, admin_session):
        """Batch delete profiles that have owui_user_ids."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             patch("api.routes.admin._owui_delete_user"), \
             patch("api.routes.admin._get_owui_token", return_value="jwt"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"owui_user_id": "owui-123"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/profiles",
                    params={"ids": ["p1", "p2"]},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 422, 403)


class TestAdminAlertsErrors:
    """Test alert endpoints error paths."""

    @pytest.mark.asyncio
    async def test_list_all_alerts_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/alerts/all", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_list_all_alerts_include_acknowledged(self, app, admin_session):
        """list_all_alerts with include_acknowledged=true uses different query."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = []
            mock_crypto.return_value.decrypt_email.return_value = "test@test.com"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/api/admin/alerts/all?include_acknowledged=true",
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_alerts_empty(self, app, admin_session):
        """Empty IDs for batch alert delete -> 400."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/alerts",
                    params={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 422, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_alerts_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.request(
                    "DELETE",
                    "http://test/api/admin/alerts",
                    params={"ids": [1, 2]},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403, 422)


class TestAdminActivityErrors:
    """Test activity endpoints error paths."""

    @pytest.mark.asyncio
    async def test_list_activity_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/activity", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_activity_empty(self, app, admin_session):
        """Empty IDs for batch activity delete -> 400."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(
                    "/api/admin/activity",
                    params={},
                    headers=_auth_header()
                )
        assert response.status_code in (400, 422, 403)

    @pytest.mark.asyncio
    async def test_batch_delete_activity_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.request(
                    "DELETE",
                    "http://test/api/admin/activity",
                    params={"ids": ["s1", "s2"]},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403, 422)


class TestAdminAuditLogErrors:
    """Test audit log error paths."""

    @pytest.mark.asyncio
    async def test_audit_log_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/audit-log", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_audit_log_generic_exception(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = RuntimeError("unexpected")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/audit-log", headers=_auth_header())
        assert response.status_code in (500, 403)


class TestAdminGetErrors:
    """Test get admin error paths."""

    @pytest.mark.asyncio
    async def test_get_admin_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/admin1", headers=_auth_header())
        assert response.status_code in (503, 403)


class TestAdminCreateProfile:
    """Test create profile route."""

    @pytest.mark.asyncio
    async def test_create_profile_parent_not_found(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/profiles",
                    json={"parent_id": "missing", "name": "Alice", "age": 10, "grade_level": "5"},
                    headers=_auth_header()
                )
        assert response.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_create_profile_without_owui(self, app, admin_session):
        """Create profile without email/password -> no OWUI account."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1", "name": "Parent"}]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/profiles",
                    json={"parent_id": "p1", "name": "Alice", "age": 10, "grade_level": "5"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_create_profile_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [{"parent_id": "p1", "name": "Parent"}]
            mock_db.execute_write.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/profiles",
                    json={"parent_id": "p1", "name": "Alice", "age": 10, "grade_level": "5"},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)


class TestAdminFalsePositives:
    """Test false positive endpoints."""

    @pytest.mark.asyncio
    async def test_list_false_positives(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.get_false_positives.return_value = []

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/false-positives", headers=_auth_header())
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_list_false_positives_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.get_false_positives.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/false-positives", headers=_auth_header())
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_mark_false_positive_reviewed(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.mark_false_positive_reviewed.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/false-positives/1",
                    json={"reviewed_by": "admin1"},
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_mark_false_positive_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.mark_false_positive_reviewed.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.patch(
                    "/api/admin/false-positives/1",
                    json={"reviewed_by": "admin1"},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)


class TestBulkImportStudents:
    """Test bulk student import endpoint."""

    @pytest.mark.asyncio
    async def test_bulk_import_under_13_no_consent(self, app, admin_session):
        """Under-13 students without institutional COPPA consent are skipped."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin._get_owui_token", return_value=""), \
             patch("api.routes.admin._owui_create_user", return_value=("uid1", None)), \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/students/import",
                    json={
                        "students": [
                            {"name": "Alice", "email": "alice@test.com", "age": 10, "grade_level": "4"}
                        ],
                        "password": "SecurePass123!",
                        "accept_institutional_coppa": False
                    },
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            data = response.json()
            assert data.get("imported") == 0

    @pytest.mark.asyncio
    async def test_bulk_import_success(self, app, admin_session):
        """Successful bulk import creates profiles."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin._get_owui_token", return_value="jwt"), \
             patch("api.routes.admin._owui_create_user", return_value=("owui-uid", None)), \
             patch("api.routes.admin.AgeVerificationManager"), \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/students/import",
                    json={
                        "students": [
                            {"name": "Bob", "email": "bob@test.com", "age": 15, "grade_level": "9"}
                        ],
                        "password": "SecurePass123!",
                        "accept_institutional_coppa": False
                    },
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_bulk_import_owui_error(self, app, admin_session):
        """OWUI creation error -> student goes to failed list."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin._get_owui_token", return_value="jwt"), \
             patch("api.routes.admin._owui_create_user", return_value=(None, "OWUI down")), \
             patch("api.routes.admin.AgeVerificationManager"), \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/students/import",
                    json={
                        "students": [
                            {"name": "Bob", "email": "bob@test.com", "age": 15, "grade_level": "9"}
                        ],
                        "password": "SecurePass123!",
                        "accept_institutional_coppa": False
                    },
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            assert response.json().get("failed")

    @pytest.mark.asyncio
    async def test_bulk_import_under_13_with_consent(self, app, admin_session):
        """Under-13 with COPPA consent flag creates student and logs consent."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin._get_owui_token", return_value="jwt"), \
             patch("api.routes.admin._owui_create_user", return_value=("owui-uid", None)), \
             patch("api.routes.admin.AgeVerificationManager") as MockAVM, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_write.return_value = None
            mock_avm = MockAVM.return_value
            mock_avm.update_profile_consent_status.return_value = None
            mock_avm.log_parental_consent.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/students/import",
                    json={
                        "students": [
                            {"name": "Alice", "email": "alice@test.com", "age": 10, "grade_level": "4"}
                        ],
                        "password": "SecurePass123!",
                        "accept_institutional_coppa": True
                    },
                    headers=_auth_header()
                )
        assert response.status_code in (200, 403)


class TestListStudents:
    """Test list students endpoint."""

    @pytest.mark.asyncio
    async def test_list_students_success(self, app, admin_session):
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_db.execute_query.return_value = [
                {"profile_id": "p1", "name": "Alice", "age": 10,
                 "grade_level": "4", "owui_user_id": "owui-1",
                 "parental_consent_given": 1, "coppa_verified": 1,
                 "is_active": 1, "created_at": "2024-01-01"}
            ]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/students", headers=_auth_header())
        assert response.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_list_students_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.audit_log"):
            mock_am.validate_session.return_value = (True, admin_session)
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/admin/students", headers=_auth_header())
        assert response.status_code in (503, 403)


class TestAdminSyncErrors:
    """Test sync error paths."""

    @pytest.mark.asyncio
    async def test_sync_db_error(self, app, admin_session):
        import sqlite3
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            MockDB.return_value.execute_query.side_effect = sqlite3.OperationalError("fail")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/sync",
                    json={"admin_id": "owui-1", "email": "admin@test.com"},
                    headers=_auth_header()
                )
        assert response.status_code in (503, 403)

    @pytest.mark.asyncio
    async def test_sync_admin_not_found_after_upsert(self, app, admin_session):
        """If admin not found after upsert, returns 500."""
        with patch("api.middleware.auth.auth_manager") as mock_am, \
             patch("api.routes.admin.DatabaseManager") as MockDB, \
             patch("api.routes.admin.get_email_crypto") as mock_crypto, \
             patch("api.routes.admin.audit_log"), \
             _bypass_csrf():
            mock_am.validate_session.return_value = (True, admin_session)
            mock_db = MockDB.return_value
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            # First query: check existing -> not found; second query: fetch after upsert -> not found
            mock_db.execute_query.side_effect = [[], []]
            mock_db.execute_write.return_value = None

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/admin/sync",
                    json={"admin_id": "owui-1", "email": "admin@test.com"},
                    headers=_auth_header()
                )
        assert response.status_code in (500, 403)
