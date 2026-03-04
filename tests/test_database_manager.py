"""
Tests for the core DatabaseManager: transactions, query execution,
cleanup, backup, and stats. Uses real SQLite temp databases.
"""

import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from storage.database import (
    DatabaseManager,
    _redact_sensitive_sql,
    _redact_sensitive_params,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db(temp_dir):
    db_path = temp_dir / "test_dbmgr.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize_database()
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_pragma_key(self):
        sql = "PRAGMA key = 'my-secret-key'"
        assert "[REDACTED]" in _redact_sensitive_sql(sql)
        assert "my-secret-key" not in _redact_sensitive_sql(sql)

    def test_redact_password(self):
        sql = "UPDATE accounts SET password = 'hunter2'"
        assert "[REDACTED]" in _redact_sensitive_sql(sql)
        assert "hunter2" not in _redact_sensitive_sql(sql)

    def test_no_redaction_needed(self):
        sql = "SELECT * FROM accounts"
        assert _redact_sensitive_sql(sql) == sql

    def test_redact_long_tokens_in_params(self):
        params = ("short", "a" * 50, "normal value")
        result = _redact_sensitive_params(params)
        assert "[REDACTED-TOKEN]" in result
        assert "short" in result
        assert "normal value" in result

    def test_redact_none_params(self):
        assert _redact_sensitive_params(None) == str(None)

    def test_redact_empty_params(self):
        assert _redact_sensitive_params(()) == str(())


# ---------------------------------------------------------------------------
# execute_query / execute_read
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    def test_basic_select(self, db):
        result = db.execute_query("SELECT COUNT(*) as count FROM accounts")
        assert result[0]["count"] == 0

    def test_execute_read_alias(self, db):
        result = db.execute_read("SELECT COUNT(*) as count FROM accounts")
        assert result[0]["count"] == 0

    def test_query_with_params(self, db):
        result = db.execute_query("SELECT COUNT(*) as count FROM accounts WHERE username = ?", ("nonexistent",))
        assert result[0]["count"] == 0

    def test_query_error_raises(self, db):
        with pytest.raises(sqlite3.OperationalError):
            db.execute_query("SELECT * FROM nonexistent_table_xyz")


# ---------------------------------------------------------------------------
# execute_write / execute_update
# ---------------------------------------------------------------------------


class TestExecuteWrite:
    def test_insert(self, db):
        db.execute_write(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p1", "user1", "hash", "dev1", datetime.now(timezone.utc).isoformat()),
        )
        result = db.execute_query("SELECT username FROM accounts WHERE parent_id = ?", ("p1",))
        assert result[0]["username"] == "user1"

    def test_execute_update_alias(self, db):
        db.execute_write(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p2", "user2", "hash", "dev2", datetime.now(timezone.utc).isoformat()),
        )
        db.execute_update(
            "UPDATE accounts SET username = ? WHERE parent_id = ?",
            ("updated_user2", "p2"),
        )
        result = db.execute_query("SELECT username FROM accounts WHERE parent_id = ?", ("p2",))
        assert result[0]["username"] == "updated_user2"

    def test_write_error_raises(self, db):
        with pytest.raises(sqlite3.Error):
            db.execute_write("INSERT INTO nonexistent_table_xyz (col) VALUES (?)", ("val",))


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransactions:
    def test_transaction_commit(self, db):
        with db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("p3", "txn_user", "hash", "dev3", datetime.now(timezone.utc).isoformat()),
            )
        result = db.execute_query("SELECT username FROM accounts WHERE parent_id = ?", ("p3",))
        assert len(result) == 1

    def test_begin_and_commit(self, db):
        db.begin_transaction()
        db.execute_write(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p4", "begin_user", "hash", "dev4", datetime.now(timezone.utc).isoformat()),
        )
        db.commit_transaction()
        result = db.execute_query("SELECT username FROM accounts WHERE parent_id = ?", ("p4",))
        assert len(result) == 1

    def test_begin_and_rollback(self, db):
        db.begin_transaction()
        db.execute_write(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p5", "rollback_user", "hash", "dev5", datetime.now(timezone.utc).isoformat()),
        )
        db.rollback_transaction()
        result = db.execute_query("SELECT username FROM accounts WHERE parent_id = ?", ("p5",))
        assert len(result) == 0

    def test_nested_begin_raises(self, db):
        db.begin_transaction()
        with pytest.raises(RuntimeError, match="already in progress"):
            db.begin_transaction()
        db.rollback_transaction()

    def test_commit_without_begin_raises(self, db):
        with pytest.raises(RuntimeError, match="No transaction"):
            db.commit_transaction()

    def test_rollback_without_begin_raises(self, db):
        with pytest.raises(RuntimeError, match="No transaction"):
            db.rollback_transaction()


# ---------------------------------------------------------------------------
# cleanup_old_data
# ---------------------------------------------------------------------------


class TestCleanupOldData:
    def _insert_session(self, db, session_id, ended_at):
        db.execute_write(
            "INSERT INTO sessions (session_id, started_at, ended_at) VALUES (?, ?, ?)",
            (session_id, datetime.now(timezone.utc).isoformat(), ended_at),
        )

    def _insert_audit(self, db, timestamp):
        db.execute_write(
            "INSERT INTO audit_log (timestamp, event_type, user_id, user_type, action, ip_address, success) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (timestamp, "test", "sys", "system", "test action", "127.0.0.1", 1),
        )

    def _insert_incident(self, db, resolved, resolved_at):
        db.execute_write(
            "INSERT INTO safety_incidents (profile_id, incident_type, severity, timestamp, resolved, resolved_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("profile1", "test", "minor", datetime.now(timezone.utc).isoformat(), resolved, resolved_at),
        )

    def test_deletes_old_ended_sessions(self, db):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        self._insert_session(db, "s1", old)
        self._insert_session(db, "s2", None)  # active session
        db.cleanup_old_data(retention_days=90)
        result = db.execute_query("SELECT session_id FROM sessions")
        ids = [r["session_id"] for r in result]
        assert "s1" not in ids
        assert "s2" in ids

    def test_deletes_old_audit_logs(self, db):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        self._insert_audit(db, old)
        self._insert_audit(db, recent)
        db.cleanup_old_data(retention_days=90)
        result = db.execute_query("SELECT COUNT(*) as count FROM audit_log")
        assert result[0]["count"] == 1

    def test_only_deletes_resolved_incidents(self, db):
        # First we need a profile
        db.execute_write(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("parent1", "p1user", "hash", "dev1", datetime.now(timezone.utc).isoformat()),
        )
        db.execute_write(
            "INSERT INTO child_profiles (profile_id, parent_id, name, age, grade, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("profile1", "parent1", "Child", 10, "5th", datetime.now(timezone.utc).isoformat()),
        )

        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        self._insert_incident(db, 1, old)  # resolved + old → delete
        self._insert_incident(db, 0, None)  # unresolved → keep
        db.cleanup_old_data(retention_days=90)
        result = db.execute_query("SELECT COUNT(*) as count FROM safety_incidents")
        assert result[0]["count"] == 1  # only unresolved remains


# ---------------------------------------------------------------------------
# get_database_stats
# ---------------------------------------------------------------------------


class TestDatabaseStats:
    def test_returns_stats(self, db):
        stats = db.get_database_stats()
        assert stats["database_type"] == "sqlite"
        assert "accounts_count" in stats
        assert "database_size_mb" in stats

    def test_handles_missing_tables(self, db):
        # Even if a query fails for one table, stats should still return
        stats = db.get_database_stats()
        assert isinstance(stats, dict)


# ---------------------------------------------------------------------------
# backup_database
# ---------------------------------------------------------------------------


class TestBackupDatabase:
    def test_creates_backup(self, db, temp_dir):
        backup_path = temp_dir / "backup.db"
        db.backup_database(backup_path)
        assert backup_path.exists()

    def test_postgresql_raises(self, db):
        db.db_type = "postgresql"
        with pytest.raises(NotImplementedError):
            db.backup_database(Path("/tmp/backup.db"))
        db.db_type = "sqlite"  # restore


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_does_not_raise(self, db):
        db.close()
        # Calling close multiple times should be safe
        db.close()


# ---------------------------------------------------------------------------
# execute_many
# ---------------------------------------------------------------------------


class TestExecuteMany:
    def test_batch_insert(self, db):
        now = datetime.now(timezone.utc).isoformat()
        params_list = [
            (f"p{i}", f"batch_user{i}", "hash", f"dev_batch{i}", now)
            for i in range(5)
        ]
        db.execute_many(
            "INSERT INTO accounts (parent_id, username, password_hash, device_id, created_at) VALUES (?, ?, ?, ?, ?)",
            params_list,
        )
        result = db.execute_query("SELECT COUNT(*) as count FROM accounts")
        assert result[0]["count"] == 5
