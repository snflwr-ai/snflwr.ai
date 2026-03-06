"""
Database Adapters
Provides unified interface for SQLite and PostgreSQL databases
"""

import sqlite3
import os
from abc import ABC, abstractmethod
from typing import List, Tuple, Any, Optional
from contextlib import contextmanager
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    POSTGRESQL_AVAILABLE = True
except ImportError:
    POSTGRESQL_AVAILABLE = False

from utils.logger import get_logger

# Unified database exception tuples for dual-backend support.
# Import these in any module that catches database errors:
#   from storage.db_adapters import DB_ERRORS
if POSTGRESQL_AVAILABLE:
    DB_ERRORS = (sqlite3.Error, psycopg2.Error)
    DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError)
    DB_OPERATIONAL_ERRORS = (sqlite3.OperationalError, psycopg2.OperationalError)
else:
    DB_ERRORS = (sqlite3.Error,)
    DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
    DB_OPERATIONAL_ERRORS = (sqlite3.OperationalError,)

logger = get_logger(__name__)

# Import config for encryption settings
from config import system_config


class DatabaseAdapter(ABC):
    """Abstract base class for database adapters"""

    @abstractmethod
    def connect(self):
        """Establish database connection"""
        pass

    @abstractmethod
    def close(self):
        """Close database connection"""
        pass

    @abstractmethod
    def execute_query(self, query: str, params: Tuple = ()) -> List[Any]:
        """Execute SELECT query and return results"""
        pass

    @abstractmethod
    def execute_write(self, query: str, params: Tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE and return affected rows"""
        pass

    @abstractmethod
    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """Execute batch write operations"""
        pass

    @abstractmethod
    @contextmanager
    def transaction(self):
        """Context manager for transactions"""
        pass

    @abstractmethod
    def get_placeholder(self) -> str:
        """Get parameter placeholder character (? for SQLite, %s for PostgreSQL)"""
        pass


class SQLiteAdapter(DatabaseAdapter):
    """SQLite database adapter"""

    def __init__(
        self, db_path: Path, timeout: float = 30.0, check_same_thread: bool = True
    ):
        self.db_path = db_path
        self.timeout = timeout
        self.check_same_thread = check_same_thread
        self.connection = None
        logger.info(f"Initializing SQLite adapter: {db_path}")

    def connect(self):
        """Establish SQLite connection"""
        if self.connection is None:
            self.connection = sqlite3.connect(
                str(self.db_path),
                timeout=self.timeout,
                check_same_thread=self.check_same_thread,
                isolation_level="DEFERRED",
            )
            self.connection.row_factory = sqlite3.Row

            # Enable foreign keys
            self.connection.execute("PRAGMA foreign_keys = ON")

            # Performance optimizations
            # Use WAL by default, but on Windows prefer DELETE journal to avoid -wal/-shm file locking issues
            try:
                if os.name == "nt":
                    self.connection.execute("PRAGMA journal_mode = DELETE")
                else:
                    self.connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error as e:
                # Best-effort, continue if unsupported
                logger.debug(f"Failed to set journal mode (non-critical): {e}")
            self.connection.execute("PRAGMA synchronous = NORMAL")
            self.connection.execute("PRAGMA cache_size = -20000")  # 20MB cache
            self.connection.execute("PRAGMA temp_store = MEMORY")
            self.connection.execute("PRAGMA busy_timeout = 5000")  # 5s wait for locks

            logger.debug("SQLite connection established")

        return self.connection

    def close(self):
        """Close SQLite connection"""
        if self.connection:
            try:
                # Ensure WAL changes are checkpointed and files are truncated so Windows can remove them
                try:
                    self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error as e:
                    # Best-effort; ignore if unsupported
                    logger.warning(
                        f"WAL checkpoint failed (may cause disk space issues): {e}"
                    )
                self.connection.close()
            finally:
                self.connection = None
                logger.debug("SQLite connection closed")

    def execute_query(self, query: str, params: Tuple = ()) -> List[sqlite3.Row]:
        """Execute SELECT query"""
        conn = self.connect()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            results = cursor.fetchall()
            return results
        finally:
            try:
                cursor.close()
            except sqlite3.Error as e:
                logger.debug(f"Failed to close cursor (non-critical): {e}")

    def execute_write(
        self, query: str, params: Tuple = (), auto_commit: bool = True
    ) -> int:
        """Execute write operation

        Args:
            query: SQL query string
            params: Query parameters
            auto_commit: Whether to auto-commit (False when in explicit transaction)

        Returns:
            Number of affected rows
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query, params)
                # Only auto-commit if requested (disabled during explicit transactions)
                if auto_commit:
                    conn.commit()
                return cursor.rowcount
            finally:
                try:
                    cursor.close()
                except sqlite3.Error as e:
                    logger.debug(f"Failed to close cursor (non-critical): {e}")
        except sqlite3.Error as e:
            if auto_commit:
                conn.rollback()
            raise

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """Execute batch write operations"""
        conn = self.connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.executemany(query, params_list)
                conn.commit()
                return cursor.rowcount
            finally:
                try:
                    cursor.close()
                except sqlite3.Error as e:
                    logger.debug(f"Failed to close cursor (non-critical): {e}")
        except sqlite3.Error as e:
            conn.rollback()
            raise

    @contextmanager
    def transaction(self):
        """Transaction context manager"""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            logger.error(f"Transaction failed, rolling back: {e}")
            raise

    def get_placeholder(self) -> str:
        """Get SQLite parameter placeholder"""
        return "?"


class PostgreSQLAdapter(DatabaseAdapter):
    """PostgreSQL database adapter with connection pooling"""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        min_connections: int = 2,
        max_connections: int = 10,
    ):
        if not POSTGRESQL_AVAILABLE:
            raise ImportError(
                "psycopg2 is required for PostgreSQL support. Install: pip install psycopg2-binary"
            )

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.pool = None
        self.connection = None

        logger.info(
            "Initializing PostgreSQL adapter: ***@%s:%s/%s", host, port, database
        )

    def connect(self):
        """Establish PostgreSQL connection with pooling"""
        if self.pool is None:
            try:
                # statement_timeout prevents runaway queries from holding locks
                # indefinitely. Default 30s; override via POSTGRES_STATEMENT_TIMEOUT env var.
                stmt_timeout_ms = int(os.getenv("POSTGRES_STATEMENT_TIMEOUT", "30000"))
                self.pool = psycopg2.pool.ThreadedConnectionPool(
                    self.min_connections,
                    self.max_connections,
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    sslmode=system_config.POSTGRES_SSLMODE,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                    options=f"-c statement_timeout={stmt_timeout_ms}",
                )
                logger.info(
                    f"PostgreSQL connection pool created: {self.min_connections}-{self.max_connections} connections"
                )
            except psycopg2.Error as e:
                logger.error(f"Failed to create PostgreSQL connection pool: {e}")
                raise

        if self.connection is None or self.connection.closed:
            self.connection = self.pool.getconn()
            logger.debug("PostgreSQL connection acquired from pool")

        return self.connection

    def close(self):
        """Return connection to pool"""
        if self.connection and not self.connection.closed:
            self.pool.putconn(self.connection)
            self.connection = None
            logger.debug("PostgreSQL connection returned to pool")

    def shutdown_pool(self):
        """Shutdown connection pool (call on application exit)"""
        if self.pool:
            self.pool.closeall()
            self.pool = None
            logger.info("PostgreSQL connection pool closed")

    @staticmethod
    def _translate_placeholders(query: str) -> str:
        """Translate SQLite-style ? placeholders to PostgreSQL-style %s.

        Only replaces ? outside single-quoted string literals so that
        queries like ``WHERE col LIKE '%?%'`` are not corrupted.
        """
        result = []
        in_quote = False
        i = 0
        while i < len(query):
            char = query[i]
            if char == "'" and not in_quote:
                in_quote = True
                result.append(char)
            elif char == "'" and in_quote:
                # Handle SQL escaped quotes (''): stay in quote mode
                if i + 1 < len(query) and query[i + 1] == "'":
                    result.append("''")
                    i += 1  # skip the next quote
                else:
                    in_quote = False
                    result.append(char)
            elif char == "?" and not in_quote:
                result.append("%s")
            else:
                result.append(char)
            i += 1
        return "".join(result)

    def execute_query(self, query: str, params: Tuple = ()) -> List[dict]:
        """Execute SELECT query"""
        query = self._translate_placeholders(query)
        conn = self.connect()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            results = cursor.fetchall()
            return results
        except psycopg2.Error as e:
            # Rollback to clear InFailedSqlTransaction state
            try:
                conn.rollback()
            except psycopg2.Error:
                pass
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except psycopg2.Error:
                    pass

    def execute_write(
        self, query: str, params: Tuple = (), auto_commit: bool = True
    ) -> int:
        """Execute write operation

        Args:
            query: SQL query string
            params: Query parameters
            auto_commit: Whether to auto-commit (False when in explicit transaction)

        Returns:
            Number of affected rows
        """
        query = self._translate_placeholders(query)
        conn = self.connect()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rowcount = cursor.rowcount
            if auto_commit:
                conn.commit()
            return rowcount
        except psycopg2.Error as e:
            if auto_commit:
                conn.rollback()
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except psycopg2.Error:
                    pass

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """Execute batch write operations"""
        query = self._translate_placeholders(query)
        conn = self.connect()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.executemany(query, params_list)
            rowcount = cursor.rowcount
            conn.commit()
            return rowcount
        except psycopg2.Error as e:
            conn.rollback()
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except psycopg2.Error:
                    pass

    @contextmanager
    def transaction(self):
        """Transaction context manager"""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            logger.error(f"Transaction failed, rolling back: {e}")
            raise

    def get_placeholder(self) -> str:
        """Get PostgreSQL parameter placeholder"""
        return "%s"


def create_adapter(db_type: str, **config) -> DatabaseAdapter:
    """
    Factory function to create appropriate database adapter

    Args:
        db_type: 'sqlite' or 'postgresql'
        **config: Database-specific configuration

            For SQLite:
                - db_path: Path to database file
                - timeout: Connection timeout (default: 30.0)
                - check_same_thread: SQLite thread safety (default: True)

            For PostgreSQL:
                - host: Database host
                - port: Database port (default: 5432)
                - database: Database name
                - user: Database user
                - password: Database password
                - min_connections: Min pool size (default: 2)
                - max_connections: Max pool size (default: 10)

    Returns:
        DatabaseAdapter instance
    """
    if db_type.lower() == "sqlite":
        # Check if encryption is enabled
        if system_config.DB_ENCRYPTION_ENABLED and system_config.DB_ENCRYPTION_KEY:
            # Lazy import to avoid circular dependency
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter

            logger.info("Creating encrypted SQLite adapter")
            return EncryptedSQLiteAdapter(
                db_path=config["db_path"],
                encryption_key=system_config.DB_ENCRYPTION_KEY,
                timeout=config.get("timeout", 30.0),
                check_same_thread=config.get("check_same_thread", True),
                kdf_iter=system_config.DB_KDF_ITERATIONS,
            )
        else:
            # Fail closed: refuse to create an unencrypted database when
            # encryption is explicitly enabled. Silently falling back would
            # store student PII in plaintext — a FERPA violation.
            if system_config.DB_ENCRYPTION_ENABLED:
                raise RuntimeError(
                    "FERPA VIOLATION PREVENTED: DB_ENCRYPTION_ENABLED=true but "
                    "DB_ENCRYPTION_KEY is not set. Refusing to create an unencrypted "
                    "database that would store student data in plaintext. "
                    "Set DB_ENCRYPTION_KEY or disable DB_ENCRYPTION_ENABLED."
                )
            return SQLiteAdapter(
                db_path=config["db_path"],
                timeout=config.get("timeout", 30.0),
                check_same_thread=config.get("check_same_thread", True),
            )
    elif db_type.lower() == "postgresql":
        return PostgreSQLAdapter(
            host=config["host"],
            port=config.get("port", 5432),
            database=config["database"],
            user=config["user"],
            password=config["password"],
            min_connections=config.get("min_connections", 2),
            max_connections=config.get("max_connections", 10),
        )
    else:
        raise ValueError(
            f"Unsupported database type: {db_type}. Must be 'sqlite' or 'postgresql'"
        )
