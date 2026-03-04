"""
Tests for storage/connection_pool.py — PostgreSQL Connection Pooling

Covers:
    - PostgreSQLConnectionPool: init, initialize, get_connection, execute_query,
      execute_many, close, get_stats, health_check, context manager
    - Global singleton: get_connection_pool, close_connection_pool

All tests mock psycopg2 to avoid requiring a real PostgreSQL server.
Skipped entirely if psycopg2 is not installed (it's required at import time).
"""

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, PropertyMock
import threading

import pytest

# Skip entire module if psycopg2 is not available
psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")


@pytest.fixture(autouse=True)
def _mock_config():
    """Mock system_config for all tests."""
    mock_config = MagicMock()
    mock_config.POSTGRES_HOST = "localhost"
    mock_config.POSTGRES_PORT = 5432
    mock_config.POSTGRES_DB = "testdb"
    mock_config.POSTGRES_USER = "testuser"
    mock_config.POSTGRES_PASSWORD = "testpass"
    mock_config.POSTGRES_SSLMODE = "prefer"
    mock_config.POSTGRES_MIN_CONNECTIONS = 2
    mock_config.POSTGRES_MAX_CONNECTIONS = 10

    with patch("storage.connection_pool.system_config", mock_config):
        yield mock_config


class TestPostgreSQLConnectionPool:

    @pytest.fixture
    def mock_pool_class(self):
        with patch("storage.connection_pool.pool.ThreadedConnectionPool") as mock:
            mock_conn = MagicMock()
            mock_instance = MagicMock()
            mock_instance.getconn.return_value = mock_conn
            mock.return_value = mock_instance
            yield {"class": mock, "instance": mock_instance, "conn": mock_conn}

    @pytest.fixture
    def pool_obj(self):
        from storage.connection_pool import PostgreSQLConnectionPool
        return PostgreSQLConnectionPool(
            min_connections=2,
            max_connections=10,
            connection_timeout=30,
            idle_timeout=300,
        )

    def test_init(self, pool_obj):
        assert pool_obj.min_connections == 2
        assert pool_obj.max_connections == 10
        assert pool_obj._pool is None

    def test_initialize(self, pool_obj, mock_pool_class):
        result = pool_obj.initialize()
        assert result is True
        assert pool_obj._pool is not None
        mock_pool_class["class"].assert_called_once()

    def test_initialize_already_initialized(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        result = pool_obj.initialize()
        assert result is True
        mock_pool_class["class"].assert_called_once()

    def test_initialize_failure(self, pool_obj, mock_pool_class):
        mock_pool_class["class"].side_effect = psycopg2.Error("Connection failed")
        result = pool_obj.initialize()
        assert result is False

    def test_get_connection(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        # Reset after initialize() (which does its own getconn/putconn test cycle)
        mock_pool_class["instance"].putconn.reset_mock()
        with pool_obj.get_connection() as conn:
            assert conn is mock_pool_class["conn"]
        mock_pool_class["conn"].commit.assert_called_once()
        mock_pool_class["instance"].putconn.assert_called_once()

    def test_get_connection_not_initialized_raises(self, pool_obj):
        with pytest.raises(RuntimeError, match="not initialized"):
            with pool_obj.get_connection():
                pass

    def test_get_connection_rollback_on_error(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        with pytest.raises(psycopg2.Error):
            with pool_obj.get_connection() as conn:
                raise psycopg2.Error("query failed")
        mock_pool_class["conn"].rollback.assert_called_once()

    def test_execute_query(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"id": 1, "name": "test"}]
        mock_pool_class["conn"].cursor.return_value = mock_cursor

        results = pool_obj.execute_query("SELECT * FROM test", fetch=True, dict_cursor=True)
        mock_cursor.execute.assert_called_once()
        assert results is not None
        mock_cursor.close.assert_called_once()

    def test_execute_query_no_fetch(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_cursor = MagicMock()
        mock_pool_class["conn"].cursor.return_value = mock_cursor

        result = pool_obj.execute_query("INSERT INTO test VALUES (1)", fetch=False)
        assert result is None
        mock_cursor.close.assert_called_once()

    def test_execute_many(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_pool_class["conn"].cursor.return_value = mock_cursor

        rows = pool_obj.execute_many(
            "INSERT INTO test VALUES (?)",
            [(1,), (2,), (3,)]
        )
        assert rows == 3
        mock_cursor.executemany.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_close(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        pool_obj.close()
        mock_pool_class["instance"].closeall.assert_called_once()
        assert pool_obj._pool is None

    def test_close_not_initialized(self, pool_obj):
        pool_obj.close()

    def test_get_stats(self, pool_obj):
        stats = pool_obj.get_stats()
        assert "connections_created" in stats
        assert "pool_size" in stats
        assert stats["pool_size"] == 10

    def test_health_check_success(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_pool_class["conn"].cursor.return_value = mock_cursor

        assert pool_obj.health_check() is True

    def test_health_check_failure(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_pool_class["conn"].cursor.side_effect = psycopg2.Error("fail")
        assert pool_obj.health_check() is False

    def test_context_manager(self, mock_pool_class):
        from storage.connection_pool import PostgreSQLConnectionPool
        p = PostgreSQLConnectionPool(min_connections=1, max_connections=5)
        with p:
            assert p._pool is not None
        assert p._pool is None

    def test_stats_track_queries(self, pool_obj, mock_pool_class):
        pool_obj.initialize()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_pool_class["conn"].cursor.return_value = mock_cursor

        pool_obj.execute_query("SELECT 1")
        stats = pool_obj.get_stats()
        assert stats["queries_executed"] == 1


class TestGlobalPool:

    def test_get_and_close(self, _mock_config):
        with patch("storage.connection_pool.pool.ThreadedConnectionPool") as mock_pool_cls:
            mock_instance = MagicMock()
            mock_conn = MagicMock()
            mock_instance.getconn.return_value = mock_conn
            mock_pool_cls.return_value = mock_instance

            import storage.connection_pool as cp
            cp._connection_pool = None

            pool = cp.get_connection_pool()
            assert pool is not None

            cp.close_connection_pool()
            assert cp._connection_pool is None
