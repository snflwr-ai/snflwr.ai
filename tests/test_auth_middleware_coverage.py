"""
Tests for api/middleware/auth.py — audit log helpers, resource authorization,
Redis rate limiter, and audit failure tracking.
"""
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from fastapi import HTTPException

import safety.safety_monitor  # noqa: F401 — ensure module is in sys.modules
_safety_monitor_mod = sys.modules["safety.safety_monitor"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session():
    from api.middleware.auth import AuthSession
    return AuthSession(
        user_id="user-123",
        role="parent",
        session_token="tok",
    )


@pytest.fixture
def admin_session():
    from api.middleware.auth import AuthSession
    return AuthSession(
        user_id="admin-1",
        role="admin",
        session_token="tok",
    )


# ---------------------------------------------------------------------------
# Audit failure counter helpers
# ---------------------------------------------------------------------------

class TestAuditFailureCounters:
    def test_get_count_no_redis_returns_local(self):
        from api.middleware.auth import _get_audit_failure_count, _audit_failure_count_local
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 3
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = False
            count = _get_audit_failure_count()
        assert count == 3
        auth_mod._audit_failure_count_local = 0

    def test_get_count_redis_enabled(self):
        from api.middleware.auth import _get_audit_failure_count
        mock_client = MagicMock()
        mock_client.get.return_value = b"7"
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client = mock_client
            count = _get_audit_failure_count()
        assert count == 7

    def test_get_count_redis_missing_key_returns_zero(self):
        from api.middleware.auth import _get_audit_failure_count
        mock_client = MagicMock()
        mock_client.get.return_value = None
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client = mock_client
            count = _get_audit_failure_count()
        assert count == 0

    def test_get_count_redis_exception_falls_back(self):
        from api.middleware.auth import _get_audit_failure_count
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 2
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client.get.side_effect = Exception("redis down")
            count = _get_audit_failure_count()
        assert count == 2
        auth_mod._audit_failure_count_local = 0

    def test_increment_no_redis(self):
        from api.middleware.auth import _increment_audit_failure_count
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 0
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = False
            val = _increment_audit_failure_count()
        assert val == 1
        auth_mod._audit_failure_count_local = 0

    def test_increment_redis_enabled(self):
        from api.middleware.auth import _increment_audit_failure_count
        mock_client = MagicMock()
        mock_client.incr.return_value = 5
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client = mock_client
            val = _increment_audit_failure_count()
        assert val == 5

    def test_increment_redis_exception_falls_back(self):
        from api.middleware.auth import _increment_audit_failure_count
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 1
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client.incr.side_effect = Exception("redis down")
            val = _increment_audit_failure_count()
        assert val == 2
        auth_mod._audit_failure_count_local = 0

    def test_reset_no_redis(self):
        from api.middleware.auth import _reset_audit_failure_count
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 5
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = False
            _reset_audit_failure_count()
        assert auth_mod._audit_failure_count_local == 0

    def test_reset_redis_enabled(self):
        from api.middleware.auth import _reset_audit_failure_count
        mock_client = MagicMock()
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client = mock_client
            _reset_audit_failure_count()
        mock_client.delete.assert_called_once()

    def test_reset_redis_exception_silenced(self):
        from api.middleware.auth import _reset_audit_failure_count
        import api.middleware.auth as auth_mod
        auth_mod._audit_failure_count_local = 3
        with patch("utils.cache.cache") as mock_cache:
            mock_cache.enabled = True
            mock_cache._client.delete.side_effect = Exception("redis down")
            _reset_audit_failure_count()
        assert auth_mod._audit_failure_count_local == 0


class TestSendAuditFailureAlert:
    def test_sends_email_when_admin_email_configured(self):
        from api.middleware.auth import _send_audit_failure_alert
        with patch("config.system_config") as mock_cfg, \
             patch("tasks.background_tasks.safe_dispatch") as mock_dispatch:
            mock_cfg.ADMIN_EMAIL = "admin@school.edu"
            _send_audit_failure_alert(6, Exception("db error"))
            mock_dispatch.assert_called_once()

    def test_no_email_when_admin_email_not_set(self):
        from api.middleware.auth import _send_audit_failure_alert
        with patch("config.system_config") as mock_cfg:
            mock_cfg.ADMIN_EMAIL = None
            # Should not raise
            _send_audit_failure_alert(6, Exception("db error"))

    def test_exception_in_alert_silenced(self):
        from api.middleware.auth import _send_audit_failure_alert
        with patch("config.system_config") as mock_cfg, \
             patch("tasks.background_tasks.safe_dispatch", side_effect=Exception("mail down")):
            mock_cfg.ADMIN_EMAIL = "admin@school.edu"
            # Should not raise
            _send_audit_failure_alert(6, Exception("db error"))


# ---------------------------------------------------------------------------
# ResourceAuthorization.verify_session_access
# ---------------------------------------------------------------------------

class TestVerifySessionAccess:
    @pytest.mark.asyncio
    async def test_admin_can_access_any_session(self, admin_session):
        from api.middleware.auth import ResourceAuthorization
        result = await ResourceAuthorization.verify_session_access("sess-99", admin_session)
        assert result == admin_session

    @pytest.mark.asyncio
    async def test_session_not_found_raises_404(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        with patch("core.session_manager.session_manager") as mock_sm:
            mock_sm.get_session.return_value = None
            with pytest.raises(HTTPException) as exc_info:
                await ResourceAuthorization.verify_session_access("missing-sess", mock_session)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_profile_not_found_raises_404(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        mock_conv_session = MagicMock(profile_id="profile-abc")
        with patch("core.session_manager.session_manager") as mock_sm, \
             patch("core.profile_manager.ProfileManager") as mock_pm_cls:
            mock_sm.get_session.return_value = mock_conv_session
            mock_pm_cls.return_value.get_profile.return_value = None
            with pytest.raises(HTTPException) as exc_info:
                await ResourceAuthorization.verify_session_access("sess-1", mock_session)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_parent_raises_403(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        mock_conv_session = MagicMock(profile_id="profile-abc")
        mock_profile = MagicMock(parent_id="other-parent")
        with patch("core.session_manager.session_manager") as mock_sm, \
             patch("api.middleware.auth.ProfileManager") as mock_pm_cls:
            mock_sm.get_session.return_value = mock_conv_session
            mock_pm_cls.return_value.get_profile.return_value = mock_profile
            with pytest.raises(HTTPException) as exc_info:
                await ResourceAuthorization.verify_session_access("sess-1", mock_session)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_correct_parent_allowed(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        mock_conv_session = MagicMock(profile_id="profile-abc")
        mock_profile = MagicMock(parent_id="user-123")
        with patch("core.session_manager.session_manager") as mock_sm, \
             patch("api.middleware.auth.ProfileManager") as mock_pm_cls:
            mock_sm.get_session.return_value = mock_conv_session
            mock_pm_cls.return_value.get_profile.return_value = mock_profile
            result = await ResourceAuthorization.verify_session_access("sess-1", mock_session)
        assert result == mock_session


# ---------------------------------------------------------------------------
# ResourceAuthorization.verify_alert_access
# ---------------------------------------------------------------------------

class TestVerifyAlertAccess:
    @pytest.mark.asyncio
    async def test_admin_can_access_any_alert(self, admin_session):
        from api.middleware.auth import ResourceAuthorization
        result = await ResourceAuthorization.verify_alert_access("alert-99", admin_session)
        assert result == admin_session

    @pytest.mark.asyncio
    async def test_alert_not_found_raises_404(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        with patch.object(_safety_monitor_mod, "safety_monitor") as mock_sm:
            mock_sm.get_alert.return_value = None
            with pytest.raises(HTTPException) as exc_info:
                await ResourceAuthorization.verify_alert_access("missing-alert", mock_session)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_parent_raises_403(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        mock_alert = MagicMock(parent_id="other-parent")
        with patch.object(_safety_monitor_mod, "safety_monitor") as mock_sm:
            mock_sm.get_alert.return_value = mock_alert
            with pytest.raises(HTTPException) as exc_info:
                await ResourceAuthorization.verify_alert_access("alert-1", mock_session)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_correct_parent_allowed(self, mock_session):
        from api.middleware.auth import ResourceAuthorization
        mock_alert = MagicMock(parent_id="user-123")
        with patch.object(_safety_monitor_mod, "safety_monitor") as mock_sm:
            mock_sm.get_alert.return_value = mock_alert
            result = await ResourceAuthorization.verify_alert_access("alert-1", mock_session)
        assert result == mock_session


# ---------------------------------------------------------------------------
# RedisRateLimiter
# ---------------------------------------------------------------------------

class TestRedisRateLimiter:
    def _make_limiter(self, redis_enabled=False, mock_client=None):
        from api.middleware.auth import RedisRateLimiter
        limiter = RedisRateLimiter()
        if redis_enabled and mock_client:
            limiter._redis = mock_client
        return limiter

    def test_init_creates_limiter(self):
        from api.middleware.auth import RedisRateLimiter
        limiter = RedisRateLimiter()
        assert hasattr(limiter, "_redis")
        assert hasattr(limiter, "limits")

    def test_check_rate_limit_no_redis_allows(self):
        limiter = self._make_limiter(redis_enabled=False)
        limiter._redis = None
        result = limiter.check_rate_limit("user-123", "default")
        assert isinstance(result, bool)

    def test_check_rate_limit_redis_under_limit(self):
        mock_client = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.execute.return_value = [5, True]
        mock_client.pipeline.return_value = mock_pipeline
        limiter = self._make_limiter(redis_enabled=True, mock_client=mock_client)
        result = limiter.check_rate_limit("user-123", "default")
        assert result is True

    def test_check_rate_limit_redis_over_limit(self):
        mock_client = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.execute.return_value = [9999, True]
        mock_client.pipeline.return_value = mock_pipeline
        limiter = self._make_limiter(redis_enabled=True, mock_client=mock_client)
        result = limiter.check_rate_limit("user-123", "default")
        assert result is False

    def test_check_rate_limit_redis_exception_falls_back(self):
        try:
            from redis.exceptions import RedisError as _RedisError
        except ImportError:
            _RedisError = OSError
        mock_client = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.execute.side_effect = _RedisError("redis down")
        mock_client.pipeline.return_value = mock_pipeline
        limiter = self._make_limiter(redis_enabled=True, mock_client=mock_client)
        # Fails open (returns True) on Redis errors
        result = limiter.check_rate_limit("user-123", "default")
        assert result is True

    def test_get_remaining_no_redis(self):
        limiter = self._make_limiter()
        limiter._redis = None
        result = limiter.get_remaining("user-123")
        assert isinstance(result, int)

    def test_get_remaining_with_redis(self):
        mock_client = MagicMock()
        mock_client.get.return_value = b"10"
        limiter = self._make_limiter(redis_enabled=True, mock_client=mock_client)
        remaining = limiter.get_remaining("user-123", "default")
        assert isinstance(remaining, int)

    def test_reset_no_redis(self):
        limiter = self._make_limiter()
        limiter._redis = None
        result = limiter.reset("user-123")
        assert isinstance(result, bool)

    def test_reset_with_redis(self):
        mock_client = MagicMock()
        mock_client.delete.return_value = 1
        limiter = self._make_limiter(redis_enabled=True, mock_client=mock_client)
        result = limiter.reset("user-123")
        assert isinstance(result, bool)
