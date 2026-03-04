"""
Tests for COPPA-mandated parent email notification system.
Verifies safety alerts are queued/sent, templates render correctly,
retry logic works, and disabled alerts are no-ops.
"""

import smtplib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock
from queue import Queue

import pytest

from utils.email_alerts import EmailAlertSystem, EmailConfig, EmailTemplate


# ---------------------------------------------------------------------------
# EmailConfig
# ---------------------------------------------------------------------------


class TestEmailConfig:
    def test_has_expected_attributes(self):
        cfg = EmailConfig()
        assert hasattr(cfg, "SMTP_HOST")
        assert hasattr(cfg, "SMTP_PORT")
        assert hasattr(cfg, "ENABLE_EMAIL_ALERTS")
        assert isinstance(cfg.SMTP_PORT, int)

    def test_load_from_env(self):
        # Save originals
        orig_host = EmailConfig.SMTP_HOST
        orig_port = EmailConfig.SMTP_PORT
        orig_enabled = EmailConfig.ENABLE_EMAIL_ALERTS
        orig_user = EmailConfig.SMTP_USERNAME
        orig_pass = EmailConfig.SMTP_PASSWORD

        env = {
            "SMTP_HOST": "mail.example.com",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "user@example.com",
            "SMTP_PASSWORD": "secret",
            "FROM_EMAIL": "alerts@example.com",
            "FROM_NAME": "Test Alerts",
            "ENABLE_EMAIL_ALERTS": "true",
        }
        try:
            with patch.dict("os.environ", env, clear=False):
                EmailConfig.load_from_env()
            assert EmailConfig.SMTP_HOST == "mail.example.com"
            assert EmailConfig.SMTP_PORT == 465
            assert EmailConfig.ENABLE_EMAIL_ALERTS is True
        finally:
            # Restore originals
            EmailConfig.SMTP_HOST = orig_host
            EmailConfig.SMTP_PORT = orig_port
            EmailConfig.ENABLE_EMAIL_ALERTS = orig_enabled
            EmailConfig.SMTP_USERNAME = orig_user
            EmailConfig.SMTP_PASSWORD = orig_pass


# ---------------------------------------------------------------------------
# EmailTemplate rendering
# ---------------------------------------------------------------------------


class TestEmailTemplates:
    def test_critical_incident_template(self):
        subject, body = EmailTemplate.safety_incident_critical(
            "Alice", "harmful_content", 42, "2026-01-15 10:30:00"
        )
        assert "URGENT" in subject
        assert "Alice" in subject
        assert "Alice" in body
        assert "harmful_content" in body
        assert "#42" in body
        assert "2026-01-15 10:30:00" in body

    def test_major_incident_template(self):
        subject, body = EmailTemplate.safety_incident_major(
            "Bob", 3, ["profanity", "violence", "self-harm"], 7
        )
        assert "Important" in subject
        assert "Bob" in body
        assert "3" in body
        assert "profanity" in body

    def test_major_incident_truncates_types(self):
        types = ["type1", "type2", "type3", "type4", "type5"]
        _, body = EmailTemplate.safety_incident_major("Child", 5, types, 30)
        # Should show up to 3 types
        assert "type1" in body
        assert "type3" in body

    def test_daily_digest_template(self):
        summary = {"total_sessions": 5, "total_questions": 20, "incidents": 0}
        subject, body = EmailTemplate.daily_digest("Parent", summary)
        assert "Daily" in subject
        assert "Parent" in body
        assert "5" in body
        assert "20" in body

    def test_system_error_template(self):
        subject, body = EmailTemplate.system_error_alert("DB connection timeout", 12)
        assert "12" in subject
        assert "DB connection timeout" in body


# ---------------------------------------------------------------------------
# EmailAlertSystem — initialization and worker lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_system():
    with patch("utils.email_alerts.db_manager"):
        system = EmailAlertSystem.__new__(EmailAlertSystem)
        system.config = EmailConfig()
        system.config.ENABLE_EMAIL_ALERTS = True
        system.db = MagicMock()
        system.email_queue = Queue()
        system.worker_thread = None
        system.running = False
    return system


class TestAlertSystemLifecycle:
    def test_start_worker(self, alert_system):
        alert_system.start_worker()
        assert alert_system.running is True
        assert alert_system.worker_thread is not None
        alert_system.stop_worker()

    def test_start_worker_idempotent(self, alert_system):
        alert_system.start_worker()
        first_thread = alert_system.worker_thread
        alert_system.start_worker()
        assert alert_system.worker_thread is first_thread
        alert_system.stop_worker()

    def test_stop_when_not_running(self, alert_system):
        alert_system.stop_worker()  # should not raise


# ---------------------------------------------------------------------------
# send_safety_alert — COPPA parent notification
# ---------------------------------------------------------------------------


class TestSendSafetyAlert:
    def test_queues_critical_alert(self, alert_system):
        alert_system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Alice",
            incident_type="self_harm",
            severity="critical",
            incident_id=99,
        )
        assert not alert_system.email_queue.empty()
        email = alert_system.email_queue.get()
        assert email["to_email"] == "parent@test.com"
        assert email["priority"] == "high"
        assert "URGENT" in email["subject"]

    def test_queues_major_alert(self, alert_system):
        alert_system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Bob",
            incident_type="profanity",
            severity="major",
            incident_id=100,
        )
        email = alert_system.email_queue.get()
        assert email["priority"] == "normal"

    def test_skips_when_disabled(self, alert_system):
        alert_system.config.ENABLE_EMAIL_ALERTS = False
        alert_system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Alice",
            incident_type="test",
            severity="critical",
            incident_id=1,
        )
        assert alert_system.email_queue.empty()


# ---------------------------------------------------------------------------
# send_daily_digest
# ---------------------------------------------------------------------------


class TestSendDailyDigest:
    def test_queues_digest(self, alert_system):
        alert_system.send_daily_digest(
            "parent@test.com", "Parent Name", {"total_sessions": 3, "total_questions": 10, "incidents": 0}
        )
        email = alert_system.email_queue.get()
        assert email["priority"] == "low"
        assert "Daily" in email["subject"]

    def test_skips_when_disabled(self, alert_system):
        alert_system.config.ENABLE_EMAIL_ALERTS = False
        alert_system.send_daily_digest("p@t.com", "P", {})
        assert alert_system.email_queue.empty()


# ---------------------------------------------------------------------------
# send_error_alert
# ---------------------------------------------------------------------------


class TestSendErrorAlert:
    def test_queues_error_alert(self, alert_system):
        alert_system.send_error_alert("admin@test.com", "DB timeout", 5)
        email = alert_system.email_queue.get()
        assert email["priority"] == "high"

    def test_skips_when_disabled(self, alert_system):
        alert_system.config.ENABLE_EMAIL_ALERTS = False
        alert_system.send_error_alert("admin@test.com", "test", 1)
        assert alert_system.email_queue.empty()


# ---------------------------------------------------------------------------
# _send_email_with_retry
# ---------------------------------------------------------------------------


class TestSendEmailWithRetry:
    def test_succeeds_on_first_try(self, alert_system):
        with patch.object(alert_system, "_send_email", return_value=True):
            result = alert_system._send_email_with_retry(
                {"to_email": "t@t.com", "subject": "s", "body": "b", "is_html": True}
            )
        assert result is True

    def test_retries_on_smtp_error(self, alert_system):
        alert_system.config.MAX_RETRIES = 3
        alert_system.config.RETRY_DELAY = 0  # no actual sleep in tests
        with patch.object(
            alert_system, "_send_email",
            side_effect=[smtplib.SMTPException("fail"), smtplib.SMTPException("fail"), True],
        ) as mock_send, patch("utils.email_alerts.time.sleep"):
            result = alert_system._send_email_with_retry(
                {"to_email": "t@t.com", "subject": "s", "body": "b"}
            )
        assert result is True
        assert mock_send.call_count == 3

    def test_gives_up_after_max_retries(self, alert_system):
        alert_system.config.MAX_RETRIES = 2
        alert_system.config.RETRY_DELAY = 0
        with patch.object(
            alert_system, "_send_email",
            side_effect=smtplib.SMTPException("fail"),
        ), patch("utils.email_alerts.time.sleep"):
            result = alert_system._send_email_with_retry(
                {"to_email": "t@t.com", "subject": "s", "body": "b"}
            )
        assert result is False


# ---------------------------------------------------------------------------
# _send_email — SMTP delivery
# ---------------------------------------------------------------------------


class TestSendEmail:
    def test_sends_html_via_tls(self, alert_system):
        alert_system.config.SMTP_USE_SSL = False
        alert_system.config.SMTP_USE_TLS = True
        alert_system.config.SMTP_USERNAME = "user"
        alert_system.config.SMTP_PASSWORD = "pass"

        mock_server = MagicMock()
        with patch("utils.email_alerts.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            result = alert_system._send_email("to@test.com", "Subject", "<p>HTML</p>", is_html=True)

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.sendmail.assert_called_once()

    def test_sends_via_ssl(self, alert_system):
        alert_system.config.SMTP_USE_SSL = True
        alert_system.config.SMTP_USERNAME = ""
        alert_system.config.SMTP_PASSWORD = ""

        mock_server = MagicMock()
        with patch("utils.email_alerts.smtplib.SMTP_SSL") as mock_smtp_ssl, \
             patch("utils.email_alerts.ssl.create_default_context"):
            mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)
            result = alert_system._send_email("to@test.com", "Subject", "plain", is_html=False)

        assert result is True
        mock_server.login.assert_not_called()  # no credentials

    def test_raises_on_smtp_error(self, alert_system):
        alert_system.config.SMTP_USE_SSL = False
        alert_system.config.SMTP_USE_TLS = False
        alert_system.config.SMTP_USERNAME = ""
        alert_system.config.SMTP_PASSWORD = ""

        with patch("utils.email_alerts.smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            mock_server.sendmail.side_effect = smtplib.SMTPException("refused")
            with pytest.raises(smtplib.SMTPException):
                alert_system._send_email("to@test.com", "Subject", "body")


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    def test_success_tls(self, alert_system):
        alert_system.config.SMTP_USE_SSL = False
        alert_system.config.SMTP_USE_TLS = True
        alert_system.config.SMTP_USERNAME = ""
        alert_system.config.SMTP_PASSWORD = ""

        mock_server = MagicMock()
        with patch("utils.email_alerts.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            assert alert_system.test_connection() is True

    def test_failure(self, alert_system):
        alert_system.config.SMTP_USE_SSL = False
        alert_system.config.SMTP_USE_TLS = False
        with patch("utils.email_alerts.smtplib.SMTP", side_effect=ConnectionError("refused")):
            assert alert_system.test_connection() is False
