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
