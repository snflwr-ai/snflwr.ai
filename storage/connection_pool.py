"""
Database Connection Pooling for PostgreSQL
Optimized connection management for production deployments
"""

import os
from contextlib import contextmanager
from typing import Optional, Any, Dict
import psycopg2
from psycopg2 import pool, extras
from psycopg2.extensions import connection as Connection
import threading
import time

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)


class PostgreSQLConnectionPool:
    """
    Thread-safe PostgreSQL connection pool with automatic connection management
    """

    def __init__(
        self,
        min_connections: int = None,
        max_connections: int = None,
        connection_timeout: int = 30,
        idle_timeout: int = 300,
    ):
        """
        Initialize connection pool

        Args:
            min_connections: Minimum number of connections to maintain
            max_connections: Maximum number of connections allowed
            connection_timeout: Timeout for getting connection from pool (seconds)
            idle_timeout: Timeout for idle connections (seconds)
        """
        self.min_connections = min_connections or system_config.POSTGRES_MIN_CONNECTIONS
        self.max_connections = max_connections or system_config.POSTGRES_MAX_CONNECTIONS
        self.connection_timeout = connection_timeout
        self.idle_timeout = idle_timeout

        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._lock = threading.Lock()
        self._stats = {
            'connections_created': 0,
            'connections_closed': 0,
            'connections_active': 0,
            'queries_executed': 0,
            'errors': 0,
        }

        logger.info(f"Initializing PostgreSQL connection pool:")
        logger.info(f"  Min connections: {self.min_connections}")
        logger.info(f"  Max connections: {self.max_connections}")
        logger.info(f"  Connection timeout: {connection_timeout}s")
        logger.info(f"  Idle timeout: {idle_timeout}s")

    def initialize(self) -> bool:
        """Initialize the connection pool"""
        try:
            with self._lock:
                if self._pool is not None:
                    logger.warning("Connection pool already initialized")
                    return True

                # Build connection parameters
                connection_params = {
                    'host': system_config.POSTGRES_HOST,
                    'port': system_config.POSTGRES_PORT,
                    'database': system_config.POSTGRES_DB,
                    'user': system_config.POSTGRES_USER,
                    'password': system_config.POSTGRES_PASSWORD,
                    'sslmode': system_config.POSTGRES_SSLMODE,
                    'connect_timeout': self.connection_timeout,
                    'options': f'-c statement_timeout={self.connection_timeout * 1000}',  # milliseconds
                }

                # Create threaded connection pool
                self._pool = pool.ThreadedConnectionPool(
                    minconn=self.min_connections,
                    maxconn=self.max_connections,
                    **connection_params
                )

                # Test connection (verify pool works by getting and returning)
                test_conn = self._pool.getconn()
                self._pool.putconn(test_conn)

                self._stats['connections_created'] = self.min_connections

                logger.info("[OK] PostgreSQL connection pool initialized successfully")
                return True

        except psycopg2.Error as e:
            logger.exception(f"Failed to initialize connection pool: {e}")
            return False

    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool (context manager)

        Usage:
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ...")
        """
        if self._pool is None:
            raise RuntimeError("Connection pool not initialized")

        conn = None
        try:
            # Get connection from pool
            conn = self._pool.getconn()
            with self._lock:
                self._stats['connections_active'] += 1

            # Set connection to autocommit=False for transaction control
            conn.autocommit = False

            yield conn

            # Commit if no exception
            conn.commit()

        except Exception as e:
            # Rollback on any error to prevent dirty connections in pool
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            with self._lock:
                self._stats['errors'] += 1
            logger.error(f"Database error: {e}")
            raise

        finally:
            # Return connection to pool
            if conn:
                self._pool.putconn(conn)
                with self._lock:
                    self._stats['connections_active'] -= 1

    def execute_query(
        self,
        query: str,
        params: tuple = None,
        fetch: bool = True,
        dict_cursor: bool = True
    ) -> Optional[list]:
        """
        Execute a query with automatic connection management

        Args:
            query: SQL query
            params: Query parameters (tuple)
            fetch: Whether to fetch results
            dict_cursor: Use DictCursor for dictionary results

        Returns:
            Query results (if fetch=True) or None
        """
        with self.get_connection() as conn:
            cursor_factory = extras.RealDictCursor if dict_cursor else None
            cursor = conn.cursor(cursor_factory=cursor_factory)

            try:
                # Execute query
                cursor.execute(query, params)
                self._stats['queries_executed'] += 1

                # Fetch results if needed
                if fetch:
                    results = cursor.fetchall()
                    return [dict(row) for row in results] if dict_cursor else results
                else:
                    return None

            finally:
                cursor.close()

    def execute_many(
        self,
        query: str,
        params_list: list
    ) -> int:
        """
        Execute query multiple times with different parameters

        Args:
            query: SQL query
            params_list: List of parameter tuples

        Returns:
            Number of rows affected
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            try:
                cursor.executemany(query, params_list)
                self._stats['queries_executed'] += len(params_list)
                return cursor.rowcount

            finally:
                cursor.close()

    def close(self):
        """Close all connections in the pool"""
        with self._lock:
            if self._pool is not None:
                self._pool.closeall()
                self._stats['connections_closed'] = self._stats['connections_created']
                logger.info("Connection pool closed")
                self._pool = None

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        return {
            **self._stats,
            'pool_size': self.max_connections,
            'min_connections': self.min_connections,
        }

    def health_check(self) -> bool:
        """Check if pool is healthy"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                cursor.close()
                return result[0] == 1
        except psycopg2.Error as e:
            logger.debug(f"Health check failed (non-critical): {e}")
            return False

    def __enter__(self):
        """Context manager enter"""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


# Global connection pool instance
_connection_pool: Optional[PostgreSQLConnectionPool] = None
_pool_lock = threading.Lock()


def get_connection_pool() -> PostgreSQLConnectionPool:
    """
    Get the global connection pool instance (singleton)

    Returns:
        PostgreSQLConnectionPool instance
    """
    global _connection_pool

    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = PostgreSQLConnectionPool()
                _connection_pool.initialize()

    return _connection_pool


def close_connection_pool():
    """Close the global connection pool"""
    global _connection_pool

    if _connection_pool is not None:
        with _pool_lock:
            if _connection_pool is not None:
                _connection_pool.close()
                _connection_pool = None


# Export public interface
__all__ = [
    'PostgreSQLConnectionPool',
    'get_connection_pool',
    'close_connection_pool',
]
