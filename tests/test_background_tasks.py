"""
Tests for Celery background tasks.

Covers data retention cleanup (COPPA compliance), email sending with
retry logic, user data export/deletion with grace period, and database
maintenance. All external services (DB, SMTP, Redis) are mocked.
"""

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, call

# Celery is optional in dev — skip if not installed
pytest.importorskip("celery")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Mock db_manager with default returns."""
    db = MagicMock()
    db.execute_write.return_value = 0
    db.execute_read.return_value = []
    return db


@pytest.fixture
def mock_email():
    """Mock email_service."""
    svc = MagicMock()
    svc.send_email.return_value = True
    return svc


# ---------------------------------------------------------------------------
# CLEANUP TASKS — COPPA Data Retention
# ---------------------------------------------------------------------------

class TestCleanupOldMessages:

    @patch("tasks.background_tasks.db_manager")
    def test_deletes_messages_older_than_180_days(self, mock_db):
        from tasks.background_tasks import cleanup_old_messages
        mock_db.execute_write.return_value = 42

        result = cleanup_old_messages()

        assert result == 42
        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM messages" in sql
        assert "180" in sql

    @patch("tasks.background_tasks.db_manager")
    def test_returns_zero_on_db_error(self, mock_db):
        import sqlite3
        from tasks.background_tasks import cleanup_old_messages
        mock_db.execute_write.side_effect = sqlite3.OperationalError("disk I/O")

        result = cleanup_old_messages()

        assert result == 0


class TestCleanupOldSessions:

    @patch("tasks.background_tasks.db_manager")
    def test_deletes_expired_and_invalid_tokens(self, mock_db):
        from tasks.background_tasks import cleanup_old_sessions
        mock_db.execute_write.return_value = 15

        result = cleanup_old_sessions()

        assert result == 15
        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM auth_tokens" in sql
        assert "expires_at" in sql
        assert "is_valid = 0" in sql

    @patch("tasks.background_tasks.db_manager")
    def test_returns_zero_on_db_error(self, mock_db):
        import sqlite3
        from tasks.background_tasks import cleanup_old_sessions
        mock_db.execute_write.side_effect = sqlite3.OperationalError("locked")

        result = cleanup_old_sessions()

        assert result == 0


class TestCleanupOldIncidents:

    @patch("tasks.background_tasks.db_manager")
    def test_only_deletes_resolved_incidents(self, mock_db):
        """COPPA: only resolved incidents should be purged."""
        from tasks.background_tasks import cleanup_old_incidents
        mock_db.execute_write.return_value = 7

        result = cleanup_old_incidents()

        assert result == 7
        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM safety_incidents" in sql
        assert "resolved = 1" in sql
        assert "90" in sql

    @patch("tasks.background_tasks.db_manager")
    def test_returns_zero_on_db_error(self, mock_db):
        import sqlite3
        from tasks.background_tasks import cleanup_old_incidents
        mock_db.execute_write.side_effect = sqlite3.OperationalError("err")

        result = cleanup_old_incidents()

        assert result == 0


class TestCleanupAuditLogs:

    @patch("storage.database.db_manager")
    def test_uses_configured_retention_days(self, mock_db):
        from tasks.background_tasks import cleanup_audit_logs
        mock_db.execute_write.return_value = 100

        result = cleanup_audit_logs()

        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM audit_log" in sql
        assert "365" in sql  # default retention


class TestCleanupEndedSessions:

    @patch("storage.database.db_manager")
    def test_only_deletes_ended_sessions(self, mock_db):
        from tasks.background_tasks import cleanup_ended_sessions
        mock_db.execute_write.return_value = 20

        result = cleanup_ended_sessions()

        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM sessions" in sql
        assert "ended_at IS NOT NULL" in sql
        assert "180" in sql


class TestCleanupAnalytics:

    @patch("storage.database.db_manager")
    def test_uses_730_day_retention(self, mock_db):
        from tasks.background_tasks import cleanup_analytics
        mock_db.execute_write.return_value = 5

        result = cleanup_analytics()

        sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM learning_analytics" in sql
        assert "730" in sql


class TestVacuumDatabase:

    @patch("storage.database.db_manager")
    def test_sqlite_runs_vacuum(self, mock_db):
        from tasks.background_tasks import vacuum_database
        mock_db.execute_write.return_value = None

        result = vacuum_database()

        assert result is True
        mock_db.execute_write.assert_called_once_with("VACUUM")

    def test_postgresql_skips_vacuum(self):
        """PostgreSQL uses autovacuum — VACUUM should be skipped."""
        from tasks.background_tasks import vacuum_database
        with patch("storage.database.db_manager") as mock_db:
            with patch("config.system_config") as mock_cfg:
                mock_cfg.DB_TYPE = "postgresql"
                result = vacuum_database()

        assert result is True
        mock_db.execute_write.assert_not_called()


# ---------------------------------------------------------------------------
# EMAIL TASKS
# ---------------------------------------------------------------------------

class TestSendEmail:

    @patch("tasks.background_tasks.email_service")
    def test_successful_send(self, mock_svc):
        from tasks.background_tasks import send_email
        mock_svc.send_email.return_value = True

        result = send_email("parent@test.com", "Subject", "<p>Hello</p>")

        assert result is True
        mock_svc.send_email.assert_called_once_with(
            to_email="parent@test.com",
            subject="Subject",
            html_content="<p>Hello</p>",
            text_content=None,
        )

    @patch("tasks.background_tasks.email_service")
    def test_failure_triggers_retry(self, mock_svc):
        from tasks.background_tasks import send_email
        mock_svc.send_email.return_value = False

        # Celery's self.retry raises Retry exception
        with pytest.raises(Exception):
            send_email("parent@test.com", "Subject", "<p>Hi</p>")


class TestSendSafetyAlert:

    @patch("tasks.background_tasks.send_email")
    def test_html_escapes_user_input(self, mock_send):
        """XSS prevention: user-controlled values must be HTML-escaped."""
        from tasks.background_tasks import send_safety_alert
        mock_send.return_value = True

        # Inject XSS in child_name
        send_safety_alert(
            parent_email="p@test.com",
            child_name='<script>alert("xss")</script>',
            incident_type="test",
            severity="CRITICAL",
            timestamp="2024-01-01",
            details="some details",
        )

        # The html_content passed to send_email should have escaped the script tag
        call_args = mock_send.call_args
        html = call_args[1].get("html_content") or call_args[0][2]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    @patch("tasks.background_tasks.send_email")
    def test_severity_colors(self, mock_send):
        """Alert email uses different colors for different severities."""
        from tasks.background_tasks import send_safety_alert
        mock_send.return_value = True

        send_safety_alert("p@test.com", "Child", "test", "CRITICAL", "2024-01-01", "d")
        html = mock_send.call_args[0][2] if len(mock_send.call_args[0]) > 2 else mock_send.call_args[1]["html_content"]
        assert "#d63031" in html  # critical red


class TestSendDailyDigests:

    @patch("tasks.background_tasks.send_email")
    @patch("tasks.background_tasks.get_email_crypto")
    @patch("tasks.background_tasks.db_manager")
    def test_sends_digest_to_parents_with_incidents(self, mock_db, mock_crypto_fn, mock_send):
        from tasks.background_tasks import send_daily_safety_digests

        crypto = MagicMock()
        crypto.decrypt_email.return_value = "parent@test.com"
        mock_crypto_fn.return_value = crypto

        mock_db.execute_read.side_effect = [
            # First call: parents with unresolved incidents
            [{"encrypted_email": "enc_email", "parent_id": "p1"}],
            # Second call: incidents for parent p1
            [{"child_name": "Tommy", "incident_type": "prohibited", "timestamp": "2024-01-01"}],
        ]

        result = send_daily_safety_digests()

        assert result == 1
        mock_send.delay.assert_called_once()
        # Subject should mention incident count
        subject = mock_send.delay.call_args[0][1]
        assert "1 incidents" in subject or "1 incident" in subject

    @patch("tasks.background_tasks.get_email_crypto")
    @patch("tasks.background_tasks.db_manager")
    def test_no_incidents_sends_zero_digests(self, mock_db, mock_crypto_fn):
        from tasks.background_tasks import send_daily_safety_digests

        mock_db.execute_read.return_value = []  # No parents with incidents

        result = send_daily_safety_digests()

        assert result == 0


# ---------------------------------------------------------------------------
# DATA EXPORT / DELETE — COPPA Right to Deletion
# ---------------------------------------------------------------------------

class TestExportUserData:

    @patch("tasks.background_tasks.db_manager")
    def test_exports_all_user_data_to_json(self, mock_db, tmp_path):
        from tasks.background_tasks import export_user_data

        mock_db.execute_read.side_effect = [
            # User account
            [{"parent_id": "u1", "username": "parent1"}],
            # Child profiles
            [{"profile_id": "p1", "name": "Child1"}],
            # Messages for profile p1
            [{"message_id": "m1", "content": "hello"}],
            # Incidents for profile p1
            [{"incident_id": 1, "incident_type": "test"}],
        ]

        with patch("tasks.background_tasks.system_config") as mock_cfg:
            mock_cfg.APP_DATA_DIR = tmp_path
            result = export_user_data("u1")

        assert result is not None
        assert result.endswith(".json")
        # Verify file was written with correct structure
        with open(result) as f:
            data = json.load(f)
        assert data["user"]["parent_id"] == "u1"
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["profile"]["name"] == "Child1"

    @patch("tasks.background_tasks.db_manager")
    def test_returns_none_on_db_error(self, mock_db):
        import sqlite3
        from tasks.background_tasks import export_user_data
        mock_db.execute_read.side_effect = sqlite3.OperationalError("err")

        result = export_user_data("u1")

        assert result is None


class TestDeleteUserData:

    @patch("tasks.background_tasks.db_manager")
    def test_first_call_marks_for_deletion(self, mock_db):
        """First call should mark the account, not delete yet."""
        from tasks.background_tasks import delete_user_data

        mock_db.execute_read.side_effect = [
            # User exists, no deletion_requested_at
            [{"deletion_requested_at": None}],
        ]

        result = delete_user_data("u1", grace_period_days=30)

        assert result is False  # Not deleted yet, just marked
        # Should have set deletion_requested_at
        write_sql = mock_db.execute_write.call_args[0][0]
        assert "deletion_requested_at" in write_sql

    @patch("tasks.background_tasks.db_manager")
    def test_within_grace_period_not_deleted(self, mock_db):
        """During grace period, data should NOT be deleted."""
        from tasks.background_tasks import delete_user_data

        # Requested 5 days ago, grace period is 30 days
        requested_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        mock_db.execute_read.side_effect = [
            [{"deletion_requested_at": requested_at}],
        ]

        result = delete_user_data("u1", grace_period_days=30)

        assert result is False
        mock_db.execute_write.assert_not_called()

    @patch("tasks.background_tasks.db_manager")
    def test_after_grace_period_deletes_everything(self, mock_db):
        """After grace period, all user data should be permanently deleted."""
        from tasks.background_tasks import delete_user_data

        # Requested 31 days ago, grace period is 30 days
        requested_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        mock_db.execute_read.side_effect = [
            # First: deletion request check
            [{"deletion_requested_at": requested_at}],
            # Second: child profiles
            [{"profile_id": "p1"}, {"profile_id": "p2"}],
        ]

        result = delete_user_data("u1", grace_period_days=30)

        assert result is True
        # Should delete: messages, sessions, incidents (per profile), profiles, tokens, account
        write_calls = [c[0][0] for c in mock_db.execute_write.call_args_list]
        assert any("DELETE FROM messages" in s for s in write_calls)
        assert any("DELETE FROM sessions" in s for s in write_calls)
        assert any("DELETE FROM safety_incidents" in s for s in write_calls)
        assert any("DELETE FROM child_profiles" in s for s in write_calls)
        assert any("DELETE FROM auth_tokens" in s for s in write_calls)
        assert any("DELETE FROM accounts" in s for s in write_calls)

    @patch("tasks.background_tasks.db_manager")
    def test_cascade_deletes_all_profiles(self, mock_db):
        """Deletion should cascade through ALL child profiles."""
        from tasks.background_tasks import delete_user_data

        requested_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        mock_db.execute_read.side_effect = [
            [{"deletion_requested_at": requested_at}],
            [{"profile_id": "p1"}, {"profile_id": "p2"}, {"profile_id": "p3"}],
        ]

        result = delete_user_data("u1", grace_period_days=30)

        assert result is True
        # Messages, sessions, incidents deleted once per profile
        msg_deletes = [c for c in mock_db.execute_write.call_args_list
                       if "DELETE FROM messages" in c[0][0]]
        assert len(msg_deletes) == 3  # one per profile

    @patch("tasks.background_tasks.db_manager")
    def test_nonexistent_user_returns_false(self, mock_db):
        from tasks.background_tasks import delete_user_data
        mock_db.execute_read.return_value = []

        result = delete_user_data("nonexistent")

        assert result is False

    @patch("tasks.background_tasks.db_manager")
    def test_db_error_returns_false(self, mock_db):
        import sqlite3
        from tasks.background_tasks import delete_user_data
        mock_db.execute_read.side_effect = sqlite3.OperationalError("err")

        result = delete_user_data("u1")

        assert result is False


# ---------------------------------------------------------------------------
# BATCH EMAIL
# ---------------------------------------------------------------------------

class TestSendBatchEmails:

    @patch("tasks.background_tasks.send_email")
    def test_queues_all_emails(self, mock_send):
        from tasks.background_tasks import send_batch_emails
        mock_send.apply_async.return_value = MagicMock()

        emails = [
            {"to": "a@test.com", "subject": "S1", "html_content": "<p>1</p>"},
            {"to": "b@test.com", "subject": "S2", "html_content": "<p>2</p>"},
        ]

        result = send_batch_emails(emails)

        assert result["success"] == 2
        assert result["failed"] == 0
        assert mock_send.apply_async.call_count == 2
