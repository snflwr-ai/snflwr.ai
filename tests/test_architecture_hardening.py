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
