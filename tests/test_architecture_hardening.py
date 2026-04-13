"""Tests for architecture hardening: key rotation, rate limiting, classifier recovery."""

import pytest
from unittest.mock import patch, MagicMock


class TestOperatorAlert:
    """Operator alert email delivery."""

    @patch("core.email_service.EmailService._send_email", return_value=(True, None))
    def test_send_operator_alert_delivers_email(self, mock_send):
        from core.email_service import email_service

        old_enabled = email_service.enabled
        email_service.enabled = True
        try:
            with patch("core.email_service.system_config") as cfg:
                cfg.ADMIN_EMAIL = "admin@school.edu"
                cfg.SMTP_FROM_NAME = "snflwr.ai"
                cfg.SMTP_FROM_EMAIL = "noreply@snflwr.ai"
                result, err = email_service.send_operator_alert(
                    subject="Test alert",
                    description="Something happened",
                )
            assert result is True
            assert err is None
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[1]["to_email"] == "admin@school.edu"
            assert call_args[1]["subject"] == "[snflwr.ai] Test alert"
        finally:
            email_service.enabled = old_enabled

    def test_send_operator_alert_skips_when_no_admin_email(self):
        from core.email_service import email_service

        with patch("core.email_service.system_config") as cfg:
            cfg.ADMIN_EMAIL = ""
            result, err = email_service.send_operator_alert(
                subject="Test", description="test"
            )
        assert result is True  # Not a failure, just skipped


import os
from datetime import datetime, timezone, timedelta


class TestKeyRotationConfig:
    """INTERNAL_API_KEY rotation config validation."""

    def test_previous_key_defaults_to_none(self):
        """When env var is unset, INTERNAL_API_KEY_PREVIOUS should be None."""
        from config import INTERNAL_API_KEY_PREVIOUS

        # Default (no env var set in test) should be None
        if os.getenv("INTERNAL_API_KEY_PREVIOUS") is None:
            assert INTERNAL_API_KEY_PREVIOUS is None

    def test_max_age_days_defaults_to_90(self):
        """When env var is unset, max age should default to 90."""
        from config import INTERNAL_API_KEY_MAX_AGE_DAYS

        if os.getenv("INTERNAL_API_KEY_MAX_AGE_DAYS") is None:
            assert INTERNAL_API_KEY_MAX_AGE_DAYS == 90

    def test_max_age_days_type_is_int(self):
        """Max age days should always be an integer."""
        from config import INTERNAL_API_KEY_MAX_AGE_DAYS

        assert isinstance(INTERNAL_API_KEY_MAX_AGE_DAYS, int)

    def test_insecure_default_rejected_in_prod(self):
        """Production validation must reject snflwr-internal-dev-key."""
        from config import ProductionConfigValidator

        with patch.dict(
            os.environ,
            {"INTERNAL_API_KEY": "snflwr-internal-dev-key", "SNFLWR_ENV": "production"},
            clear=False,
        ):
            validator = ProductionConfigValidator()
            errors, _warnings = validator.validate()
            assert any("insecure" in e.lower() for e in errors)


class TestDualKeyAuth:
    """Dual-key authentication in auth middleware."""

    def test_primary_key_authenticates(self):
        with patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary"), \
             patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", None):
            from api.middleware.auth import get_current_session
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    get_current_session("Bearer new-key-primary")
                )
            finally:
                loop.close()
            assert result is not None
            assert result.user_id == "internal_service"

    def test_previous_key_authenticates_during_rotation(self):
        with patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary"), \
             patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", "old-key-previous"):
            from api.middleware.auth import get_current_session
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    get_current_session("Bearer old-key-previous")
                )
            finally:
                loop.close()
            assert result is not None
            assert result.user_id == "internal_service"

    def test_random_key_rejected(self):
        with patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary"), \
             patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", "old-key-previous"):
            from api.middleware.auth import get_current_session
            import asyncio
            from fastapi import HTTPException

            loop = asyncio.new_event_loop()
            try:
                with pytest.raises(HTTPException):
                    loop.run_until_complete(
                        get_current_session("Bearer totally-wrong-key")
                    )
            finally:
                loop.close()


class TestKeyRotationAgeCheck:
    """Background task warns when API key is overdue for rotation."""

    @patch("core.email_service.email_service.send_operator_alert")
    def test_warns_when_key_overdue(self, mock_alert):
        mock_alert.return_value = (True, None)
        import asyncio

        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        loop = asyncio.new_event_loop()
        try:
            with patch(
                "config.INTERNAL_API_KEY_CREATED_AT", old_date
            ), patch("config.INTERNAL_API_KEY_MAX_AGE_DAYS", 90):
                import importlib
                import api.server as _srv
                importlib.reload(_srv)
                loop.run_until_complete(_srv.check_key_rotation_age())
        finally:
            loop.close()
        mock_alert.assert_called_once()
        call_str = str(mock_alert.call_args)
        assert "100" in call_str

    @patch("core.email_service.email_service.send_operator_alert")
    def test_no_warning_when_key_fresh(self, mock_alert):
        import asyncio

        recent_date = datetime.now(timezone.utc) - timedelta(days=10)
        loop = asyncio.new_event_loop()
        try:
            with patch(
                "config.INTERNAL_API_KEY_CREATED_AT", recent_date
            ), patch("config.INTERNAL_API_KEY_MAX_AGE_DAYS", 90):
                import importlib
                import api.server as _srv
                importlib.reload(_srv)
                loop.run_until_complete(_srv.check_key_rotation_age())
        finally:
            loop.close()
        mock_alert.assert_not_called()

    @patch("core.email_service.email_service.send_operator_alert")
    def test_no_warning_when_created_at_not_set(self, mock_alert):
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            with patch("config.INTERNAL_API_KEY_CREATED_AT", None):
                import importlib
                import api.server as _srv
                importlib.reload(_srv)
                loop.run_until_complete(_srv.check_key_rotation_age())
        finally:
            loop.close()
        mock_alert.assert_not_called()


import tempfile
import time


class TestSqliteRateLimiter:
    """SQLite-backed rate limiter for home mode."""

    def test_enforces_limit(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            limiter = SqliteRateLimiter(db_path)
            for _ in range(3):
                assert limiter.check("user1", "default", 3, 60) is True
            assert limiter.check("user1", "default", 3, 60) is False

    def test_persists_across_instances(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            limiter1 = SqliteRateLimiter(db_path)
            for _ in range(3):
                limiter1.check("user1", "default", 3, 60)

            limiter2 = SqliteRateLimiter(db_path)
            assert limiter2.check("user1", "default", 3, 60) is False

    def test_cleans_expired_entries(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            limiter = SqliteRateLimiter(db_path)
            for _ in range(3):
                limiter.check("user1", "default", 3, 1)

            time.sleep(1.1)
            assert limiter.check("user1", "default", 3, 1) is True


class TestRedisFailClosed:
    """Production rate limiting must block on Redis errors."""

    @patch("api.middleware.auth.system_config")
    def test_blocks_request_on_redis_error(self, mock_cfg):
        mock_cfg.REDIS_ENABLED = True
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter.__new__(RedisRateLimiter)
        limiter.limits = {"default": (100, 60)}
        limiter._redis = MagicMock()
        limiter._redis.pipeline.side_effect = Exception("Connection refused")
        limiter._fallback_requests = {}
        limiter._fallback_lock = MagicMock()
        limiter._sqlite_limiter = None
        limiter._redis_healthy = True
        limiter._redis_alert_sent = False

        result = limiter._check_redis_rate_limit("user1", "default", 100, 60)
        assert result is False

    @patch("api.middleware.auth.system_config")
    def test_redis_recovery_clears_flag(self, mock_cfg):
        mock_cfg.REDIS_ENABLED = True
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter.__new__(RedisRateLimiter)
        limiter.limits = {"default": (100, 60)}
        limiter._redis_healthy = False
        limiter._redis_alert_sent = True

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [1]
        limiter._redis = MagicMock()
        limiter._redis.pipeline.return_value = mock_pipe

        result = limiter._check_redis_rate_limit("user1", "default", 100, 60)
        assert result is True
        assert limiter._redis_healthy is True


class TestClassifierStateMachine:
    """Stage 4 classifier state machine and recovery."""

    def test_classify_returns_none_when_degraded(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._available = False
        clf._client = None
        clf._state = "degraded"
        result = clf.classify("test input")
        assert result is None

    def test_classify_blocks_and_transitions_on_error(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._available = True
        clf._state = "available"
        clf._model = "test-model"
        clf._state_since = datetime.now(timezone.utc)

        mock_client = MagicMock()
        mock_client.generate.side_effect = Exception("connection lost")
        clf._client = mock_client
        clf._OllamaError = Exception

        result = clf.classify("test input")
        assert result is not None
        assert result.is_safe is False
        assert clf._state == "degraded"

    @patch("core.email_service.email_service.send_operator_alert")
    def test_transition_to_degraded_sends_alert(self, mock_alert):
        mock_alert.return_value = (True, None)
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "available"
        clf._state_since = datetime.now(timezone.utc)
        clf._available = True

        clf._transition_state("degraded")
        assert clf._state == "degraded"
        assert clf._available is False
        mock_alert.assert_called_once()

    @patch("core.email_service.email_service.send_operator_alert")
    def test_transition_to_available_sends_recovery_alert(self, mock_alert):
        mock_alert.return_value = (True, None)
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "degraded"
        clf._state_since = datetime.now(timezone.utc)
        clf._available = False
        clf._model = "test-model"

        clf._transition_state("available")
        assert clf._state == "available"
        assert clf._available is True
        mock_alert.assert_called_once()
        call_str = str(mock_alert.call_args)
        assert "recover" in call_str.lower()

    def test_probe_returns_true_when_model_available(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._model = "llama-guard3:8b"
        mock_client = MagicMock()
        mock_client.check_connection.return_value = (True, "0.1")
        mock_client.list_models.return_value = (
            True, [{"name": "llama-guard3:8b"}], None,
        )
        clf._client = mock_client
        assert clf._probe_ollama() is True

    def test_probe_returns_false_when_unreachable(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._model = "llama-guard3:8b"
        mock_client = MagicMock()
        mock_client.check_connection.return_value = (False, None)
        clf._client = mock_client
        assert clf._probe_ollama() is False

    def test_no_op_transition_same_state(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "disabled"
        clf._state_since = datetime.now(timezone.utc)
        original_since = clf._state_since

        clf._transition_state("disabled")
        assert clf._state_since == original_since  # Unchanged

    def test_state_and_since_exposed(self):
        from safety.pipeline import _SemanticClassifier

        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "available"
        clf._state_since = datetime(2026, 4, 13, tzinfo=timezone.utc)
        assert clf._state == "available"
        assert clf._state_since.year == 2026


class TestHealthEndpoint:
    """Health endpoint reports rate limiter and classifier state."""

    def test_health_includes_operational_fields(self):
        from fastapi.testclient import TestClient
        from api.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        data = resp.json()
        assert "rate_limiter" in data
        assert data["rate_limiter"] in ("redis", "sqlite", "memory")
        assert "safety_classifier" in data
        assert data["safety_classifier"] in ("available", "degraded", "disabled")
        assert "safety_classifier_since" in data
