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
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERNAL_API_KEY_PREVIOUS", None)
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_PREVIOUS is None

    def test_previous_key_reads_from_env(self):
        with patch.dict(
            os.environ, {"INTERNAL_API_KEY_PREVIOUS": "old-key-abc123"}, clear=False
        ):
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_PREVIOUS == "old-key-abc123"

    def test_max_age_days_defaults_to_90(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERNAL_API_KEY_MAX_AGE_DAYS", None)
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_MAX_AGE_DAYS == 90

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


class TestOWUMiddlewareKeyDefault:
    """OWU middleware must not use a hardcoded insecure default key."""

    def test_no_hardcoded_default_key(self):
        with open(
            "frontend/open-webui/backend/open_webui/middleware/snflwr.py"
        ) as f:
            content = f.read()

        assert "snflwr-internal-dev-key" not in content, (
            "Hardcoded insecure default key still present in OWU middleware"
        )
