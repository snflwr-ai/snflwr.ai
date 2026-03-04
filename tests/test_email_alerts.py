# tests/test_email_alerts.py
"""
Tests for COPPA-required parent email alert system.
Verifies safety incident notifications, queue management, and retry logic.
"""

import smtplib
from unittest.mock import patch, MagicMock, PropertyMock
from queue import Queue

import pytest


class TestEmailConfig:
    """Test email configuration loading"""

    def test_default_values(self):
        from utils.email_alerts import EmailConfig
        assert EmailConfig.SMTP_PORT == 587
        assert EmailConfig.ENABLE_EMAIL_ALERTS is False
        assert EmailConfig.MAX_RETRIES == 3

    @patch.dict("os.environ", {
        "SMTP_HOST": "mail.test.com",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "user@test.com",
        "SMTP_PASSWORD": "secret",
        "ENABLE_EMAIL_ALERTS": "true",
    })
    def test_load_from_env(self):
        from utils.email_alerts import EmailConfig
        cfg = EmailConfig()
        cfg.load_from_env()
        assert cfg.SMTP_HOST == "mail.test.com"
        assert cfg.SMTP_PORT == 465
        assert cfg.SMTP_USERNAME == "user@test.com"
        assert cfg.ENABLE_EMAIL_ALERTS is True

    @patch.dict("os.environ", {"ENABLE_EMAIL_ALERTS": "false"})
    def test_enable_alerts_false(self):
        from utils.email_alerts import EmailConfig
        cfg = EmailConfig()
        cfg.load_from_env()
        assert cfg.ENABLE_EMAIL_ALERTS is False


class TestEmailTemplates:
    """Test email template generation"""

    def test_critical_incident_template(self):
        from utils.email_alerts import EmailTemplate
        subject, body = EmailTemplate.safety_incident_critical(
            child_name="Alice",
            incident_type="harmful_content",
            incident_id=42,
            timestamp="2026-01-15 10:30:00"
        )
        assert "URGENT" in subject
        assert "Alice" in subject
        assert "Alice" in body
        assert "harmful_content" in body
        assert "#42" in body

    def test_major_incident_template(self):
        from utils.email_alerts import EmailTemplate
        subject, body = EmailTemplate.safety_incident_major(
            child_name="Bob",
            incident_count=3,
            incident_types=["bullying", "profanity", "violence"],
            period_days=7
        )
        assert "Important" in subject or "Safety" in subject
        assert "Bob" in body
        assert "3" in body
        assert "bullying" in body

    def test_major_template_truncates_types(self):
        """Only show up to 3 incident types"""
        from utils.email_alerts import EmailTemplate
        _, body = EmailTemplate.safety_incident_major(
            child_name="Test",
            incident_count=5,
            incident_types=["a", "b", "c", "d", "e"],
            period_days=1
        )
        # Should show first 3
        assert "a" in body
        assert "b" in body
        assert "c" in body

    def test_daily_digest_template(self):
        from utils.email_alerts import EmailTemplate
        subject, body = EmailTemplate.daily_digest(
            parent_name="Jane",
            summary_data={
                'total_sessions': 5,
                'total_questions': 42,
                'incidents': 1
            }
        )
        assert "Daily" in subject
        assert "Jane" in body
        assert "5" in body  # sessions
        assert "42" in body  # questions

    def test_system_error_template(self):
        from utils.email_alerts import EmailTemplate
        subject, body = EmailTemplate.system_error_alert(
            error_summary="Database timeout x5",
            error_count=5
        )
        assert "5" in subject
        assert "Database timeout x5" in body


class TestEmailAlertSystem:
    """Test the email alert system lifecycle and queueing"""

    @patch("utils.email_alerts.db_manager")
    def test_init(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        assert system.running is False
        assert isinstance(system.email_queue, Queue)

    @patch("utils.email_alerts.db_manager")
    def test_start_stop_worker(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.start_worker()
        assert system.running is True
        assert system.worker_thread is not None
        system.stop_worker()
        assert system.running is False

    @patch("utils.email_alerts.db_manager")
    def test_start_worker_idempotent(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.start_worker()
        first = system.worker_thread
        system.start_worker()
        assert system.worker_thread is first
        system.stop_worker()


class TestSendSafetyAlert:
    """COPPA: Verify safety alerts are queued correctly"""

    @patch("utils.email_alerts.db_manager")
    def test_alert_disabled_skips(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = False
        system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Alice",
            incident_type="violence",
            severity="critical",
            incident_id=1
        )
        assert system.email_queue.empty()

    @patch("utils.email_alerts.db_manager")
    def test_critical_alert_queued_high_priority(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = True
        system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Alice",
            incident_type="self_harm",
            severity="critical",
            incident_id=99
        )
        assert not system.email_queue.empty()
        email_data = system.email_queue.get()
        assert email_data['to_email'] == "parent@test.com"
        assert email_data['priority'] == 'high'
        assert email_data['is_html'] is True
        assert "URGENT" in email_data['subject']

    @patch("utils.email_alerts.db_manager")
    def test_major_alert_queued_normal_priority(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = True
        system.send_safety_alert(
            parent_email="parent@test.com",
            child_name="Bob",
            incident_type="bullying",
            severity="major",
            incident_id=50
        )
        email_data = system.email_queue.get()
        assert email_data['priority'] == 'normal'


class TestSendDailyDigest:
    @patch("utils.email_alerts.db_manager")
    def test_digest_disabled_skips(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = False
        system.send_daily_digest("p@t.com", "Parent", {'total_sessions': 1})
        assert system.email_queue.empty()

    @patch("utils.email_alerts.db_manager")
    def test_digest_queued(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = True
        system.send_daily_digest("p@t.com", "Parent", {'total_sessions': 3, 'total_questions': 10, 'incidents': 0})
        email_data = system.email_queue.get()
        assert email_data['priority'] == 'low'
        assert "Daily" in email_data['subject']


class TestSendErrorAlert:
    @patch("utils.email_alerts.db_manager")
    def test_error_alert_disabled_skips(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = False
        system.send_error_alert("admin@t.com", "DB error", 5)
        assert system.email_queue.empty()

    @patch("utils.email_alerts.db_manager")
    def test_error_alert_queued_high_priority(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = True
        system.send_error_alert("admin@t.com", "DB timeout", 10)
        email_data = system.email_queue.get()
        assert email_data['priority'] == 'high'


class TestSendEmailWithRetry:
    """Test SMTP retry logic"""

    @patch("utils.email_alerts.db_manager")
    def test_success_on_first_attempt(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.ENABLE_EMAIL_ALERTS = True
        with patch.object(system, '_send_email', return_value=True) as mock_send:
            result = system._send_email_with_retry({
                'to_email': 'test@test.com',
                'subject': 'Test',
                'body': '<p>Hi</p>',
                'is_html': True
            })
            assert result is True
            assert mock_send.call_count == 1

    @patch("utils.email_alerts.db_manager")
    def test_retries_on_smtp_error(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.MAX_RETRIES = 3
        system.config.RETRY_DELAY = 0  # Don't actually wait in tests
        with patch.object(system, '_send_email', side_effect=smtplib.SMTPException("fail")):
            with patch("utils.email_alerts.time.sleep"):
                result = system._send_email_with_retry({
                    'to_email': 'test@test.com',
                    'subject': 'Test',
                    'body': 'body',
                })
                assert result is False

    @patch("utils.email_alerts.db_manager")
    def test_retries_then_succeeds(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.MAX_RETRIES = 3
        system.config.RETRY_DELAY = 0
        with patch.object(system, '_send_email', side_effect=[
            smtplib.SMTPException("fail"),
            True
        ]):
            with patch("utils.email_alerts.time.sleep"):
                result = system._send_email_with_retry({
                    'to_email': 'test@test.com',
                    'subject': 'Test',
                    'body': 'body',
                })
                assert result is True


class TestSendEmail:
    """Test actual SMTP sending (mocked)"""

    @patch("utils.email_alerts.db_manager")
    def test_send_html_email_tls(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.SMTP_USE_SSL = False
        system.config.SMTP_USE_TLS = True
        system.config.SMTP_USERNAME = "user"
        system.config.SMTP_PASSWORD = "pass"
        with patch("utils.email_alerts.smtplib.SMTP") as MockSMTP:
            mock_server = MagicMock()
            MockSMTP.return_value.__enter__ = MagicMock(return_value=mock_server)
            MockSMTP.return_value.__exit__ = MagicMock(return_value=False)
            result = system._send_email("to@test.com", "Subject", "<p>Body</p>", is_html=True)
            assert result is True
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("user", "pass")
            mock_server.sendmail.assert_called_once()

    @patch("utils.email_alerts.db_manager")
    def test_send_email_ssl(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.SMTP_USE_SSL = True
        system.config.SMTP_USERNAME = "user"
        system.config.SMTP_PASSWORD = "pass"
        with patch("utils.email_alerts.smtplib.SMTP_SSL") as MockSSL:
            mock_server = MagicMock()
            MockSSL.return_value.__enter__ = MagicMock(return_value=mock_server)
            MockSSL.return_value.__exit__ = MagicMock(return_value=False)
            result = system._send_email("to@test.com", "Subject", "Plain body", is_html=False)
            assert result is True

    @patch("utils.email_alerts.db_manager")
    def test_smtp_error_raises(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.SMTP_USE_SSL = False
        system.config.SMTP_USE_TLS = False
        with patch("utils.email_alerts.smtplib.SMTP") as MockSMTP:
            mock_server = MagicMock()
            mock_server.sendmail.side_effect = smtplib.SMTPException("failed")
            MockSMTP.return_value.__enter__ = MagicMock(return_value=mock_server)
            MockSMTP.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(smtplib.SMTPException):
                system._send_email("to@test.com", "Subject", "body")


class TestTestConnection:
    @patch("utils.email_alerts.db_manager")
    def test_successful_connection(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.SMTP_USE_SSL = False
        system.config.SMTP_USE_TLS = True
        system.config.SMTP_USERNAME = "u"
        system.config.SMTP_PASSWORD = "p"
        with patch("utils.email_alerts.smtplib.SMTP") as MockSMTP:
            mock_server = MagicMock()
            MockSMTP.return_value.__enter__ = MagicMock(return_value=mock_server)
            MockSMTP.return_value.__exit__ = MagicMock(return_value=False)
            assert system.test_connection() is True

    @patch("utils.email_alerts.db_manager")
    def test_failed_connection(self, mock_db):
        from utils.email_alerts import EmailAlertSystem
        system = EmailAlertSystem()
        system.config.SMTP_USE_SSL = False
        system.config.SMTP_USE_TLS = False
        with patch("utils.email_alerts.smtplib.SMTP", side_effect=ConnectionError("refused")):
            assert system.test_connection() is False
