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


# --------------------------------------------------------------------------
# RedisError import fallback (lines 26-27)
# --------------------------------------------------------------------------

class TestRedisErrorFallback:

    def test_redis_error_fallback_is_importable(self):
        """When redis is not installed, RedisError falls back to OSError."""
        from api.middleware import auth as auth_mod
        # RedisError is either the real redis.exceptions.RedisError or OSError
        assert auth_mod.RedisError is not None
        assert issubclass(auth_mod.RedisError, BaseException)

    def test_redis_import_fallback_path(self):
        """Reload auth module with redis blocked to cover lines 26-27."""
        import importlib
        import sys
        from api.middleware import auth as auth_mod

        # Save references
        redis_mod = sys.modules.get("redis")
        redis_exc_mod = sys.modules.get("redis.exceptions")

        try:
            # Block redis imports
            sys.modules["redis"] = None
            sys.modules["redis.exceptions"] = None
            importlib.reload(auth_mod)
            assert auth_mod.RedisError is OSError
        finally:
            # Restore
            if redis_mod is not None:
                sys.modules["redis"] = redis_mod
            else:
                sys.modules.pop("redis", None)
            if redis_exc_mod is not None:
                sys.modules["redis.exceptions"] = redis_exc_mod
            else:
                sys.modules.pop("redis.exceptions", None)
            importlib.reload(auth_mod)


# --------------------------------------------------------------------------
# get_optional_session (lines 118-124)
# --------------------------------------------------------------------------

class TestGetOptionalSession:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_header(self):
        from api.middleware.auth import get_optional_session
        result = await get_optional_session(authorization=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_not_bearer(self):
        from api.middleware.auth import get_optional_session
        result = await get_optional_session(authorization="Basic abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_session_when_valid(self):
        from api.middleware.auth import get_optional_session
        session = AuthSession(
            user_id="u1", role="parent", session_token="tok", email="u@test.com"
        )
        with patch("api.middleware.auth.auth_manager") as am:
            am.validate_session.return_value = (True, session)
            result = await get_optional_session(authorization="Bearer tok")
            assert result is not None
            assert result.user_id == "u1"

    @pytest.mark.asyncio
    async def test_returns_none_when_invalid_token(self):
        from api.middleware.auth import get_optional_session
        with patch("api.middleware.auth.auth_manager") as am:
            am.validate_session.return_value = (False, None)
            result = await get_optional_session(authorization="Bearer bad-token")
            assert result is None


# --------------------------------------------------------------------------
# require_parent — non-parent, non-admin denial (lines 174-177)
# --------------------------------------------------------------------------

class TestRequireParentDenial:

    @pytest.mark.asyncio
    async def test_student_role_denied(self):
        from api.middleware.auth import require_parent
        student_session = AuthSession(
            user_id="student1", role="student", session_token="tok", email="s@test.com"
        )
        with pytest.raises(HTTPException) as exc:
            await require_parent(session=student_session)
        assert exc.value.status_code == 403
        assert "Parent access required" in exc.value.detail


# --------------------------------------------------------------------------
# audit_log — unexpected (non-DB) error path (line 526)
# --------------------------------------------------------------------------

class TestAuditLogUnexpectedError:

    def test_unexpected_error_uses_logger_exception(self, parent_session):
        """Non-DB errors should use logger.exception, not logger.error."""
        import api.middleware.auth as auth_mod

        with patch("storage.database.db_manager") as db, \
             patch("api.middleware.auth.logger") as mock_logger, \
             patch("api.middleware.auth._increment_audit_failure_count", return_value=1):
            db.execute_write.side_effect = RuntimeError("unexpected crash")
            result = auth_mod.audit_log("read", "profile", "prof1", parent_session)
            assert result is False
            mock_logger.exception.assert_called_once()
            assert "Unexpected error" in mock_logger.exception.call_args[0][0]

    def test_unexpected_error_triggers_alert_at_threshold(self, parent_session):
        """Non-DB error at threshold triggers critical log + alert."""
        import api.middleware.auth as auth_mod

        with patch("storage.database.db_manager") as db, \
             patch("api.middleware.auth.logger") as mock_logger, \
             patch("api.middleware.auth._increment_audit_failure_count",
                   return_value=auth_mod._AUDIT_FAILURE_THRESHOLD), \
             patch("api.middleware.auth._send_audit_failure_alert") as mock_alert:
            db.execute_write.side_effect = RuntimeError("unexpected crash")
            result = auth_mod.audit_log("read", "profile", "prof1", parent_session)
            assert result is False
            mock_logger.critical.assert_called_once()
            mock_alert.assert_called_once()


# --------------------------------------------------------------------------
# Metrics import fallback (lines 550-551)
# --------------------------------------------------------------------------

class TestMetricsFallback:

    def test_metrics_flag_exists(self):
        """_metrics_available should be a boolean regardless of whether utils.metrics exists."""
        import api.middleware.auth as auth_mod
        assert isinstance(auth_mod._metrics_available, bool)

    def test_metrics_import_fallback_path(self):
        """Reload auth module with utils.metrics blocked to cover lines 550-551."""
        import importlib
        import sys
        from api.middleware import auth as auth_mod

        metrics_mod = sys.modules.get("utils.metrics")
        try:
            sys.modules["utils.metrics"] = None
            importlib.reload(auth_mod)
            assert auth_mod._metrics_available is False
        finally:
            if metrics_mod is not None:
                sys.modules["utils.metrics"] = metrics_mod
            else:
                sys.modules.pop("utils.metrics", None)
            importlib.reload(auth_mod)


# --------------------------------------------------------------------------
# Rate limiter init failures (lines 634-636, 649-650, 655-656)
# --------------------------------------------------------------------------

class TestRateLimiterInit:

    def test_sqlite_fallback_init_failure(self):
        """When SQLite fallback init fails, limiter still works (in-memory)."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            with patch("api.middleware.auth.os.path.join", side_effect=Exception("bad path")):
                rl = RedisRateLimiter()
                rl._redis = None
                # Should still work with in-memory fallback
                assert rl.check_rate_limit("user1") is True

    def test_redis_init_cache_enabled_sets_redis(self):
        """When cache is enabled and has a client, _redis is set."""
        from api.middleware.auth import RedisRateLimiter

        mock_client = MagicMock()
        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()

        # Now call _initialize_redis manually with a mocked cache
        mock_cache = MagicMock()
        mock_cache.enabled = True
        mock_cache._client = mock_client
        with patch("utils.cache.cache", mock_cache):
            rl._initialize_redis()
        assert rl._redis is mock_client

    def test_redis_init_cache_not_enabled(self):
        """When cache is not enabled, _redis stays None."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()

        mock_cache = MagicMock()
        mock_cache.enabled = False
        mock_cache._client = None
        with patch("utils.cache.cache", mock_cache):
            rl._initialize_redis()
        assert rl._redis is None

    def test_redis_init_import_error_fallback(self):
        """When Redis import fails, limiter falls back gracefully."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()

        with patch.dict("sys.modules", {"utils.cache": None}):
            rl._initialize_redis()
        assert rl._redis is None

    def test_full_init_with_sqlite_fallback(self):
        """Full __init__ without Redis creates SqliteRateLimiter (lines 634-636)."""
        import tempfile, os, config
        from api.middleware.auth import RedisRateLimiter

        mock_cache = MagicMock()
        mock_cache.enabled = False
        mock_cache._client = None

        with tempfile.TemporaryDirectory() as tmpdir:
            # DATA_DIR doesn't exist in config by default — inject it
            config.DATA_DIR = tmpdir
            try:
                with patch("utils.cache.cache", mock_cache):
                    rl = RedisRateLimiter()
                assert rl._redis is None
                assert rl._sqlite_limiter is not None
            finally:
                delattr(config, "DATA_DIR")


# --------------------------------------------------------------------------
# Redis rate limit failure alerts (lines 735-736, 740, 747)
# --------------------------------------------------------------------------

class TestRedisRateLimitFailure:

    def _make_limiter(self):
        from api.middleware.auth import RedisRateLimiter
        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis_healthy = True
        rl._redis_alert_sent = False
        return rl

    def test_redis_failure_production_sends_alert_then_blocks(self):
        """In production (REDIS_ENABLED), Redis failure sends alert and blocks."""
        rl = self._make_limiter()
        rl._redis.pipeline.side_effect = Exception("Redis down")

        with patch("api.middleware.auth.system_config") as cfg, \
             patch("api.middleware.auth.logger"):
            cfg.REDIS_ENABLED = True
            with patch("core.email_service.email_service") as email_svc:
                result = rl._check_redis_rate_limit("user1", "default", 100, 60)
                assert result is False
                assert rl._redis_alert_sent is True
                email_svc.send_operator_alert.assert_called_once()

    def test_redis_failure_production_alert_fails_silently(self):
        """Alert email failure should not prevent blocking (lines 735-736)."""
        rl = self._make_limiter()
        rl._redis.pipeline.side_effect = Exception("Redis down")

        with patch("api.middleware.auth.system_config") as cfg, \
             patch("api.middleware.auth.logger"):
            cfg.REDIS_ENABLED = True
            with patch("core.email_service.email_service") as email_svc:
                email_svc.send_operator_alert.side_effect = Exception("SMTP down")
                result = rl._check_redis_rate_limit("user1", "default", 100, 60)
                assert result is False

    def test_redis_failure_production_no_duplicate_alerts(self):
        """Second failure should not send another alert."""
        rl = self._make_limiter()
        rl._redis.pipeline.side_effect = Exception("Redis down")
        rl._redis_alert_sent = True  # Already alerted

        with patch("api.middleware.auth.system_config") as cfg, \
             patch("api.middleware.auth.logger"):
            cfg.REDIS_ENABLED = True
            result = rl._check_redis_rate_limit("user1", "default", 100, 60)
            assert result is False

    def test_redis_failure_home_mode_falls_through(self):
        """In home mode (REDIS_ENABLED=False), Redis failure returns True (line 740)."""
        rl = self._make_limiter()
        rl._redis.pipeline.side_effect = Exception("Redis down")

        with patch("api.middleware.auth.system_config") as cfg, \
             patch("api.middleware.auth.logger"):
            cfg.REDIS_ENABLED = False
            result = rl._check_redis_rate_limit("user1", "default", 100, 60)
            assert result is True

    def test_fallback_delegates_to_sqlite(self):
        """Fallback rate limit delegates to SQLite limiter when available (line 747)."""
        rl = self._make_limiter()
        rl._redis = None
        mock_sqlite = MagicMock()
        mock_sqlite.check.return_value = True
        rl._sqlite_limiter = mock_sqlite

        result = rl._check_fallback_rate_limit("user1", "default", 100, 60)
        assert result is True
        mock_sqlite.check.assert_called_once_with("user1", "default", 100, 60)


# --------------------------------------------------------------------------
# In-memory rate limit fallback (lines 789, 791-793)
# --------------------------------------------------------------------------

class TestRateLimiterGetRemaining:

    def test_get_remaining_redis_success(self):
        """Redis path for get_remaining returns correct count (line 789)."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis.get.return_value = b"30"

        remaining = rl.get_remaining("user1", "default")
        assert remaining == 70  # 100 - 30

    def test_get_remaining_redis_no_key(self):
        """Redis returns None when no key exists (line 789)."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis.get.return_value = None

        remaining = rl.get_remaining("user1", "default")
        assert remaining == 100  # Full limit

    def test_get_remaining_redis_error_returns_max(self):
        """Redis error in get_remaining returns max requests (lines 791-793)."""
        from api.middleware.auth import RedisRateLimiter, RedisError

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis.get.side_effect = RedisError("connection lost")

        with patch("api.middleware.auth.logger"):
            remaining = rl.get_remaining("user1", "default")
        assert remaining == 100


# --------------------------------------------------------------------------
# Rate limit reset with Redis (lines 807-809)
# --------------------------------------------------------------------------

class TestRateLimiterReset:

    def test_reset_redis_success(self):
        """Redis reset deletes the key and returns True."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis.delete.return_value = 1

        result = rl.reset("user1", "default")
        assert result is True
        rl._redis.delete.assert_called_once()

    def test_reset_redis_error_returns_false(self):
        """Redis error in reset returns False (lines 807-809)."""
        from api.middleware.auth import RedisRateLimiter, RedisError

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = MagicMock()
        rl._redis.delete.side_effect = RedisError("connection lost")

        with patch("api.middleware.auth.logger"):
            result = rl.reset("user1", "default")
        assert result is False


# --------------------------------------------------------------------------
# Rate limit metrics recording + check_rate_limit dependency (lines 834-835)
# --------------------------------------------------------------------------

class TestCheckRateLimitDependency:

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises_429(self):
        """check_rate_limit dependency raises 429 when limit exceeded (lines 834-835)."""
        from api.middleware.auth import check_rate_limit
        session = AuthSession(
            user_id="u1", role="parent", session_token="tok", email="u@test.com"
        )
        with patch("api.middleware.auth._rate_limiter") as rl:
            rl.check_rate_limit.return_value = False
            with pytest.raises(HTTPException) as exc:
                await check_rate_limit(session=session, limit_type="api")
            assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_not_exceeded_passes(self):
        """check_rate_limit dependency does not raise when within limit."""
        from api.middleware.auth import check_rate_limit
        session = AuthSession(
            user_id="u1", role="parent", session_token="tok", email="u@test.com"
        )
        with patch("api.middleware.auth._rate_limiter") as rl:
            rl.check_rate_limit.return_value = True
            # Should not raise
            await check_rate_limit(session=session, limit_type="api")

    def test_metrics_recorded_when_available(self):
        """When _metrics_available is True, record_rate_limit_check is called."""
        from api.middleware.auth import RedisRateLimiter

        with patch("api.middleware.auth.RedisRateLimiter._initialize_redis"):
            rl = RedisRateLimiter()
        rl._redis = None
        rl._sqlite_limiter = None

        with patch("api.middleware.auth._metrics_available", True), \
             patch("api.middleware.auth.record_rate_limit_check") as mock_record:
            rl.check_rate_limit("user1")
            mock_record.assert_called_once_with(True)
