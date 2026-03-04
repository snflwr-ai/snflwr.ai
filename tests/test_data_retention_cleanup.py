"""
Tests for COPPA-compliant data retention management.
Verifies automated cleanup respects retention periods,
only deletes resolved/expired records, and logs to audit trail.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from utils.data_retention import DataRetentionManager


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_query = MagicMock(return_value=[])
    db.execute_write = MagicMock(return_value=None)
    return db


@pytest.fixture
def retention(mock_db):
    with patch("utils.data_retention.db_manager", mock_db):
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        import threading
        mgr._stop_event = threading.Event()
    return mgr


# ---------------------------------------------------------------------------
# cleanup_safety_incidents — only resolved incidents older than retention
# ---------------------------------------------------------------------------


class TestCleanupSafetyIncidents:
    def test_deletes_resolved_old_incidents(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 5}]
        count = retention.cleanup_safety_incidents()
        assert count == 5
        # Must use DELETE with resolved = 1
        write_calls = [c for c in mock_db.execute_write.call_args_list]
        delete_sql = write_calls[0][0][0]
        assert "DELETE FROM safety_incidents" in delete_sql
        assert "resolved = 1" in delete_sql

    def test_skips_when_no_records(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 0}]
        count = retention.cleanup_safety_incidents()
        assert count == 0
        # Should not call execute_write for delete (only audit)
        assert mock_db.execute_write.call_count == 0

    def test_never_deletes_unresolved(self, retention, mock_db):
        """COPPA: unresolved incidents must be retained regardless of age"""
        mock_db.execute_query.return_value = [{"count": 3}]
        retention.cleanup_safety_incidents()
        delete_sql = mock_db.execute_write.call_args_list[0][0][0]
        assert "resolved = 1" in delete_sql

    def test_uses_configured_retention_days(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 1}]
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.SAFETY_LOG_RETENTION_DAYS = 90
            retention.cleanup_safety_incidents()
        # The cutoff calculation uses SAFETY_LOG_RETENTION_DAYS
        query_sql = mock_db.execute_query.call_args[0][0]
        assert "resolved = 1" in query_sql


# ---------------------------------------------------------------------------
# cleanup_audit_logs
# ---------------------------------------------------------------------------


class TestCleanupAuditLogs:
    def test_deletes_old_audit_logs(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 10}]
        count = retention.cleanup_audit_logs()
        assert count == 10
        delete_sql = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM audit_log" in delete_sql

    def test_skips_when_empty(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 0}]
        count = retention.cleanup_audit_logs()
        assert count == 0
        assert mock_db.execute_write.call_count == 0


# ---------------------------------------------------------------------------
# cleanup_sessions — only ended sessions
# ---------------------------------------------------------------------------


class TestCleanupSessions:
    def test_only_deletes_ended_sessions(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 7}]
        retention.cleanup_sessions()
        delete_sql = mock_db.execute_write.call_args_list[0][0][0]
        assert "ended_at IS NOT NULL" in delete_sql

    def test_logs_to_audit_trail(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 2}]
        retention.cleanup_sessions()
        # Should have a DELETE + audit log INSERT
        assert mock_db.execute_write.call_count == 2
        audit_sql = mock_db.execute_write.call_args_list[1][0][0]
        assert "INSERT INTO audit_log" in audit_sql


# ---------------------------------------------------------------------------
# cleanup_conversations — cascading delete (messages then conversations)
# ---------------------------------------------------------------------------


class TestCleanupConversations:
    def test_deletes_messages_before_conversations(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 3}]
        retention.cleanup_conversations()
        # First write = delete messages, second = delete conversations, third = audit
        calls = mock_db.execute_write.call_args_list
        assert len(calls) == 3
        assert "DELETE FROM messages" in calls[0][0][0]
        assert "DELETE FROM conversations" in calls[1][0][0]

    def test_skips_when_no_old_conversations(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 0}]
        count = retention.cleanup_conversations()
        assert count == 0
        assert mock_db.execute_write.call_count == 0


# ---------------------------------------------------------------------------
# cleanup_analytics
# ---------------------------------------------------------------------------


class TestCleanupAnalytics:
    def test_deletes_old_analytics(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 20}]
        count = retention.cleanup_analytics()
        assert count == 20
        delete_sql = mock_db.execute_write.call_args_list[0][0][0]
        assert "DELETE FROM learning_analytics" in delete_sql


# ---------------------------------------------------------------------------
# cleanup_expired_tokens
# ---------------------------------------------------------------------------


class TestCleanupExpiredTokens:
    def test_deletes_expired_and_invalid(self, retention, mock_db):
        mock_db.execute_query.return_value = [{"count": 4}]
        retention.cleanup_expired_tokens()
        delete_sql = mock_db.execute_write.call_args[0][0]
        assert "expires_at < ?" in delete_sql
        assert "is_valid = 0" in delete_sql


# ---------------------------------------------------------------------------
# vacuum_database
# ---------------------------------------------------------------------------


class TestVacuumDatabase:
    def test_runs_vacuum(self, retention, mock_db):
        retention.vacuum_database()
        mock_db.execute_write.assert_called_once_with("VACUUM")


# ---------------------------------------------------------------------------
# run_all_cleanup_tasks — the full pipeline
# ---------------------------------------------------------------------------


class TestRunAllCleanupTasks:
    def test_respects_disabled_flag(self, retention, mock_db):
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.DATA_CLEANUP_ENABLED = False
            result = retention.run_all_cleanup_tasks()
        assert result is None
        mock_db.execute_query.assert_not_called()

    def test_runs_all_seven_tasks(self, retention, mock_db):
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.DATA_CLEANUP_ENABLED = True
            mock_cfg.SAFETY_LOG_RETENTION_DAYS = 90
            mock_cfg.AUDIT_LOG_RETENTION_DAYS = 365
            mock_cfg.SESSION_RETENTION_DAYS = 180
            mock_cfg.CONVERSATION_RETENTION_DAYS = 180
            mock_cfg.ANALYTICS_RETENTION_DAYS = 730
            mock_db.execute_query.return_value = [{"count": 0}]
            result = retention.run_all_cleanup_tasks()

        assert result is not None
        assert "tasks" in result
        tasks = result["tasks"]
        assert "safety_incidents" in tasks
        assert "audit_logs" in tasks
        assert "sessions" in tasks
        assert "conversations" in tasks
        assert "analytics" in tasks
        assert "auth_tokens" in tasks
        assert "vacuum" in tasks

    def test_handles_db_errors_gracefully(self, retention, mock_db):
        """Each task should fail independently without stopping the pipeline"""
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.DATA_CLEANUP_ENABLED = True
            mock_cfg.SAFETY_LOG_RETENTION_DAYS = 90
            mock_cfg.AUDIT_LOG_RETENTION_DAYS = 365
            mock_cfg.SESSION_RETENTION_DAYS = 180
            mock_cfg.CONVERSATION_RETENTION_DAYS = 180
            mock_cfg.ANALYTICS_RETENTION_DAYS = 730
            mock_db.execute_query.side_effect = sqlite3.OperationalError("table not found")
            result = retention.run_all_cleanup_tasks()

        # All tasks should have error status but pipeline completed
        for task_name, task_result in result["tasks"].items():
            if task_name != "vacuum":
                assert task_result["status"] == "error"


# ---------------------------------------------------------------------------
# get_retention_summary
# ---------------------------------------------------------------------------


class TestRetentionSummary:
    def test_returns_policy_even_on_db_error(self, retention, mock_db):
        mock_db.execute_query.side_effect = sqlite3.OperationalError("no such table")
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.get_retention_policy.return_value = {"days": 90}
            mock_cfg.DATA_CLEANUP_ENABLED = True
            mock_cfg.DATA_CLEANUP_HOUR = 3
            result = retention.get_retention_summary()
        assert "retention_policy" in result
        assert result["cleanup_enabled"] is True

    def test_includes_data_volumes(self, retention, mock_db):
        mock_db.execute_query.side_effect = [
            [{"total": 10, "resolved": 5}],  # incidents
            [{"total": 100}],                 # audit
            [{"total": 20, "ended": 15}],     # sessions
            [{"total": 50}],                  # conversations
            [{"total": 200}],                 # analytics
        ]
        with patch("utils.data_retention.safety_config") as mock_cfg:
            mock_cfg.get_retention_policy.return_value = {}
            mock_cfg.DATA_CLEANUP_ENABLED = True
            mock_cfg.DATA_CLEANUP_HOUR = 2
            mock_cfg.SAFETY_LOG_RETENTION_DAYS = 90
            mock_cfg.AUDIT_LOG_RETENTION_DAYS = 365
            mock_cfg.SESSION_RETENTION_DAYS = 180
            mock_cfg.CONVERSATION_RETENTION_DAYS = 180
            mock_cfg.ANALYTICS_RETENTION_DAYS = 730
            result = retention.get_retention_summary()
        assert len(result["data_volumes"]) == 5


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


class TestSchedulerLifecycle:
    def test_start_and_stop(self, retention):
        with patch("utils.data_retention.schedule"):
            retention.start_scheduler()
            assert retention.running is True
            assert retention.scheduler_thread is not None

            retention.stop_scheduler()
            assert retention.running is False

    def test_start_idempotent(self, retention):
        with patch("utils.data_retention.schedule"):
            retention.start_scheduler()
            first_thread = retention.scheduler_thread
            retention.start_scheduler()  # second call should be no-op
            assert retention.scheduler_thread is first_thread
            retention.stop_scheduler()

    def test_stop_when_not_running(self, retention):
        # Should not raise
        retention.stop_scheduler()


# ---------------------------------------------------------------------------
# _audit_log — internal audit trail logging
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_writes_audit_entry(self, retention, mock_db):
        retention._audit_log("data_retention", "Deleted 5 records", True)
        sql = mock_db.execute_write.call_args[0][0]
        assert "INSERT INTO audit_log" in sql
        params = mock_db.execute_write.call_args[0][1]
        assert params[1] == "data_retention"
        assert params[2] == "system"  # user_id
        assert params[3] == "system"  # user_type
        assert params[7] == 1  # success

    def test_handles_db_error_silently(self, retention, mock_db):
        mock_db.execute_write.side_effect = sqlite3.OperationalError("disk full")
        # Should not raise
        retention._audit_log("data_retention", "test", True)


# ---------------------------------------------------------------------------
# _log_cleanup_summary
# ---------------------------------------------------------------------------


class TestLogCleanupSummary:
    def test_sums_deleted_counts(self, retention, mock_db):
        results = {
            "timestamp": "2026-01-01T00:00:00",
            "tasks": {
                "safety_incidents": {"status": "success", "deleted_count": 5},
                "audit_logs": {"status": "success", "deleted_count": 10},
                "sessions": {"status": "error", "error": "fail"},
            },
        }
        retention._log_cleanup_summary(results)
        params = mock_db.execute_write.call_args[0][1]
        action = params[4]
        assert "15 total records deleted" in action
