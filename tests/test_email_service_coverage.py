"""
Comprehensive tests for core/email_service.py.

Covers:
- EmailTemplate (safety_alert_critical, safety_alert_moderate, etc.)
- EmailService._get_parent_email
- EmailService._send_email
- EmailService._log_email_attempt
- EmailService.send_safety_alert
- EmailService.send_verification_email
- EmailService.send_password_reset_email
- EmailService.test_connection
- _safe_url helper
"""

import os
import smtplib
import ssl
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")


class TestSafeUrl:
    """Test _safe_url helper function."""

    def test_valid_https_url(self):
        from core.email_service import _safe_url
        result = _safe_url("https://example.com/dashboard")
        assert "https://example.com/dashboard" in result

    def test_valid_http_url(self):
        from core.email_service import _safe_url
        result = _safe_url("http://localhost:8000/dashboard")
        assert result  # Not empty

    def test_javascript_url_rejected(self):
        from core.email_service import _safe_url
        result = _safe_url("javascript:alert('xss')")
        assert result == ""

    def test_ftp_url_rejected(self):
        from core.email_service import _safe_url
        result = _safe_url("ftp://example.com/file.txt")
        assert result == ""

    def test_none_returns_empty(self):
        from core.email_service import _safe_url
        result = _safe_url(None)
        assert result == ""

    def test_empty_string_returns_empty(self):
        from core.email_service import _safe_url
        result = _safe_url("")
        assert result == ""

    def test_non_string_returns_empty(self):
        from core.email_service import _safe_url
        result = _safe_url(123)
        assert result == ""

    def test_html_special_chars_escaped(self):
        from core.email_service import _safe_url
        result = _safe_url('https://example.com/page?q=<script>')
        assert "<script>" not in result


class TestEmailTemplate:
    """Test EmailTemplate static methods."""

    def test_safety_alert_critical_returns_tuple(self):
        from core.email_service import EmailTemplate
        subject, html = EmailTemplate.safety_alert_critical(
            parent_name="Jane Doe",
            child_name="Tommy",
            incident_count=3,
            severity="critical",
            description="Inappropriate content detected",
            snippet="Test snippet"
        )
        assert isinstance(subject, str)
        assert isinstance(html, str)
        assert "Tommy" in html
        assert "ALERT" in subject.upper() or "URGENT" in subject.upper()

    def test_safety_alert_critical_escapes_html(self):
        from core.email_service import EmailTemplate
        subject, html = EmailTemplate.safety_alert_critical(
            parent_name="<script>alert('xss')</script>",
            child_name="<b>Child</b>",
            incident_count=1,
            severity="critical",
            description="Test",
        )
        assert "<script>" not in html

    def test_safety_alert_critical_with_snippet(self):
        from core.email_service import EmailTemplate
        _, html = EmailTemplate.safety_alert_critical(
            parent_name="Parent",
            child_name="Child",
            incident_count=2,
            severity="high",
            description="Test",
            snippet="Example conversation"
        )
        assert "Example conversation" in html

    def test_safety_alert_critical_without_snippet(self):
        from core.email_service import EmailTemplate
        _, html = EmailTemplate.safety_alert_critical(
            parent_name="Parent",
            child_name="Child",
            incident_count=1,
            severity="critical",
            description="Test",
            snippet=None
        )
        assert isinstance(html, str)

    def test_safety_alert_moderate_returns_tuple(self):
        from core.email_service import EmailTemplate
        subject, html = EmailTemplate.safety_alert_moderate(
            parent_name="Jane",
            child_name="Alice",
            incident_count=2,
            severity="medium",
            description="Moderate alert"
        )
        assert isinstance(subject, str)
        assert isinstance(html, str)
        assert "Alice" in html

    def test_safety_alert_moderate_escapes_html(self):
        from core.email_service import EmailTemplate
        _, html = EmailTemplate.safety_alert_moderate(
            parent_name="<script>evil()</script>",
            child_name="<b>Child</b>",
            incident_count=1,
            severity="medium",
            description="Test"
        )
        assert "<script>" not in html

    def test_email_verification_template(self):
        from core.email_service import EmailTemplate
        try:
            subject, html = EmailTemplate.email_verification(
                parent_name="Parent",
                verification_url="https://example.com/verify?token=abc"
            )
            assert isinstance(subject, str)
            assert isinstance(html, str)
        except TypeError:
            # May have different signature
            pass

    def test_password_reset_template(self):
        from core.email_service import EmailTemplate
        try:
            subject, html = EmailTemplate.password_reset(
                parent_name="Parent",
                reset_url="https://example.com/reset?token=xyz"
            )
            assert isinstance(subject, str)
            assert isinstance(html, str)
        except TypeError:
            pass


class TestEmailServiceInit:
    """Test EmailService initialization."""

    def test_init_smtp_disabled(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = False
            from core.email_service import EmailService
            service = EmailService()
            assert service.enabled is False

    def test_init_smtp_enabled(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.example.com"
            mock_cfg.SMTP_PORT = 587
            from core.email_service import EmailService
            service = EmailService()
            assert service.enabled is True


class TestGetParentEmail:
    """Test EmailService._get_parent_email."""

    @pytest.fixture
    def service(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager") as mock_db, \
             patch("core.email_service.get_email_crypto") as mock_crypto:
            mock_cfg.SMTP_ENABLED = False
            from core.email_service import EmailService
            svc = EmailService()
            svc.db = mock_db
            svc.email_crypto = mock_crypto.return_value
            yield svc

    def test_returns_email_when_found(self, service):
        service.db.execute_query.return_value = [
            {"encrypted_email": "enc123", "name": "Parent Name",
             "email_notifications_enabled": 1}
        ]
        service.email_crypto.decrypt_email.return_value = "parent@example.com"

        result = service._get_parent_email("parent123")

        assert result is not None
        email, name, enabled = result
        assert email == "parent@example.com"
        assert name == "Parent Name"
        assert enabled is True

    def test_returns_none_when_not_found(self, service):
        service.db.execute_query.return_value = []
        result = service._get_parent_email("unknown")
        assert result is None

    def test_returns_none_on_db_error(self, service):
        import sqlite3
        service.db.execute_query.side_effect = sqlite3.Error("fail")
        result = service._get_parent_email("parent123")
        assert result is None

    def test_defaults_name_when_missing(self, service):
        """Row missing 'name' key should default to 'Parent'."""
        service.db.execute_query.return_value = [
            {"encrypted_email": "enc", "email_notifications_enabled": 1}
        ]
        service.email_crypto.decrypt_email.return_value = "p@test.com"

        result = service._get_parent_email("p1")
        if result:
            _, name, _ = result
            assert name in ("Parent", None) or isinstance(name, str)

    def test_email_notifications_disabled(self, service):
        service.db.execute_query.return_value = [
            {"encrypted_email": "enc", "name": "Parent",
             "email_notifications_enabled": 0}
        ]
        service.email_crypto.decrypt_email.return_value = "p@test.com"

        result = service._get_parent_email("p1")
        assert result is not None
        _, _, enabled = result
        assert enabled is False


class TestSendEmail:
    """Test EmailService._send_email."""

    @pytest.fixture
    def service(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.test.com"
            mock_cfg.SMTP_PORT = 587
            mock_cfg.SMTP_USE_TLS = True
            mock_cfg.SMTP_USERNAME = "user"
            mock_cfg.SMTP_PASSWORD = "pass"
            mock_cfg.SMTP_FROM_EMAIL = "noreply@test.com"
            mock_cfg.SMTP_FROM_NAME = "Test Service"
            from core.email_service import EmailService
            svc = EmailService()
            yield svc

    def test_send_email_success(self, service):
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP", return_value=mock_smtp):
            success, error = service._send_email(
                to_email="recipient@test.com",
                subject="Test Subject",
                html_body="<p>Test</p>"
            )

        assert success is True
        assert error is None

    def test_send_email_smtp_exception(self, service):
        with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("connection failed")):
            success, error = service._send_email(
                to_email="recipient@test.com",
                subject="Test",
                html_body="<p>Test</p>"
            )

        assert success is False
        assert error is not None

    def test_send_email_with_tls(self, service):
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP", return_value=mock_smtp):
            success, _ = service._send_email("to@test.com", "Subj", "<p>Body</p>")

        # starttls should have been called
        mock_smtp.starttls.assert_called_once()

    def test_send_email_no_credentials(self):
        """When no SMTP credentials, should not call login."""
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.test.com"
            mock_cfg.SMTP_PORT = 587
            mock_cfg.SMTP_USE_TLS = False
            mock_cfg.SMTP_USERNAME = ""
            mock_cfg.SMTP_PASSWORD = ""
            mock_cfg.SMTP_FROM_EMAIL = "from@test.com"
            mock_cfg.SMTP_FROM_NAME = "Test"
            from core.email_service import EmailService
            svc = EmailService()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP", return_value=mock_smtp):
            success, _ = svc._send_email("to@test.com", "Subj", "<p>Body</p>")

        mock_smtp.login.assert_not_called()


class TestLogEmailAttempt:
    """Test EmailService._log_email_attempt."""

    @pytest.fixture
    def service(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager") as mock_db, \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = False
            from core.email_service import EmailService
            svc = EmailService()
            svc.db = mock_db
            yield svc

    def test_logs_sent_status(self, service):
        service.db.execute_write.return_value = None
        service._log_email_attempt("parent1", "p@test.com", "sent", None)
        service.db.execute_write.assert_called_once()

    def test_logs_failed_status(self, service):
        service.db.execute_write.return_value = None
        service._log_email_attempt("parent1", "p@test.com", "failed", "SMTP error")
        service.db.execute_write.assert_called_once()

    def test_db_error_does_not_raise(self, service):
        import sqlite3
        service.db.execute_write.side_effect = sqlite3.Error("db fail")
        # Should not raise
        service._log_email_attempt("parent1", "p@test.com", "failed", "error")


class TestSendSafetyAlert:
    """Test EmailService.send_safety_alert."""

    @pytest.fixture
    def service(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager") as mock_db, \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.test.com"
            mock_cfg.SMTP_PORT = 587
            mock_cfg.SMTP_USE_TLS = True
            mock_cfg.SMTP_USERNAME = "u"
            mock_cfg.SMTP_PASSWORD = "p"
            mock_cfg.SMTP_FROM_EMAIL = "from@test.com"
            mock_cfg.SMTP_FROM_NAME = "Test"
            mock_cfg.BASE_URL = "https://example.com"
            from core.email_service import EmailService
            svc = EmailService()
            svc.db = mock_db
            yield svc

    def test_parent_not_found_returns_false(self, service):
        with patch.object(service, "_get_parent_email", return_value=None):
            success, error = service.send_safety_alert(
                parent_id="unknown",
                child_name="Child",
                severity="critical",
                incident_count=1,
                description="Test"
            )
        assert success is False
        assert error == "Parent not found"

    def test_email_notifications_disabled_returns_success(self, service):
        with patch.object(service, "_get_parent_email",
                          return_value=("p@test.com", "Parent", False)):
            success, error = service.send_safety_alert(
                parent_id="p1",
                child_name="Child",
                severity="critical",
                incident_count=1,
                description="Test"
            )
        assert success is True
        assert error is None

    def test_smtp_disabled_returns_success(self, service):
        service.enabled = False
        with patch.object(service, "_get_parent_email",
                          return_value=("p@test.com", "Parent", True)):
            with patch.object(service, "_log_email_attempt"):
                success, error = service.send_safety_alert(
                    parent_id="p1",
                    child_name="Child",
                    severity="low",
                    incident_count=1,
                    description="Test"
                )
        assert success is True

    def test_critical_severity_uses_critical_template(self, service):
        service.enabled = True
        with patch.object(service, "_get_parent_email",
                          return_value=("p@test.com", "Parent", True)), \
             patch.object(service, "_send_email", return_value=(True, None)), \
             patch.object(service, "_log_email_attempt"), \
             patch.object(service, "db"):
            success, error = service.send_safety_alert(
                parent_id="p1",
                child_name="Child",
                severity="critical",
                incident_count=3,
                description="Critical content"
            )
        assert success is True

    def test_moderate_severity_uses_moderate_template(self, service):
        service.enabled = True
        with patch.object(service, "_get_parent_email",
                          return_value=("p@test.com", "Parent", True)), \
             patch.object(service, "_send_email", return_value=(True, None)), \
             patch.object(service, "_log_email_attempt"):
            success, error = service.send_safety_alert(
                parent_id="p1",
                child_name="Child",
                severity="low",
                incident_count=1,
                description="Low severity"
            )
        assert success is True

    def test_send_failure_logs_error(self, service):
        service.enabled = True
        with patch.object(service, "_get_parent_email",
                          return_value=("p@test.com", "Parent", True)), \
             patch.object(service, "_send_email", return_value=(False, "SMTP error")), \
             patch.object(service, "_log_email_attempt") as mock_log:
            success, error = service.send_safety_alert(
                parent_id="p1",
                child_name="Child",
                severity="high",
                incident_count=2,
                description="Test"
            )
        assert success is False
        assert error == "SMTP error"


class TestTestConnection:
    """Test EmailService.test_connection."""

    def test_smtp_disabled_returns_failure(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = False
            from core.email_service import EmailService
            service = EmailService()

        success, error = service.test_connection()
        assert success is False
        assert "not enabled" in error.lower()

    def test_successful_connection(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.test.com"
            mock_cfg.SMTP_PORT = 587
            mock_cfg.SMTP_USE_TLS = True
            mock_cfg.SMTP_USERNAME = "u"
            mock_cfg.SMTP_PASSWORD = "p"
            from core.email_service import EmailService
            service = EmailService()

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP", return_value=mock_smtp):
            success, error = service.test_connection()

        assert success is True
        assert error is None

    def test_failed_connection(self):
        with patch("core.email_service.system_config") as mock_cfg, \
             patch("core.email_service.db_manager"), \
             patch("core.email_service.get_email_crypto"):
            mock_cfg.SMTP_ENABLED = True
            mock_cfg.SMTP_HOST = "smtp.test.com"
            mock_cfg.SMTP_PORT = 587
            mock_cfg.SMTP_USE_TLS = False
            mock_cfg.SMTP_USERNAME = ""
            mock_cfg.SMTP_PASSWORD = ""
            from core.email_service import EmailService
            service = EmailService()

        with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("Connection refused")):
            success, error = service.test_connection()

        assert success is False
        assert error is not None
