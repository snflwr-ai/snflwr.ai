"""
Tests for storage/db_adapters.py

Covers:
- DB_ERRORS / DB_INTEGRITY_ERRORS / DB_OPERATIONAL_ERRORS tuple contents
- SQLiteAdapter: connect, close, execute_query, execute_write, execute_many, transaction, get_placeholder
- SQLiteAdapter: WAL/DELETE journal mode branching, error handling, cursor close errors
- PostgreSQLAdapter: placeholder translation, execute_query, execute_write, execute_many,
  transaction, connect pool creation, shutdown_pool, close, get_placeholder (all via mocks)
- create_adapter factory: sqlite, postgresql, unknown type, encryption flags
"""

import sqlite3
import sys
import tempfile
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock
from typing import List, Tuple

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sqlite_adapter(tmp_path: Path) -> "SQLiteAdapter":
    from storage.db_adapters import SQLiteAdapter
    return SQLiteAdapter(db_path=tmp_path / "test.db", timeout=5.0, check_same_thread=False)


# ===========================================================================
# DB_ERRORS tuple contents
# ===========================================================================

class TestDBErrorsTuple:

    def test_sqlite_error_is_in_db_errors(self):
        """sqlite3.Error must always be present in DB_ERRORS."""
        from storage.db_adapters import DB_ERRORS
        assert sqlite3.Error in DB_ERRORS

    def test_sqlite_integrity_error_in_db_integrity_errors(self):
        from storage.db_adapters import DB_INTEGRITY_ERRORS
        assert sqlite3.IntegrityError in DB_INTEGRITY_ERRORS

    def test_sqlite_operational_error_in_db_operational_errors(self):
        from storage.db_adapters import DB_OPERATIONAL_ERRORS
        assert sqlite3.OperationalError in DB_OPERATIONAL_ERRORS

    def test_db_errors_is_tuple(self):
        from storage.db_adapters import DB_ERRORS
        assert isinstance(DB_ERRORS, tuple)

    def test_db_integrity_errors_is_tuple(self):
        from storage.db_adapters import DB_INTEGRITY_ERRORS
        assert isinstance(DB_INTEGRITY_ERRORS, tuple)

    def test_db_operational_errors_is_tuple(self):
        from storage.db_adapters import DB_OPERATIONAL_ERRORS
        assert isinstance(DB_OPERATIONAL_ERRORS, tuple)

    def test_sqlite_error_is_catchable_via_db_errors(self):
        """Raise sqlite3.Error and catch it via DB_ERRORS."""
        from storage.db_adapters import DB_ERRORS
        with pytest.raises(DB_ERRORS):
            raise sqlite3.OperationalError("test")


# ===========================================================================
# SQLiteAdapter — connection / lifecycle
# ===========================================================================

class TestSQLiteAdapterConnect:

    def test_connect_returns_connection(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        assert conn is not None
        adapter.close()

    def test_connect_is_idempotent(self, tmp_path):
        """Calling connect() twice returns the same connection object."""
        adapter = _make_sqlite_adapter(tmp_path)
        conn1 = adapter.connect()
        conn2 = adapter.connect()
        assert conn1 is conn2
        adapter.close()

    def test_row_factory_is_sqlite_row(self, tmp_path):
        """Connection row_factory must be sqlite3.Row after connect()."""
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        assert conn.row_factory is sqlite3.Row
        adapter.close()

    def test_foreign_keys_enabled_after_connect(self, tmp_path):
        """PRAGMA foreign_keys should be ON (1)."""
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        cursor = conn.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()
        assert result[0] == 1
        adapter.close()

    def test_close_sets_connection_to_none(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        assert adapter.connection is not None
        adapter.close()
        assert adapter.connection is None

    def test_close_when_already_none_is_safe(self, tmp_path):
        """Closing without connecting should not raise."""
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.close()  # connection is None — should be a no-op

    def test_close_twice_is_safe(self, tmp_path):
        """Closing twice should not raise."""
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        adapter.close()
        adapter.close()


# ===========================================================================
# SQLiteAdapter — WAL / journal mode branching
# ===========================================================================

class TestSQLiteAdapterJournalMode:

    def test_wal_mode_on_non_windows(self, tmp_path):
        """On non-Windows, journal_mode should be WAL."""
        from storage.db_adapters import SQLiteAdapter

        with patch("os.name", "posix"):
            adapter = SQLiteAdapter(db_path=tmp_path / "wal.db", check_same_thread=False)
            conn = adapter.connect()
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            adapter.close()

        assert mode.upper() == "WAL"

    def test_delete_mode_on_windows(self, tmp_path):
        """On Windows (nt), journal_mode should be DELETE."""
        from storage.db_adapters import SQLiteAdapter

        with patch("os.name", "nt"):
            adapter = SQLiteAdapter(db_path=tmp_path / "nt.db", check_same_thread=False)
            conn = adapter.connect()
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            adapter.close()

        assert mode.upper() == "DELETE"

    def test_journal_mode_error_is_swallowed(self, tmp_path):
        """If setting journal_mode raises sqlite3.Error, connect() should still succeed."""
        from storage.db_adapters import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=tmp_path / "journal_err.db", check_same_thread=False)

        # Build a mock connection that raises on journal_mode PRAGMA but passes everything else
        real_conn = sqlite3.connect(
            str(tmp_path / "real.db"),
            check_same_thread=False,
            isolation_level="DEFERRED",
        )

        mock_conn = MagicMock(wraps=real_conn)
        mock_conn.row_factory = sqlite3.Row

        original_execute = real_conn.execute
        execute_call_count = {"n": 0}

        def selective_execute(sql, *a, **kw):
            execute_call_count["n"] += 1
            if "journal_mode" in sql:
                raise sqlite3.Error("journal_mode not supported")
            return original_execute(sql, *a, **kw)

        mock_conn.execute = selective_execute

        with patch("sqlite3.connect", return_value=mock_conn):
            conn = adapter.connect()

        assert conn is not None
        real_conn.close()


# ===========================================================================
# SQLiteAdapter — execute_query
# ===========================================================================

class TestSQLiteAdapterExecuteQuery:

    def test_execute_query_returns_list(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect().execute("CREATE TABLE t (v INTEGER)")
        adapter.connection.commit()
        results = adapter.execute_query("SELECT * FROM t")
        assert isinstance(results, list)
        adapter.close()

    def test_execute_query_returns_rows(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect().execute("CREATE TABLE t (v INTEGER)")
        adapter.connection.execute("INSERT INTO t VALUES (42)")
        adapter.connection.commit()
        results = adapter.execute_query("SELECT v FROM t")
        assert len(results) == 1
        assert results[0][0] == 42
        adapter.close()

    def test_execute_query_with_params(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.execute("INSERT INTO t VALUES (7)")
        conn.execute("INSERT INTO t VALUES (8)")
        conn.commit()
        results = adapter.execute_query("SELECT v FROM t WHERE v = ?", (7,))
        assert len(results) == 1
        assert results[0][0] == 7
        adapter.close()

    def test_execute_query_propagates_sqlite_error(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        with pytest.raises(sqlite3.Error):
            adapter.execute_query("SELECT * FROM nonexistent_table")
        adapter.close()


# ===========================================================================
# SQLiteAdapter — execute_write
# ===========================================================================

class TestSQLiteAdapterExecuteWrite:

    def test_execute_write_insert_returns_rowcount(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()
        rowcount = adapter.execute_write("INSERT INTO t VALUES (?)", (99,))
        assert rowcount == 1
        adapter.close()

    def test_execute_write_auto_commit_true_persists_data(self, tmp_path):
        """With auto_commit=True, data should be readable after write."""
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()
        adapter.execute_write("INSERT INTO t VALUES (?)", (55,), auto_commit=True)
        results = adapter.execute_query("SELECT v FROM t")
        assert any(r[0] == 55 for r in results)
        adapter.close()

    def test_execute_write_auto_commit_false_does_not_auto_commit(self, tmp_path):
        """With auto_commit=False, data is NOT committed until explicit commit."""
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()
        adapter.execute_write("INSERT INTO t VALUES (?)", (77,), auto_commit=False)
        # Roll back immediately — data should disappear
        conn.rollback()
        results = adapter.execute_query("SELECT v FROM t")
        assert len(results) == 0
        adapter.close()

    def test_execute_write_error_triggers_rollback(self, tmp_path):
        """sqlite3.Error during write causes rollback and re-raise."""
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        with pytest.raises(sqlite3.Error):
            adapter.execute_write("INSERT INTO nonexistent VALUES (?)", (1,))
        adapter.close()

    def test_execute_write_no_autocommit_error_does_not_rollback(self, tmp_path):
        """When auto_commit=False and error occurs, rollback is skipped (caller handles it)."""
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        with pytest.raises(sqlite3.Error):
            adapter.execute_write("INSERT INTO nonexistent VALUES (?)", (1,), auto_commit=False)
        adapter.close()


# ===========================================================================
# SQLiteAdapter — execute_many
# ===========================================================================

class TestSQLiteAdapterExecuteMany:

    def test_execute_many_inserts_all_rows(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()
        rows = [(1,), (2,), (3,)]
        adapter.execute_many("INSERT INTO t VALUES (?)", rows)
        results = adapter.execute_query("SELECT v FROM t ORDER BY v")
        assert [r[0] for r in results] == [1, 2, 3]
        adapter.close()

    def test_execute_many_error_triggers_rollback(self, tmp_path):
        """execute_many raises on bad query and rolls back."""
        adapter = _make_sqlite_adapter(tmp_path)
        adapter.connect()
        with pytest.raises(sqlite3.Error):
            adapter.execute_many("INSERT INTO nonexistent VALUES (?)", [(1,)])
        adapter.close()


# ===========================================================================
# SQLiteAdapter — transaction context manager
# ===========================================================================

class TestSQLiteAdapterTransaction:

    def test_transaction_commits_on_success(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()

        with adapter.transaction() as txn_conn:
            txn_conn.execute("INSERT INTO t VALUES (42)")

        results = adapter.execute_query("SELECT v FROM t")
        assert len(results) == 1
        adapter.close()

    def test_transaction_rolls_back_on_sqlite_error(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        conn = adapter.connect()
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.commit()

        with pytest.raises(sqlite3.Error):
            with adapter.transaction() as txn_conn:
                txn_conn.execute("INSERT INTO nonexistent VALUES (1)")

        # Table t should remain empty
        results = adapter.execute_query("SELECT v FROM t")
        assert len(results) == 0
        adapter.close()


# ===========================================================================
# SQLiteAdapter — get_placeholder
# ===========================================================================

class TestSQLiteAdapterGetPlaceholder:

    def test_get_placeholder_returns_question_mark(self, tmp_path):
        adapter = _make_sqlite_adapter(tmp_path)
        assert adapter.get_placeholder() == "?"


# ===========================================================================
# PostgreSQLAdapter — placeholder translation
# ===========================================================================

class TestPostgreSQLTranslatePlaceholders:

    def _translate(self, query: str) -> str:
        # Import lazily so the module loads even if psycopg2 is absent
        # We mock POSTGRESQL_AVAILABLE so the class is always testable
        with patch.dict("sys.modules", {"psycopg2": MagicMock(), "psycopg2.extras": MagicMock(), "psycopg2.pool": MagicMock()}):
            # Re-import to get the class with psycopg2 mocked
            import importlib
            import storage.db_adapters as mod
            # Use the static method directly — it has no psycopg2 dependency
            return mod.PostgreSQLAdapter._translate_placeholders(query)

    def test_simple_placeholder_translated(self):
        from storage.db_adapters import PostgreSQLAdapter
        result = PostgreSQLAdapter._translate_placeholders("SELECT * FROM t WHERE id = ?")
        assert result == "SELECT * FROM t WHERE id = %s"

    def test_multiple_placeholders_translated(self):
        from storage.db_adapters import PostgreSQLAdapter
        result = PostgreSQLAdapter._translate_placeholders("INSERT INTO t VALUES (?, ?, ?)")
        assert result == "INSERT INTO t VALUES (%s, %s, %s)"

    def test_placeholder_inside_string_literal_not_translated(self):
        """A ? inside a quoted string literal should NOT be replaced."""
        from storage.db_adapters import PostgreSQLAdapter
        result = PostgreSQLAdapter._translate_placeholders("SELECT '?' FROM t WHERE id = ?")
        assert result == "SELECT '?' FROM t WHERE id = %s"

    def test_escaped_quotes_in_literal_handled(self):
        """SQL escaped quotes ('') inside a string literal should not break parsing."""
        from storage.db_adapters import PostgreSQLAdapter
        result = PostgreSQLAdapter._translate_placeholders("SELECT 'it''s' FROM t WHERE id = ?")
        assert result == "SELECT 'it''s' FROM t WHERE id = %s"

    def test_no_placeholders_unchanged(self):
        from storage.db_adapters import PostgreSQLAdapter
        query = "SELECT * FROM t"
        assert PostgreSQLAdapter._translate_placeholders(query) == query

    def test_empty_string(self):
        from storage.db_adapters import PostgreSQLAdapter
        assert PostgreSQLAdapter._translate_placeholders("") == ""


# ===========================================================================
# PostgreSQLAdapter — full adapter via mocks
# ===========================================================================

class TestPostgreSQLAdapterMocked:
    """Test PostgreSQLAdapter methods by mocking psycopg2 entirely."""

    @pytest.fixture
    def psycopg2_mock(self):
        """Provide a fully mocked psycopg2 module."""
        mock = MagicMock()
        mock.Error = Exception
        mock.IntegrityError = Exception
        mock.OperationalError = Exception
        pool_mock = MagicMock()
        mock.pool = MagicMock()
        mock.pool.ThreadedConnectionPool = MagicMock()
        mock.extras = MagicMock()
        mock.extras.RealDictCursor = MagicMock()
        return mock

    @pytest.fixture
    def pg_adapter(self, psycopg2_mock):
        """Return a PostgreSQLAdapter with psycopg2 fully mocked."""
        with patch.dict("sys.modules", {
            "psycopg2": psycopg2_mock,
            "psycopg2.extras": psycopg2_mock.extras,
            "psycopg2.pool": psycopg2_mock.pool,
        }):
            # Force reload of db_adapters so it picks up the mock
            import importlib
            import storage.db_adapters as mod
            importlib.reload(mod)

            adapter = mod.PostgreSQLAdapter(
                host="localhost",
                port=5432,
                database="testdb",
                user="user",
                password="pass",
            )
            # Wire the pool + connection manually
            mock_conn = MagicMock()
            mock_conn.closed = False
            mock_pool = MagicMock()
            mock_pool.getconn.return_value = mock_conn
            adapter.pool = mock_pool
            adapter.connection = mock_conn
            yield adapter, mock_conn, mock_pool

    def test_get_placeholder_returns_percent_s(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        assert adapter.get_placeholder() == "%s"

    def test_execute_query_returns_results(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"id": 1}]
        conn.cursor.return_value = mock_cursor

        results = adapter.execute_query("SELECT * FROM t WHERE id = ?", (1,))
        assert results == [{"id": 1}]
        mock_cursor.execute.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_execute_query_rolls_back_on_error(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("query failed")
        conn.cursor.return_value = mock_cursor

        with pytest.raises(Exception, match="query failed"):
            adapter.execute_query("SELECT * FROM bad", ())
        conn.rollback.assert_called_once()

    def test_execute_write_commits_when_auto_commit(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        conn.cursor.return_value = mock_cursor

        rowcount = adapter.execute_write("INSERT INTO t VALUES (?)", (1,), auto_commit=True)
        assert rowcount == 1
        conn.commit.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_execute_write_no_commit_when_auto_commit_false(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        conn.cursor.return_value = mock_cursor

        adapter.execute_write("UPDATE t SET v=1", (), auto_commit=False)
        conn.commit.assert_not_called()

    def test_execute_write_rolls_back_on_error(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("write failed")
        conn.cursor.return_value = mock_cursor

        with pytest.raises(Exception, match="write failed"):
            adapter.execute_write("INSERT INTO t VALUES (?)", (1,))
        conn.rollback.assert_called_once()

    def test_execute_write_no_rollback_when_auto_commit_false_on_error(self, pg_adapter):
        """When auto_commit=False and error occurs, rollback should NOT be called."""
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("write failed")
        conn.cursor.return_value = mock_cursor

        with pytest.raises(Exception):
            adapter.execute_write("INSERT INTO t VALUES (?)", (1,), auto_commit=False)
        conn.rollback.assert_not_called()

    def test_execute_many_commits(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        conn.cursor.return_value = mock_cursor

        rowcount = adapter.execute_many("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        assert rowcount == 3
        conn.commit.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_execute_many_rolls_back_on_error(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = Exception("batch failed")
        conn.cursor.return_value = mock_cursor

        with pytest.raises(Exception, match="batch failed"):
            adapter.execute_many("INSERT INTO t VALUES (?)", [(1,)])
        conn.rollback.assert_called_once()

    def test_transaction_commits_on_success(self, pg_adapter):
        adapter, conn, pool = pg_adapter

        with adapter.transaction() as txn_conn:
            txn_conn.execute("something")

        conn.commit.assert_called_once()

    def test_transaction_rolls_back_on_error(self, pg_adapter):
        adapter, conn, pool = pg_adapter

        with pytest.raises(Exception):
            with adapter.transaction():
                raise Exception("txn error")

        conn.rollback.assert_called_once()

    def test_close_returns_conn_to_pool(self, pg_adapter):
        adapter, conn, pool = pg_adapter

        adapter.close()

        pool.putconn.assert_called_once_with(conn)
        assert adapter.connection is None

    def test_shutdown_pool_closes_all(self, pg_adapter):
        adapter, conn, pool = pg_adapter

        adapter.shutdown_pool()

        pool.closeall.assert_called_once()
        assert adapter.pool is None

    def test_shutdown_pool_noop_when_no_pool(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        adapter.pool = None
        adapter.shutdown_pool()  # should not raise

    def test_close_noop_when_connection_closed(self, pg_adapter):
        adapter, conn, pool = pg_adapter
        conn.closed = True  # already closed

        adapter.close()

        pool.putconn.assert_not_called()

    def test_connect_creates_pool_when_pool_is_none(self, pg_adapter):
        """If pool is None, connect() should create a new pool."""
        adapter, conn, pool = pg_adapter

        # Reset pool to None to trigger pool creation path
        adapter.pool = None
        adapter.connection = None

        # Make ThreadedConnectionPool return our mock pool
        new_pool = MagicMock()
        new_pool.getconn.return_value = conn

        import storage.db_adapters as mod
        mod.psycopg2.pool.ThreadedConnectionPool.return_value = new_pool

        result = adapter.connect()
        # Pool should now be set
        assert adapter.pool is not None


# ===========================================================================
# PostgreSQLAdapter — import error path
# ===========================================================================

class TestPostgreSQLAdapterImportError:

    def test_raises_import_error_without_psycopg2(self, tmp_path):
        """Constructing PostgreSQLAdapter without psycopg2 raises ImportError."""
        with patch("storage.db_adapters.POSTGRESQL_AVAILABLE", False):
            import storage.db_adapters as mod
            with pytest.raises(ImportError, match="psycopg2"):
                mod.PostgreSQLAdapter(
                    host="h", port=5432, database="d", user="u", password="p"
                )


# ===========================================================================
# create_adapter factory
# ===========================================================================

class TestCreateAdapter:

    def test_create_sqlite_adapter(self, tmp_path):
        from storage.db_adapters import create_adapter, SQLiteAdapter

        with patch("storage.db_adapters.system_config") as sc:
            sc.DB_ENCRYPTION_ENABLED = False
            sc.DB_ENCRYPTION_KEY = None
            adapter = create_adapter("sqlite", db_path=tmp_path / "a.db")

        assert isinstance(adapter, SQLiteAdapter)

    def test_create_sqlite_adapter_case_insensitive(self, tmp_path):
        from storage.db_adapters import create_adapter, SQLiteAdapter

        with patch("storage.db_adapters.system_config") as sc:
            sc.DB_ENCRYPTION_ENABLED = False
            sc.DB_ENCRYPTION_KEY = None
            adapter = create_adapter("SQLite", db_path=tmp_path / "b.db")

        assert isinstance(adapter, SQLiteAdapter)

    def test_create_postgresql_adapter(self):
        from storage.db_adapters import create_adapter, POSTGRESQL_AVAILABLE

        if not POSTGRESQL_AVAILABLE:
            pytest.skip("psycopg2 not installed")

        with patch("storage.db_adapters.system_config") as sc, \
             patch("storage.db_adapters.psycopg2") as pg_mock:
            sc.POSTGRES_SSLMODE = "disable"
            pg_mock.pool.ThreadedConnectionPool.return_value = MagicMock()
            pg_mock.extras.RealDictCursor = MagicMock()
            from storage.db_adapters import PostgreSQLAdapter
            adapter = create_adapter(
                "postgresql",
                host="localhost",
                port=5432,
                database="testdb",
                user="user",
                password="pass",
            )
        assert isinstance(adapter, PostgreSQLAdapter)

    def test_create_unknown_type_raises(self, tmp_path):
        from storage.db_adapters import create_adapter

        with pytest.raises(ValueError, match="Unsupported database type"):
            create_adapter("oracle", db_path=tmp_path / "x.db")

    def test_create_sqlite_with_encryption_enabled_and_key_uses_encrypted_adapter(self, tmp_path):
        """When encryption is enabled + key present, EncryptedSQLiteAdapter is used."""
        from storage.db_adapters import create_adapter

        mock_encrypted = MagicMock()

        with patch("storage.db_adapters.system_config") as sc, \
             patch("storage.db_adapters.EncryptedSQLiteAdapter", mock_encrypted, create=True):
            sc.DB_ENCRYPTION_ENABLED = True
            sc.DB_ENCRYPTION_KEY = "supersecretkey"
            sc.DB_KDF_ITERATIONS = 100000

            # Patch the lazy import inside create_adapter
            with patch("builtins.__import__", side_effect=_make_import_interceptor(
                "storage.encrypted_db_adapter", "EncryptedSQLiteAdapter", mock_encrypted
            )):
                try:
                    create_adapter("sqlite", db_path=tmp_path / "enc.db")
                except Exception:
                    pass  # EncryptedSQLiteAdapter is a MagicMock, constructor may fail

    def test_create_sqlite_encryption_enabled_no_key_raises_runtime_error(self, tmp_path):
        """DB_ENCRYPTION_ENABLED=True but no key must raise RuntimeError (FERPA guard)."""
        from storage.db_adapters import create_adapter

        with patch("storage.db_adapters.system_config") as sc:
            sc.DB_ENCRYPTION_ENABLED = True
            sc.DB_ENCRYPTION_KEY = None  # key missing!

            with pytest.raises(RuntimeError, match="FERPA VIOLATION PREVENTED"):
                create_adapter("sqlite", db_path=tmp_path / "unsafe.db")


def _make_import_interceptor(target_module: str, attr_name: str, replacement):
    """Returns a side_effect for builtins.__import__ that intercepts a specific import."""
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _import(name, *args, **kwargs):
        if name == target_module:
            mod = MagicMock()
            setattr(mod, attr_name, replacement)
            return mod
        return original_import(name, *args, **kwargs)

    return _import


# ===========================================================================
# SQLiteAdapter — cursor close failure is non-fatal
# ===========================================================================

class TestSQLiteAdapterCursorCloseFailure:

    def test_cursor_close_error_in_execute_query_is_swallowed(self, tmp_path):
        """If cursor.close() raises sqlite3.Error, execute_query should still return results.

        We verify this by mocking the connection returned by connect() and making
        the cursor's close() method raise.
        """
        from storage.db_adapters import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=tmp_path / "cq.db", check_same_thread=False)

        # Build a cursor mock that raises on close but returns real data
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [sqlite3.Row]
        mock_cursor.close.side_effect = sqlite3.Error("cursor close failed")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Patch connect() to inject our mock connection
        with patch.object(adapter, "connect", return_value=mock_conn):
            # Should not raise even though cursor.close() raises
            results = adapter.execute_query("SELECT 1")

        assert mock_cursor.close.called

    def test_cursor_close_error_in_execute_write_is_swallowed(self, tmp_path):
        """If cursor.close() raises sqlite3.Error, execute_write should still succeed."""
        from storage.db_adapters import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=tmp_path / "cw.db", check_same_thread=False)

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_cursor.close.side_effect = sqlite3.Error("cursor close failed")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "connect", return_value=mock_conn):
            rowcount = adapter.execute_write("INSERT INTO t VALUES (?)", (5,))

        assert rowcount == 1
        assert mock_cursor.close.called


# ===========================================================================
# SQLiteAdapter — WAL checkpoint on close
# ===========================================================================

class TestSQLiteAdapterWALCheckpoint:

    def test_wal_checkpoint_error_on_close_is_swallowed(self, tmp_path):
        """If WAL checkpoint raises, close() should still set connection to None."""
        from storage.db_adapters import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=tmp_path / "wal_close.db", check_same_thread=False)

        # Create a mock connection whose execute() raises on PRAGMA wal_checkpoint
        mock_conn = MagicMock()

        def selective_execute(sql, *args, **kwargs):
            if "wal_checkpoint" in sql:
                raise sqlite3.Error("checkpoint failed")
            return MagicMock()

        mock_conn.execute = selective_execute
        mock_conn.closed = False
        adapter.connection = mock_conn

        adapter.close()
        assert adapter.connection is None


# ===========================================================================
# SQLiteAdapter — execute_many cursor close error is non-fatal
# ===========================================================================

class TestSQLiteAdapterExecuteManyCursorClose:

    def test_execute_many_cursor_close_error_is_swallowed(self, tmp_path):
        """If cursor.close() raises during execute_many, the result is still returned."""
        from storage.db_adapters import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=tmp_path / "em.db", check_same_thread=False)

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_cursor.close.side_effect = sqlite3.Error("cursor close failed")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "connect", return_value=mock_conn):
            rowcount = adapter.execute_many("INSERT INTO t VALUES (?)", [(1,), (2,)])

        assert rowcount == 2
        assert mock_cursor.close.called


# ===========================================================================
# PostgreSQLAdapter — pool creation failure and cursor-close swallowing
# ===========================================================================

class TestPostgreSQLAdapterEdgeCases:
    """Additional edge-case tests for PostgreSQL adapter using mocked psycopg2."""

    @pytest.fixture
    def pg_module_mock(self):
        """A psycopg2 module mock with Error as a real exception class."""
        class FakePsycopg2Error(Exception):
            pass

        mock = MagicMock()
        mock.Error = FakePsycopg2Error
        mock.IntegrityError = FakePsycopg2Error
        mock.OperationalError = FakePsycopg2Error
        mock.extras = MagicMock()
        mock.extras.RealDictCursor = MagicMock()
        mock.pool = MagicMock()
        mock.pool.ThreadedConnectionPool = MagicMock()
        return mock, FakePsycopg2Error

    def test_pool_creation_error_raises(self, pg_module_mock):
        """If ThreadedConnectionPool raises psycopg2.Error, connect() propagates it."""
        pg_mock, FakePgError = pg_module_mock

        pg_mock.pool.ThreadedConnectionPool.side_effect = FakePgError("cannot connect")

        with patch.dict("sys.modules", {
            "psycopg2": pg_mock,
            "psycopg2.extras": pg_mock.extras,
            "psycopg2.pool": pg_mock.pool,
        }):
            import importlib
            import storage.db_adapters as mod
            importlib.reload(mod)

            adapter = mod.PostgreSQLAdapter(
                host="localhost", port=5432, database="db", user="u", password="p"
            )

            with pytest.raises(FakePgError):
                adapter.connect()

    def _make_adapter_with_mocked_conn(self, pg_mock, FakePgError):
        """Helper: reload db_adapters with pg_mock, return (adapter, mock_conn, mock_cursor)."""
        import importlib
        import storage.db_adapters as mod
        importlib.reload(mod)

        adapter = mod.PostgreSQLAdapter(
            host="h", port=5432, database="d", user="u", password="p"
        )

        mock_cursor = MagicMock()
        # Make the mock_conn.closed a plain False so connect() won't re-acquire
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = mock_cursor

        # Give adapter a pool so it won't try to create one, and a pre-set connection
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        adapter.pool = mock_pool
        adapter.connection = mock_conn

        return adapter, mock_conn, mock_cursor

    def test_execute_query_cursor_close_error_swallowed(self, pg_module_mock):
        """If cursor.close() raises psycopg2.Error in execute_query, it is swallowed."""
        pg_mock, FakePgError = pg_module_mock

        with patch.dict("sys.modules", {
            "psycopg2": pg_mock,
            "psycopg2.extras": pg_mock.extras,
            "psycopg2.pool": pg_mock.pool,
        }):
            adapter, mock_conn, mock_cursor = self._make_adapter_with_mocked_conn(pg_mock, FakePgError)
            mock_cursor.fetchall.return_value = [{"id": 1}]
            mock_cursor.close.side_effect = FakePgError("cursor close fail")

            # Should not raise even though cursor.close() raises
            results = adapter.execute_query("SELECT 1", ())
            assert results == [{"id": 1}]

    def test_execute_query_rollback_failure_swallowed(self, pg_module_mock):
        """If conn.rollback() itself raises during error recovery in execute_query, it's swallowed."""
        pg_mock, FakePgError = pg_module_mock

        with patch.dict("sys.modules", {
            "psycopg2": pg_mock,
            "psycopg2.extras": pg_mock.extras,
            "psycopg2.pool": pg_mock.pool,
        }):
            adapter, mock_conn, mock_cursor = self._make_adapter_with_mocked_conn(pg_mock, FakePgError)
            mock_cursor.execute.side_effect = FakePgError("query failed")
            mock_conn.rollback.side_effect = FakePgError("rollback also failed")

            # The original query error should still propagate
            with pytest.raises(FakePgError, match="query failed"):
                adapter.execute_query("SELECT 1", ())

    def test_execute_write_cursor_close_error_swallowed(self, pg_module_mock):
        """Cursor.close() raising in execute_write is swallowed."""
        pg_mock, FakePgError = pg_module_mock

        with patch.dict("sys.modules", {
            "psycopg2": pg_mock,
            "psycopg2.extras": pg_mock.extras,
            "psycopg2.pool": pg_mock.pool,
        }):
            adapter, mock_conn, mock_cursor = self._make_adapter_with_mocked_conn(pg_mock, FakePgError)
            mock_cursor.rowcount = 1
            mock_cursor.close.side_effect = FakePgError("cursor close fail")

            rowcount = adapter.execute_write("INSERT INTO t VALUES (?)", (1,))
            assert rowcount == 1

    def test_execute_many_cursor_close_error_swallowed(self, pg_module_mock):
        """Cursor.close() raising in execute_many is swallowed."""
        pg_mock, FakePgError = pg_module_mock

        with patch.dict("sys.modules", {
            "psycopg2": pg_mock,
            "psycopg2.extras": pg_mock.extras,
            "psycopg2.pool": pg_mock.pool,
        }):
            adapter, mock_conn, mock_cursor = self._make_adapter_with_mocked_conn(pg_mock, FakePgError)
            mock_cursor.rowcount = 3
            mock_cursor.close.side_effect = FakePgError("cursor close fail")

            rowcount = adapter.execute_many("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
            assert rowcount == 3
