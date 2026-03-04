"""
Tests for api/middleware/auth.py — FERPA Access Controls

Compliance-critical paths tested:
    - get_current_session: bearer token validation, internal API key
    - require_admin / require_parent: role-based access control
    - ResourceAuthorization.verify_parent_access: parent ownership
    - ResourceAuthorization.verify_profile_access: profile ownership
    - audit_log: COPPA/FERPA audit trail, failure alerting
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import HTTPException

from core.authentication import AuthSession


@pytest.fixture
def parent_session():
    return AuthSession(
        user_id="parent123",
        role="parent",
        session_token="valid-token",
        email="parent@test.com",
    )


@pytest.fixture
def admin_session():
    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="admin-token",
        email="admin@test.com",
    )


# --------------------------------------------------------------------------
# get_current_session
# --------------------------------------------------------------------------

class TestGetCurrentSession:

    @pytest.mark.asyncio
    async def test_missing_authorization_header(self):
        from api.middleware.auth import get_current_session
        with pytest.raises(HTTPException) as exc:
            await get_current_session(authorization=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_scheme(self):
        from api.middleware.auth import get_current_session
        with pytest.raises(HTTPException) as exc:
            await get_current_session(authorization="Basic abc123")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_session_token(self):
        from api.middleware.auth import get_current_session

        session = AuthSession(
            user_id="parent123",
            role="parent",
            session_token="valid-tok",
            email="p@test.com",
        )
        with patch("api.middleware.auth.auth_manager") as am:
            am.validate_session.return_value = (True, session)
            result = await get_current_session(authorization="Bearer valid-tok")
            assert result.user_id == "parent123"
            assert result.role == "parent"

    @pytest.mark.asyncio
    async def test_invalid_session_token(self):
        from api.middleware.auth import get_current_session

        with patch("api.middleware.auth.auth_manager") as am:
            am.validate_session.return_value = (False, None)
            with pytest.raises(HTTPException) as exc:
                await get_current_session(authorization="Bearer expired-token")
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_internal_api_key(self):
        from api.middleware.auth import get_current_session
        from config import INTERNAL_API_KEY

        result = await get_current_session(authorization=f"Bearer {INTERNAL_API_KEY}")
        assert result.user_id == "internal_service"
        assert result.role == "admin"


# --------------------------------------------------------------------------
# require_admin / require_parent
# --------------------------------------------------------------------------

class TestRoleChecks:

    @pytest.mark.asyncio
    async def test_require_admin_allows_admin(self, admin_session):
        from api.middleware.auth import require_admin
        result = await require_admin(session=admin_session)
        assert result.role == "admin"

    @pytest.mark.asyncio
    async def test_require_admin_blocks_parent(self, parent_session):
        from api.middleware.auth import require_admin
        with pytest.raises(HTTPException) as exc:
            await require_admin(session=parent_session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_parent_allows_parent(self, parent_session):
        from api.middleware.auth import require_parent
        result = await require_parent(session=parent_session)
        assert result.role == "parent"

    @pytest.mark.asyncio
    async def test_require_parent_allows_admin(self, admin_session):
        from api.middleware.auth import require_parent
        result = await require_parent(session=admin_session)
        assert result.role == "admin"


# --------------------------------------------------------------------------
# verify_parent_access
# --------------------------------------------------------------------------

class TestVerifyParentAccess:

    @pytest.mark.asyncio
    async def test_parent_accesses_own_data(self, parent_session):
        from api.middleware.auth import ResourceAuthorization
        result = await ResourceAuthorization.verify_parent_access("parent123", parent_session)
        assert result.user_id == "parent123"

    @pytest.mark.asyncio
    async def test_parent_cannot_access_other_parent(self, parent_session):
        from api.middleware.auth import ResourceAuthorization
        with pytest.raises(HTTPException) as exc:
            await ResourceAuthorization.verify_parent_access("other_parent", parent_session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_access_any_parent(self, admin_session):
        from api.middleware.auth import ResourceAuthorization
        result = await ResourceAuthorization.verify_parent_access("parent123", admin_session)
        assert result.role == "admin"


# --------------------------------------------------------------------------
# verify_profile_access
# --------------------------------------------------------------------------

class TestVerifyProfileAccess:

    @pytest.mark.asyncio
    async def test_parent_accesses_own_child(self, parent_session):
        from api.middleware.auth import ResourceAuthorization
        from core.profile_manager import ChildProfile

        profile = ChildProfile(
            profile_id="prof1",
            parent_id="parent123",
            name="Tommy",
            age=10,
            grade="5th",
        )

        with patch("api.middleware.auth.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = profile
            result = await ResourceAuthorization.verify_profile_access("prof1", parent_session)
            assert result.user_id == "parent123"

    @pytest.mark.asyncio
    async def test_parent_cannot_access_other_child(self, parent_session):
        from api.middleware.auth import ResourceAuthorization
        from core.profile_manager import ChildProfile

        profile = ChildProfile(
            profile_id="prof1",
            parent_id="other_parent",
            name="Other Child",
            age=10,
            grade="5th",
        )

        with patch("api.middleware.auth.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = profile
            with pytest.raises(HTTPException) as exc:
                await ResourceAuthorization.verify_profile_access("prof1", parent_session)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_profile_not_found(self, parent_session):
        from api.middleware.auth import ResourceAuthorization

        with patch("api.middleware.auth.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = None
            with pytest.raises(HTTPException) as exc:
                await ResourceAuthorization.verify_profile_access("missing", parent_session)
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_can_access_any_profile(self, admin_session):
        from api.middleware.auth import ResourceAuthorization
        # Admin should not even query the profile
        result = await ResourceAuthorization.verify_profile_access("prof1", admin_session)
        assert result.role == "admin"


# --------------------------------------------------------------------------
# audit_log — COPPA/FERPA Audit Trail
# --------------------------------------------------------------------------

class TestAuditLog:

    def test_audit_log_writes_to_db(self, parent_session):
        from api.middleware.auth import audit_log

        with patch("storage.database.db_manager") as db:
            db.execute_write.return_value = None
            audit_log("read", "profile", "prof1", parent_session)
            db.execute_write.assert_called_once()
            args = db.execute_write.call_args[0]
            assert "INSERT INTO audit_log" in args[0]

    def test_audit_log_db_error_does_not_raise(self, parent_session):
        """Audit failure must not crash the request — but must be logged."""
        import sqlite3
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 0

        with patch("storage.database.db_manager") as db, \
             patch("api.middleware.auth._get_audit_failure_count", return_value=0), \
             patch("api.middleware.auth._increment_audit_failure_count", return_value=1):
            db.execute_write.side_effect = sqlite3.Error("db fail")
            # Should not raise
            auth_mod.audit_log("read", "profile", "prof1", parent_session)

    def test_audit_log_repeated_failures_trigger_alert(self, parent_session):
        """After threshold consecutive failures, critical alert must fire."""
        import sqlite3
        import api.middleware.auth as auth_mod

        with patch("storage.database.db_manager") as db, \
             patch("api.middleware.auth.logger") as mock_logger, \
             patch("api.middleware.auth._increment_audit_failure_count",
                   return_value=auth_mod._AUDIT_FAILURE_THRESHOLD):
            db.execute_write.side_effect = sqlite3.Error("db fail")
            auth_mod.audit_log("read", "profile", "prof1", parent_session)
            # Should have triggered critical log
            mock_logger.critical.assert_called_once()
            assert "compliance" in mock_logger.critical.call_args[0][0].lower()

    def test_audit_log_success_resets_failure_count(self, parent_session):
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 3

        with patch("storage.database.db_manager") as db, \
             patch("api.middleware.auth._reset_audit_failure_count") as mock_reset:
            db.execute_write.return_value = None
            auth_mod.audit_log("read", "profile", "prof1", parent_session)
            mock_reset.assert_called_once()


# --------------------------------------------------------------------------
# RedisRateLimiter
# --------------------------------------------------------------------------

class TestRedisRateLimiter:

    def test_fallback_rate_limiting(self):
        """In-memory fallback should work when Redis is unavailable."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
            rl._redis = None  # Ensure fallback mode

            # Should allow requests up to limit
            for _ in range(100):
                assert rl.check_rate_limit("user1") is True

            # Should block after limit
            assert rl.check_rate_limit("user1") is False

    def test_get_remaining(self):
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
            rl._redis = None
            assert rl.get_remaining("user1") == 100  # default limit

    def test_reset(self):
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
            rl._redis = None
            rl.check_rate_limit("user1")
            rl.reset("user1")
            assert rl.get_remaining("user1") == 100
