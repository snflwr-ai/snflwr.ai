"""
Tests for core/email_service.py — Parent Safety Alert Emails

Covers:
    - _safe_url: URL validation and sanitization
    - EmailTemplate: critical alert, moderate alert, verification, password reset
    - EmailService: send_safety_alert, send_verification_email,
      send_password_reset_email, test_connection, _get_parent_email, _send_email
"""

from unittest.mock import MagicMock, patch, PropertyMock
from html import escape as html_escape

import pytest


# Patch heavy dependencies before importing the module
@pytest.fixture(autouse=True)
def _patch_email_deps():
    """Patch database and email crypto so the module can be imported cleanly."""
    mock_db = MagicMock()
    mock_crypto = MagicMock()
    with patch("core.email_service.db_manager", mock_db), \
         patch("core.email_service.get_email_crypto", return_value=mock_crypto):
        yield {"db": mock_db, "crypto": mock_crypto}


# ==========================================================================
# _safe_url
# ==========================================================================

class TestSafeUrl:

    def test_valid_https_url(self):
        from core.email_service import _safe_url
        result = _safe_url("https://example.com/path")
        assert result == "https://example.com/path"

    def test_valid_http_url(self):
        from core.email_service import _safe_url
        result = _safe_url("http://localhost:8080/test")
        assert "http://localhost:8080/test" in result

    def test_javascript_protocol_blocked(self):
        from core.email_service import _safe_url
        result = _safe_url("javascript:alert(1)")
        assert result == ""

    def test_data_protocol_blocked(self):
        from core.email_service import _safe_url
        result = _safe_url("data:text/html,<h1>XSS</h1>")
        assert result == ""

    def test_empty_string(self):
        from core.email_service import _safe_url
        assert _safe_url("") == ""

    def test_none(self):
        from core.email_service import _safe_url
        assert _safe_url(None) == ""

    def test_html_escape_in_url(self):
        from core.email_service import _safe_url
        result = _safe_url("https://example.com/?a=1&b=2")
        # Ampersand should be escaped for safe HTML embedding
        assert "&amp;" in result

    def test_whitespace_stripped(self):
        from core.email_service import _safe_url
        result = _safe_url("  https://example.com  ")
        assert result.startswith("https://")

    def test_ftp_protocol_blocked(self):
        from core.email_service import _safe_url
        assert _safe_url("ftp://files.example.com") == ""


# ==========================================================================
# EmailTemplate
# ==========================================================================

class TestEmailTemplate:

    def test_critical_alert_subject_and_body(self):
        from core.email_service import EmailTemplate
        subject, body = EmailTemplate.safety_alert_critical(
            parent_name="Jane Doe",
            child_name="Tommy",
            incident_count=3,
            severity="critical",
            description="Inappropriate content detected",
        )
        assert "URGENT" in subject
        assert "Tommy" in subject
        assert "Jane Doe" in body
        assert "CRITICAL" in body
        assert "3" in body

    def test_critical_alert_escapes_xss(self):
        from core.email_service import EmailTemplate
        _, body = EmailTemplate.safety_alert_critical(
            parent_name="<script>alert(1)</script>",
            child_name="<img onerror=hack>",
            incident_count=1,
            severity="high",
            description="Test <b>bold</b>",
        )
        # Raw HTML tags should be escaped
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_critical_alert_with_snippet(self):
        from core.email_service import EmailTemplate
        _, body = EmailTemplate.safety_alert_critical(
            parent_name="Parent",
            child_name="Child",
            incident_count=1,
            severity="critical",
            description="test",
            snippet="some content here",
        )
        assert "some content here" in body
        assert "Conversation Excerpt" in body

    def test_critical_alert_without_snippet(self):
        from core.email_service import EmailTemplate
        _, body = EmailTemplate.safety_alert_critical(
            parent_name="Parent",
            child_name="Child",
            incident_count=1,
            severity="high",
            description="test",
            snippet=None,
        )
        assert "Conversation Excerpt" not in body

    def test_moderate_alert(self):
        from core.email_service import EmailTemplate
        subject, body = EmailTemplate.safety_alert_moderate(
            parent_name="Parent",
            child_name="Child",
            incident_count=2,
            severity="medium",
            description="Mild filter trigger",
        )
        assert "Safety Notice" in subject
        assert "No action required" in body

    def test_email_verification(self):
        from core.email_service import EmailTemplate
        subject, body = EmailTemplate.email_verification(
            user_name="John",
            verification_token="abc123token",
        )
        assert "Verify" in subject
        assert "abc123token" in body
        assert "John" in body

    def test_password_reset(self):
        from core.email_service import EmailTemplate
        subject, body = EmailTemplate.password_reset(
            user_name="Jane",
            reset_token="reset-token-xyz",
        )
        assert "Reset" in subject
        assert "reset-token-xyz" in body


# ==========================================================================
# EmailService
# ==========================================================================

class TestEmailService:

    @pytest.fixture
    def service(self, _patch_email_deps):
        """Create EmailService with mocked dependencies."""
        with patch("core.email_service.system_config") as mock_config:
            mock_config.SMTP_ENABLED = False
            mock_config.SMTP_HOST = "localhost"
            mock_config.SMTP_PORT = 587
            mock_config.BASE_URL = "http://localhost:8080"
            from core.email_service import EmailService
            svc = EmailService()
            svc.db = _patch_email_deps["db"]
            svc.email_crypto = _patch_email_deps["crypto"]
            yield svc

    def test_send_safety_alert_parent_not_found(self, service):
        service.db.execute_query.return_value = []
        service._get_parent_email = MagicMock(return_value=None)
        success, error = service.send_safety_alert(
            "parent1", "Child", "high", 1, "test"
        )
        assert success is False

    def test_send_safety_alert_smtp_disabled(self, service):
        service._get_parent_email = MagicMock(
            return_value=("parent@test.com", "Parent", True)
        )
        service.enabled = False
        success, error = service.send_safety_alert(
            "parent1", "Child", "high", 1, "test"
        )
        # SMTP disabled is not an error — returns True
        assert success is True

    def test_send_safety_alert_email_disabled(self, service):
        service._get_parent_email = MagicMock(
            return_value=("parent@test.com", "Parent", False)
        )
        success, error = service.send_safety_alert(
            "parent1", "Child", "medium", 1, "test"
        )
        assert success is True  # Skipped, not failed

    def test_send_verification_smtp_disabled(self, service):
        service.enabled = False
        success, error = service.send_verification_email(
            "user1", "test@test.com", "User", "token123"
        )
        assert success is True

    def test_send_password_reset_smtp_disabled(self, service):
        service.enabled = False
        success, error = service.send_password_reset_email(
            "user1", "test@test.com", "User", "reset123"
        )
        assert success is True

    def test_test_connection_disabled(self, service):
        service.enabled = False
        success, error = service.test_connection()
        assert success is False
        assert "not enabled" in error.lower()

    def test_send_email_smtp_error(self, service):
        import smtplib
        service.enabled = True
        with patch("core.email_service.smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = smtplib.SMTPException("Connection refused")
            success, error = service._send_email(
                "test@test.com", "Subject", "<p>Body</p>"
            )
            assert success is False
            assert "Connection refused" in error

    def test_get_parent_email_found(self, service):
        service.db.execute_query.return_value = [{
            'encrypted_email': 'encrypted_value',
            'name': 'Jane',
            'email_notifications_enabled': 1,
        }]
        service.email_crypto.decrypt_email.return_value = "jane@test.com"
        result = service._get_parent_email("parent1")
        assert result == ("jane@test.com", "Jane", True)

    def test_get_parent_email_not_found(self, service):
        service.db.execute_query.return_value = []
        result = service._get_parent_email("missing")
        assert result is None

    def test_log_email_attempt(self, service):
        service._log_email_attempt("parent1", "test@test.com", "sent", None)
        service.db.execute_write.assert_called_once()

    def test_log_email_attempt_db_error(self, service):
        import sqlite3
        service.db.execute_write.side_effect = sqlite3.Error("db fail")
        # Should not raise — just log the error
        service._log_email_attempt("parent1", "test@test.com", "failed", "err")
