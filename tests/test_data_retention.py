# tests/test_data_retention.py
"""
Tests for COPPA-compliant data retention management.
Verifies automated cleanup of old data according to retention policies.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest


class TestDataRetentionManagerInit:
    """Test DataRetentionManager initialization and lifecycle"""

    def test_init_sets_defaults(self):
        with patch("utils.data_retention.db_manager") as mock_db:
            from utils.data_retention import DataRetentionManager
            mgr = DataRetentionManager.__new__(DataRetentionManager)
            mgr.__init__()
            assert mgr.running is False
            assert mgr.scheduler_thread is None
            assert mgr.db is mock_db

    def test_start_scheduler_sets_running(self):
        with patch("utils.data_retention.db_manager"):
            from utils.data_retention import DataRetentionManager
            mgr = DataRetentionManager.__new__(DataRetentionManager)
            mgr.__init__()
            with patch("utils.data_retention.schedule"):
                mgr.start_scheduler()
                assert mgr.running is True
                assert mgr.scheduler_thread is not None
                mgr.stop_scheduler()

    def test_start_scheduler_idempotent(self):
        """Starting scheduler twice should be a no-op the second time"""
        with patch("utils.data_retention.db_manager"):
            from utils.data_retention import DataRetentionManager
            mgr = DataRetentionManager.__new__(DataRetentionManager)
            mgr.__init__()
            with patch("utils.data_retention.schedule"):
                mgr.start_scheduler()
                first_thread = mgr.scheduler_thread
                mgr.start_scheduler()  # second call
                assert mgr.scheduler_thread is first_thread
                mgr.stop_scheduler()

    def test_stop_scheduler_clears_state(self):
        with patch("utils.data_retention.db_manager"):
            from utils.data_retention import DataRetentionManager
            mgr = DataRetentionManager.__new__(DataRetentionManager)
            mgr.__init__()
            with patch("utils.data_retention.schedule") as mock_sched:
                mgr.start_scheduler()
                mgr.stop_scheduler()
                assert mgr.running is False
                mock_sched.clear.assert_called_once()

    def test_stop_scheduler_when_not_running(self):
        """Stopping a non-running scheduler should not error"""
        with patch("utils.data_retention.db_manager"):
            from utils.data_retention import DataRetentionManager
            mgr = DataRetentionManager.__new__(DataRetentionManager)
            mgr.__init__()
            mgr.stop_scheduler()  # Should not raise


class TestCleanupSafetyIncidents:
    """COPPA: Only resolved incidents past retention period are deleted"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_deletes_old_resolved_incidents(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 5}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_safety_incidents()
        assert result == 5
        # Should execute DELETE
        assert mock_db.execute_write.call_count >= 1
        delete_call = mock_db.execute_write.call_args_list[0]
        assert "DELETE FROM safety_incidents" in delete_call[0][0]
        assert "resolved = 1" in delete_call[0][0]

    def test_no_delete_when_zero_records(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 0}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_safety_incidents()
        assert result == 0
        mock_db.execute_write.assert_not_called()

    def test_empty_query_result(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_safety_incidents()
        assert result == 0


class TestCleanupAuditLogs:
    """Audit logs have longer retention (365 days default)"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_deletes_old_audit_logs(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 10}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_audit_logs()
        assert result == 10
        delete_call = mock_db.execute_write.call_args_list[0]
        assert "DELETE FROM audit_log" in delete_call[0][0]

    def test_no_delete_when_zero(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 0}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_audit_logs()
        assert result == 0
        mock_db.execute_write.assert_not_called()


class TestCleanupSessions:
    """Only ended sessions older than retention period are deleted"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_deletes_old_ended_sessions(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 3}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_sessions()
        assert result == 3
        delete_call = mock_db.execute_write.call_args_list[0]
        assert "DELETE FROM sessions" in delete_call[0][0]
        assert "ended_at IS NOT NULL" in delete_call[0][0]

    def test_writes_audit_log_on_delete(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 2}]
        mgr = self._make_mgr(mock_db)
        mgr.cleanup_sessions()
        # Should have DELETE + audit log INSERT
        assert mock_db.execute_write.call_count >= 2
        audit_call = mock_db.execute_write.call_args_list[-1]
        assert "INSERT INTO audit_log" in audit_call[0][0]


class TestCleanupConversations:
    """COPPA: Conversations are deleted with cascade to messages"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_cascade_deletes_messages_then_conversations(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 4}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_conversations()
        assert result == 4
        # First DELETE is messages, second is conversations
        calls = mock_db.execute_write.call_args_list
        assert "DELETE FROM messages" in calls[0][0][0]
        assert "DELETE FROM conversations" in calls[1][0][0]

    def test_no_cascade_when_zero(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 0}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_conversations()
        assert result == 0
        mock_db.execute_write.assert_not_called()


class TestCleanupAnalytics:
    """Analytics have the longest retention (730 days default)"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_deletes_old_analytics(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 100}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_analytics()
        assert result == 100
        delete_call = mock_db.execute_write.call_args_list[0]
        assert "DELETE FROM learning_analytics" in delete_call[0][0]


class TestCleanupExpiredTokens:
    """Clean up expired and invalid auth tokens"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_deletes_expired_and_invalid_tokens(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 7}]
        mgr = self._make_mgr(mock_db)
        result = mgr.cleanup_expired_tokens()
        assert result == 7
        delete_call = mock_db.execute_write.call_args_list[0]
        sql = delete_call[0][0]
        assert "DELETE FROM auth_tokens" in sql
        assert "expires_at" in sql
        assert "is_valid = 0" in sql


class TestVacuumDatabase:
    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_vacuum_executes(self):
        mock_db = MagicMock()
        mgr = self._make_mgr(mock_db)
        mgr.vacuum_database()
        mock_db.execute_write.assert_called_once_with("VACUUM")


class TestRunAllCleanupTasks:
    """Integration: run_all_cleanup_tasks orchestrates all cleanup"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    @patch("utils.data_retention.safety_config")
    def test_disabled_cleanup_returns_early(self, mock_safety):
        mock_safety.DATA_CLEANUP_ENABLED = False
        mock_db = MagicMock()
        mgr = self._make_mgr(mock_db)
        result = mgr.run_all_cleanup_tasks()
        assert result is None
        mock_db.execute_query.assert_not_called()

    @patch("utils.data_retention.safety_config")
    def test_all_tasks_run_and_results_collected(self, mock_safety):
        mock_safety.DATA_CLEANUP_ENABLED = True
        mock_safety.SAFETY_LOG_RETENTION_DAYS = 90
        mock_safety.AUDIT_LOG_RETENTION_DAYS = 365
        mock_safety.SESSION_RETENTION_DAYS = 180
        mock_safety.CONVERSATION_RETENTION_DAYS = 180
        mock_safety.ANALYTICS_RETENTION_DAYS = 730
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'count': 1}]
        mgr = self._make_mgr(mock_db)
        results = mgr.run_all_cleanup_tasks()
        assert 'tasks' in results
        assert 'safety_incidents' in results['tasks']
        assert 'audit_logs' in results['tasks']
        assert 'sessions' in results['tasks']
        assert 'conversations' in results['tasks']
        assert 'analytics' in results['tasks']
        assert 'auth_tokens' in results['tasks']
        assert 'vacuum' in results['tasks']

    @patch("utils.data_retention.safety_config")
    def test_db_error_in_task_captured_not_raised(self, mock_safety):
        """DB errors in individual tasks should not crash the whole run"""
        mock_safety.DATA_CLEANUP_ENABLED = True
        mock_safety.SAFETY_LOG_RETENTION_DAYS = 90
        mock_safety.AUDIT_LOG_RETENTION_DAYS = 365
        mock_safety.SESSION_RETENTION_DAYS = 180
        mock_safety.CONVERSATION_RETENTION_DAYS = 180
        mock_safety.ANALYTICS_RETENTION_DAYS = 730
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = sqlite3.OperationalError("table not found")
        mgr = self._make_mgr(mock_db)
        results = mgr.run_all_cleanup_tasks()
        # All tasks should have error status
        for task_name in ['safety_incidents', 'audit_logs', 'sessions', 'conversations', 'analytics', 'auth_tokens']:
            assert results['tasks'][task_name]['status'] == 'error'


class TestGetRetentionSummary:
    """Test retention summary reporting"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    @patch("utils.data_retention.safety_config")
    def test_returns_policy_and_volumes(self, mock_safety):
        mock_safety.get_retention_policy.return_value = {"some": "policy"}
        mock_safety.DATA_CLEANUP_ENABLED = True
        mock_safety.DATA_CLEANUP_HOUR = 3
        mock_safety.SAFETY_LOG_RETENTION_DAYS = 90
        mock_safety.AUDIT_LOG_RETENTION_DAYS = 365
        mock_safety.SESSION_RETENTION_DAYS = 180
        mock_safety.CONVERSATION_RETENTION_DAYS = 180
        mock_safety.ANALYTICS_RETENTION_DAYS = 730
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'total': 10, 'resolved': 5, 'ended': 3}]
        mgr = self._make_mgr(mock_db)
        result = mgr.get_retention_summary()
        assert result['retention_policy'] == {"some": "policy"}
        assert result['cleanup_enabled'] is True
        assert 'data_volumes' in result

    @patch("utils.data_retention.safety_config")
    def test_handles_db_error_gracefully(self, mock_safety):
        mock_safety.get_retention_policy.return_value = {}
        mock_safety.DATA_CLEANUP_ENABLED = False
        mock_safety.DATA_CLEANUP_HOUR = 3
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = sqlite3.OperationalError("error")
        mgr = self._make_mgr(mock_db)
        result = mgr.get_retention_summary()
        # Should still return policy, just empty volumes
        assert 'retention_policy' in result


class TestAuditLog:
    """Verify cleanup actions are logged to audit trail"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_audit_log_inserts_record(self):
        mock_db = MagicMock()
        mgr = self._make_mgr(mock_db)
        mgr._audit_log('data_retention', 'Deleted 5 records', True)
        mock_db.execute_write.assert_called_once()
        sql = mock_db.execute_write.call_args[0][0]
        assert "INSERT INTO audit_log" in sql
        params = mock_db.execute_write.call_args[0][1]
        assert params[1] == 'data_retention'  # event_type
        assert params[2] == 'system'  # user_id
        assert params[4] == 'Deleted 5 records'  # action
        assert params[7] == 1  # success = True

    def test_audit_log_handles_db_error(self):
        """Audit log failure should not raise"""
        mock_db = MagicMock()
        mock_db.execute_write.side_effect = sqlite3.OperationalError("fail")
        mgr = self._make_mgr(mock_db)
        mgr._audit_log('data_retention', 'test', True)  # Should not raise


class TestLogCleanupSummary:
    """Verify cleanup summary calculation"""

    def _make_mgr(self, mock_db):
        from utils.data_retention import DataRetentionManager
        mgr = DataRetentionManager.__new__(DataRetentionManager)
        mgr.db = mock_db
        mgr.running = False
        mgr.scheduler_thread = None
        mgr._stop_event = MagicMock()
        return mgr

    def test_summary_totals_deleted_counts(self):
        mock_db = MagicMock()
        mgr = self._make_mgr(mock_db)
        results = {
            'timestamp': '2026-01-01',
            'tasks': {
                'safety_incidents': {'status': 'success', 'deleted_count': 5},
                'audit_logs': {'status': 'success', 'deleted_count': 10},
                'sessions': {'status': 'error', 'error': 'fail'},
                'vacuum': {'status': 'success'},
            }
        }
        mgr._log_cleanup_summary(results)
        # Should have called _audit_log with total = 15
        sql = mock_db.execute_write.call_args[0][0]
        assert "INSERT INTO audit_log" in sql
        action = mock_db.execute_write.call_args[0][1][4]
        assert "15 total records deleted" in action
