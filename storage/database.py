# storage/database.py
"""
Database Manager for snflwr.ai
Production-grade database layer supporting both SQLite and PostgreSQL
"""

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config import system_config
from storage.db_adapters import (
    DB_ERRORS,
    DatabaseAdapter,
    PostgreSQLAdapter,
    create_adapter,
)
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)


def _redact_sensitive_sql(query: str) -> str:
    """Redact encryption keys and passwords from SQL before logging."""
    # Redact PRAGMA key = '...' statements
    redacted = re.sub(
        r"(PRAGMA\s+key\s*=\s*')[^']*(')", r"\1[REDACTED]\2", query, flags=re.IGNORECASE
    )
    # Redact password-like values
    redacted = re.sub(
        r"(password\s*=\s*')[^']*(')", r"\1[REDACTED]\2", redacted, flags=re.IGNORECASE
    )
    return redacted


def _redact_sensitive_params(params: Tuple) -> str:
    """Redact params tuple for logging — hide long token-like strings."""
    if not params:
        return str(params)
    redacted = []
    for p in params:
        if isinstance(p, str) and len(p) > 40 and " " not in p:
            redacted.append("[REDACTED-TOKEN]")
        else:
            redacted.append(p)
    return str(tuple(redacted))


from storage.schema import (
    ACCOUNT_MIGRATION_COLUMNS,
    PROFILE_MIGRATION_COLUMNS,
    create_postgres_tables,
    create_sqlite_tables,
)


class DatabaseManager:
    """Thread-safe database manager supporting SQLite and PostgreSQL"""

    # Support one manager per database path to allow test isolation
    _instances: Dict[str, "DatabaseManager"] = {}
    _global_lock = threading.Lock()

    def __new__(cls, db_path: Optional[Path] = None, db_type: Optional[str] = None):
        """Return a single DatabaseManager per db_path to allow isolated tests."""
        key = str(db_path) if db_path is not None else "default"
        with cls._global_lock:
            if key not in cls._instances:
                cls._instances[key] = super().__new__(cls)
            return cls._instances[key]

    def __init__(self, db_path: Optional[Path] = None, db_type: Optional[str] = None):
        """Initialize database manager"""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self.db_type = db_type or system_config.DB_TYPE
        self._local = threading.local()

        # Create appropriate adapter based on database type
        if self.db_type == "postgresql":
            logger.info(
                "Initializing PostgreSQL database: ***@%s:%s/%s",
                system_config.POSTGRES_HOST,
                system_config.POSTGRES_PORT,
                system_config.POSTGRES_DB,
            )
            self.adapter = create_adapter(
                "postgresql",
                host=system_config.POSTGRES_HOST,
                port=system_config.POSTGRES_PORT,
                database=system_config.POSTGRES_DB,
                user=system_config.POSTGRES_USER,
                password=system_config.POSTGRES_PASSWORD,
                min_connections=system_config.POSTGRES_MIN_CONNECTIONS,
                max_connections=system_config.POSTGRES_MAX_CONNECTIONS,
            )
        else:
            # Default to SQLite
            self.db_path = db_path or system_config.DB_PATH
            logger.info(f"Initializing SQLite database: {self.db_path}")

            # Ensure parent directory exists for SQLite
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            self.adapter = create_adapter(
                "sqlite",
                db_path=self.db_path,
                timeout=system_config.DB_TIMEOUT,
                check_same_thread=system_config.DB_CHECK_SAME_THREAD,
            )

        logger.info(f"Database initialized successfully using {self.db_type}")
        if self.db_type == "sqlite":
            logger.info("Run 'python database/init_db.py' to create schema if needed")
        else:
            logger.info(
                "Run 'python database/init_db_postgresql.py' to create schema if needed"
            )

    def _get_adapter(self) -> DatabaseAdapter:
        """Get thread-local adapter"""
        if not hasattr(self._local, "adapter") or self._local.adapter is None:
            self._local.adapter = self.adapter
            self._local.adapter.connect()
        return self._local.adapter

    @contextmanager
    def transaction(self):
        """Context manager for database transactions"""
        adapter = self._get_adapter()
        try:
            with adapter.transaction() as conn:
                yield conn
        except DB_ERRORS as e:
            logger.error(f"Transaction failed: {e}")
            raise

    def begin_transaction(self):
        """Begin an explicit transaction

        Note: With SQLite isolation_level='DEFERRED', transactions begin automatically
        on the first DML statement. This method just marks that we're in a transaction.
        """
        # Store transaction state in thread-local storage
        if not hasattr(self._local, "in_transaction"):
            self._local.in_transaction = False

        if self._local.in_transaction:
            raise RuntimeError("Transaction already in progress")

        # Just mark that we're in a transaction - SQLite will auto-begin on first DML
        self._local.in_transaction = True

    def commit_transaction(self):
        """Commit the current transaction"""
        if not hasattr(self._local, "in_transaction") or not self._local.in_transaction:
            raise RuntimeError("No transaction in progress")

        adapter = self._get_adapter()
        conn = adapter.connect()
        conn.commit()
        self._local.in_transaction = False

    def rollback_transaction(self):
        """Rollback the current transaction"""
        if not hasattr(self._local, "in_transaction") or not self._local.in_transaction:
            raise RuntimeError("No transaction in progress")

        adapter = self._get_adapter()
        conn = adapter.connect()
        conn.rollback()
        self._local.in_transaction = False

    def _initialize_database(self):
        """Create all database tables and indexes"""

        # Ensure parent directory exists (SQLite only — PostgreSQL doesn't use a file path)
        if hasattr(self, "db_path") and self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # For SQLite, run schema creation with a short-lived direct connection
        # to avoid creating a long-lived adapter connection that can hold file
        # handles on Windows. For other DBs, use the adapter transaction path.
        if self.db_type == "sqlite":
            # Use the configured adapter so an ENCRYPTED (SQLCipher) database is
            # opened with its key applied. A plain sqlite3.connect() fails with
            # "file is not a database" on an encrypted file. Released via
            # self.adapter.close() below so no stale handle is kept.
            conn = self.adapter.connect()
            cursor = conn.cursor()

            # Accounts table (renamed from parents — holds parents, admins, educators)
            create_sqlite_tables(cursor)

            # Migration: rename parents → accounts if needed (for existing DBs)
            try:
                cursor.execute("ALTER TABLE parents RENAME TO accounts")
            except Exception:
                pass  # Table already named accounts or doesn't exist

            # Add new columns for admin/educator support (idempotent for existing DBs)
            for col_def in ACCOUNT_MIGRATION_COLUMNS:
                try:
                    cursor.execute(f"ALTER TABLE accounts ADD COLUMN {col_def}")
                except Exception:
                    pass  # Column already exists

            # Add owui_user_id to child_profiles (for direct student Open WebUI login)
            # Add grade_level, tier, model_role (exist in schema.sql but not original CREATE TABLE)
            for col_def in PROFILE_MIGRATION_COLUMNS:
                try:
                    cursor.execute(f"ALTER TABLE child_profiles ADD COLUMN {col_def}")
                except Exception:
                    pass  # Column already exists

            # Create indexes for performance
            self._create_indexes(cursor)

            conn.commit()
            try:
                cursor.close()
            except DB_ERRORS as e:
                logger.debug(f"Failed to close cursor (non-critical): {e}")
            try:
                # Close via the adapter so it clears its connection handle
                # (a bare conn.close() would leave the adapter holding a stale,
                # closed connection that later calls would reuse).
                self.adapter.close()
            except DB_ERRORS as e:
                logger.debug(f"Failed to close connection (non-critical): {e}")

            logger.info("Database schema initialized successfully")
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()

                # Acquire an advisory lock so only one worker runs schema creation
                # at a time. Other workers block here until the lock is released.
                # Lock ID 1 is reserved for schema initialization.
                cursor.execute("SELECT pg_advisory_xact_lock(1)")

                # Accounts table (renamed from parents — holds parents, admins, educators)
                create_postgres_tables(cursor)

                # Migration: rename parents → accounts if needed (for existing DBs)
                # In PostgreSQL, a failed statement aborts the entire transaction.
                # Use savepoints so failures don't poison the transaction.
                try:
                    cursor.execute("SAVEPOINT rename_parents")
                    cursor.execute("ALTER TABLE parents RENAME TO accounts")
                    cursor.execute("RELEASE SAVEPOINT rename_parents")
                except Exception:
                    cursor.execute("ROLLBACK TO SAVEPOINT rename_parents")

                # Add new columns for admin/educator support (idempotent for existing DBs)
                for col_def in ACCOUNT_MIGRATION_COLUMNS:
                    try:
                        cursor.execute("SAVEPOINT add_col")
                        cursor.execute(
                            f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col_def}"
                        )
                        cursor.execute("RELEASE SAVEPOINT add_col")
                    except Exception:
                        cursor.execute("ROLLBACK TO SAVEPOINT add_col")

                # Add owui_user_id to child_profiles (for direct student Open WebUI login)
                # Add grade_level, tier, model_role (exist in schema.sql but not original CREATE TABLE)
                for col_def in PROFILE_MIGRATION_COLUMNS:
                    try:
                        cursor.execute("SAVEPOINT add_col")
                        cursor.execute(
                            f"ALTER TABLE child_profiles ADD COLUMN IF NOT EXISTS {col_def}"
                        )
                        cursor.execute("RELEASE SAVEPOINT add_col")
                    except Exception:
                        cursor.execute("ROLLBACK TO SAVEPOINT add_col")

                # Create indexes for performance
                self._create_indexes(cursor)

                logger.info("Database schema initialized successfully")
                try:
                    cursor.close()
                except DB_ERRORS as e:
                    logger.debug(f"Failed to close cursor (non-critical): {e}")

    # Public compatibility wrapper expected by some tests
    def initialize_database(self, db_path: Optional[Path] = None):
        """Compatibility wrapper to initialize or re-initialize the database.

        Args:
            db_path: Optional path to set as the database file before initializing.
        """
        if db_path is not None:
            self.db_path = db_path
        self._initialize_database()
        # Ensure connections are closed after initialization to avoid file locks
        try:
            self.close()
        except DB_ERRORS as e:
            logger.debug(f"Failed to close after initialization (non-critical): {e}")

    def _create_indexes(self, cursor):
        """Create database indexes for query optimization"""

        indexes = [
            # Accounts
            "CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_device ON accounts(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_email_hash ON accounts(email_hash)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role)",
            # Profiles
            "CREATE INDEX IF NOT EXISTS idx_profiles_parent ON child_profiles(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_profiles_active ON child_profiles(is_active)",
            # Sessions
            "CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id)",
            # Conversations
            "CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_conversations_profile ON conversations(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_conversations_flagged ON conversations(is_flagged)",
            # Messages
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)",
            # Safety incidents
            "CREATE INDEX IF NOT EXISTS idx_incidents_profile ON safety_incidents(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON safety_incidents(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_severity ON safety_incidents(severity)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_unresolved ON safety_incidents(resolved) WHERE NOT resolved",
            # Analytics
            "CREATE INDEX IF NOT EXISTS idx_analytics_profile_date ON learning_analytics(profile_id, date)",
            "CREATE INDEX IF NOT EXISTS idx_analytics_date ON learning_analytics(date)",
            # Audit
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type)",
            # Error tracking
            "CREATE INDEX IF NOT EXISTS idx_errors_hash ON error_tracking(error_hash)",
            "CREATE INDEX IF NOT EXISTS idx_errors_severity ON error_tracking(severity)",
            "CREATE INDEX IF NOT EXISTS idx_errors_first_seen ON error_tracking(first_seen)",
            "CREATE INDEX IF NOT EXISTS idx_errors_unresolved ON error_tracking(resolved) WHERE resolved = 0",  # INTEGER col, not BOOLEAN
        ]

        for index_sql in indexes:
            try:
                if self.db_type == "postgresql":
                    # In PostgreSQL, a failed statement aborts the transaction.
                    # Use savepoints so one bad index doesn't kill everything.
                    cursor.execute("SAVEPOINT idx_sp")
                cursor.execute(index_sql)
                if self.db_type == "postgresql":
                    cursor.execute("RELEASE SAVEPOINT idx_sp")
            except DB_ERRORS as e:
                if self.db_type == "postgresql":
                    cursor.execute("ROLLBACK TO SAVEPOINT idx_sp")
                logger.warning(f"Index creation warning: {e}")

    def execute_query(self, query: str, params: Tuple = ()) -> List[sqlite3.Row]:
        """
        Execute a SELECT query and return results

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            List of Row objects (dict-like for PostgreSQL, sqlite3.Row for SQLite)
        """
        try:
            adapter = self._get_adapter()
            results = adapter.execute_query(query, params)
            # Keep connection open for reuse (closed in DatabaseManager.close())
            return results
        except DB_ERRORS as e:
            logger.error(f"Query execution failed: {e}")
            logger.error(f"Query: {_redact_sensitive_sql(query)!r}")
            logger.error(
                f"Params: {sanitize_log_value(_redact_sensitive_params(params))!r}"
            )
            raise

    def execute_read(self, query: str, params: Tuple = ()) -> List[Union[Dict, Any]]:
        """Alias for execute_query for backward compatibility"""
        return self.execute_query(query, params)  # type: ignore[return-value]

    def execute_write(self, query: str, params: Tuple = ()) -> int:
        """
        Execute an INSERT, UPDATE, or DELETE query

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            Number of affected rows
        """
        try:
            adapter = self._get_adapter()

            # Determine if we should auto-commit based on transaction state
            in_transaction = (
                hasattr(self._local, "in_transaction") and self._local.in_transaction
            )
            auto_commit = not in_transaction

            result = adapter.execute_write(query, params, auto_commit=auto_commit)  # type: ignore[call-arg]

            # Keep connection open for reuse (closed in DatabaseManager.close())
            # Transactions are managed by the transaction() context manager

            return result
        except DB_ERRORS as e:
            logger.error(f"Write operation failed: {e}")
            logger.error(f"Query: {_redact_sensitive_sql(query)!r}")
            logger.error(
                f"Params: {sanitize_log_value(_redact_sensitive_params(params))!r}"
            )
            raise

    # Backwards-compatible alias used by tests
    def execute_update(self, query: str, params: Tuple = ()) -> int:
        return self.execute_write(query, params)

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """
        Execute multiple write operations in a transaction

        Args:
            query: SQL query string
            params_list: List of parameter tuples

        Returns:
            Total number of affected rows
        """
        try:
            adapter = self._get_adapter()
            result = adapter.execute_many(query, params_list)
            try:
                adapter.close()
            except DB_ERRORS as e_close:
                logger.debug(
                    f"Failed to close adapter after batch (non-critical): {e_close}"
                )
            return result
        except DB_ERRORS as e:
            logger.error(f"Batch operation failed: {e}")
            raise

    def cleanup_old_data(self, retention_days: int = 90):
        """Remove old data according to retention policies"""

        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        try:
            adapter = self._get_adapter()
            placeholder = adapter.get_placeholder()

            with self.transaction() as conn:
                cursor = conn.cursor()

                # Clean up old sessions
                cursor.execute(
                    f"DELETE FROM sessions WHERE ended_at < {placeholder} AND ended_at IS NOT NULL",  # nosec B608
                    (cutoff_date,),
                )
                sessions_deleted = cursor.rowcount

                # Clean up old audit logs
                cursor.execute(
                    f"DELETE FROM audit_log WHERE timestamp < {placeholder}",  # nosec B608
                    (cutoff_date,),
                )
                audit_deleted = cursor.rowcount

                # Clean up resolved safety incidents
                if self.db_type == "sqlite":
                    cursor.execute(
                        f"DELETE FROM safety_incidents WHERE resolved = 1 AND resolved_at < {placeholder}",  # nosec B608
                        (cutoff_date,),
                    )
                else:
                    cursor.execute(
                        f"DELETE FROM safety_incidents WHERE resolved = TRUE AND resolved_at < {placeholder}",  # nosec B608
                        (cutoff_date,),
                    )
                incidents_deleted = cursor.rowcount

                logger.info(
                    f"Cleanup complete: {sessions_deleted} sessions, "
                    f"{audit_deleted} audit logs, {incidents_deleted} incidents removed"
                )

            # VACUUM must run outside a transaction (SQLite and PostgreSQL both require this).
            # Run it after the DELETE transaction has committed successfully.
            # We open a separate connection in autocommit mode so VACUUM is not
            # wrapped in the adapter's default DEFERRED transaction.
            try:
                if self.db_type == "sqlite":
                    # Separate autocommit connection for VACUUM. If the DB is
                    # encrypted, open it via SQLCipher with the key applied — a
                    # plain sqlite3 connection can't read an encrypted file.
                    from storage.encrypted_db_adapter import SQLCIPHER_AVAILABLE

                    enc_key = getattr(self.adapter, "encryption_key", None)
                    if SQLCIPHER_AVAILABLE and enc_key:
                        from storage.encrypted_db_adapter import sqlcipher

                        conn_vac = sqlcipher.connect(
                            str(self.db_path), isolation_level=None
                        )
                        conn_vac.execute(
                            "PRAGMA key = '%s'" % enc_key.replace("'", "''")
                        )
                    else:
                        import sqlite3 as _sqlite3

                        conn_vac = _sqlite3.connect(
                            str(self.db_path), isolation_level=None
                        )
                    conn_vac.execute("VACUUM")
                    conn_vac.close()
                else:
                    adapter = self._get_adapter()
                    conn_vac = adapter.pool.getconn()
                    conn_vac.autocommit = True
                    cur = conn_vac.cursor()
                    cur.execute("VACUUM ANALYZE")
                    cur.close()
                    conn_vac.autocommit = False
                    adapter.pool.putconn(conn_vac)
            except DB_ERRORS as ve:
                # VACUUM failure is non-critical — data was already cleaned up
                logger.warning(f"VACUUM after cleanup failed (non-critical): {ve}")

        except DB_ERRORS as e:
            logger.error(f"Data cleanup failed: {e}")
            raise

    def insert_false_positive(
        self,
        profile_id: str,
        message_text: str,
        block_reason: str,
        triggered_keywords: str,  # JSON string e.g. '["bomb"]'
        educator_note: Optional[str] = None,
    ) -> int:
        """Insert a false positive report. Returns the new row id."""
        from datetime import datetime, timezone

        self.execute_write(
            """
            INSERT INTO safety_false_positives
                (profile_id, message_text, block_reason, triggered_keywords, educator_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                message_text,
                block_reason,
                triggered_keywords,
                educator_note,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        rows = self.execute_query(
            "SELECT id FROM safety_false_positives WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        )
        return int(rows[0]["id"])

    def get_false_positives(self, reviewed: bool = False) -> List[Dict[str, Any]]:
        """Return false positive reports. If reviewed=False, return only unreviewed."""
        if reviewed:
            rows = self.execute_query(
                "SELECT * FROM safety_false_positives ORDER BY created_at DESC"
            )
        else:
            rows = self.execute_query(
                "SELECT * FROM safety_false_positives WHERE reviewed_at IS NULL ORDER BY created_at DESC"
            )
        return [dict(row) for row in rows]

    def mark_false_positive_reviewed(self, fp_id: int, reviewed_by: str) -> None:
        """Mark a false positive report as reviewed."""
        from datetime import datetime, timezone

        self.execute_write(
            """
            UPDATE safety_false_positives
            SET reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), reviewed_by, fp_id),
        )

    def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics"""

        try:
            stats: dict = {}
            stats["database_type"] = self.db_type

            # Count tables - whitelist of valid table names
            VALID_TABLES = {
                "accounts",
                "child_profiles",
                "sessions",
                "messages",
                "safety_incidents",
                "parent_alerts",
            }

            for table in VALID_TABLES:
                try:
                    # Validate table name is in whitelist (defense in depth)
                    if table not in VALID_TABLES:
                        logger.warning(f"Skipping invalid table name: {table}")
                        continue

                    # Safe to use f-string since table is validated against whitelist
                    result = self.execute_query(
                        f"SELECT COUNT(*) as count FROM {table}"
                    )
                    if result:
                        stats[f"{table}_count"] = (
                            result[0]["count"]
                            if isinstance(result[0], dict)
                            else result[0][0]
                        )
                except DB_ERRORS as e:
                    logger.debug(f"Error getting count for {table}: {e}")
                    stats[f"{table}_count"] = 0

            # Database size
            if self.db_type == "sqlite":
                adapter = self._get_adapter()
                conn = adapter.connect()
                page_count_result = conn.execute("PRAGMA page_count").fetchone()
                page_size_result = conn.execute("PRAGMA page_size").fetchone()
                # Safely extract values with null checks
                if page_count_result and page_size_result:
                    page_count = page_count_result[0] if page_count_result[0] else 0
                    page_size = page_size_result[0] if page_size_result[0] else 0
                    stats["database_size_mb"] = round(
                        (page_count * page_size) / (1024 * 1024), 2
                    )
                else:
                    stats["database_size_mb"] = 0
            else:
                # PostgreSQL database size
                result = self.execute_query(
                    "SELECT pg_size_pretty(pg_database_size(current_database())) as size"
                )
                if result:
                    stats["database_size"] = (
                        result[0]["size"]
                        if isinstance(result[0], dict)
                        else result[0][0]
                    )

            return stats

        except DB_ERRORS as e:
            logger.error(f"Failed to get database stats: {e}")
            return {"database_type": self.db_type, "error": str(e)}

    def backup_database(self, backup_path: Path):
        """Create a backup of the database"""

        if self.db_type != "sqlite":
            logger.warning(
                "Database backup is only supported for SQLite. For PostgreSQL, use pg_dump."
            )
            raise NotImplementedError("Use pg_dump for PostgreSQL backups")

        try:
            import shutil

            # Close any open connections
            adapter = self._get_adapter()
            adapter.close()

            # Create backup
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"Database backed up to: {backup_path}")

        except OSError as e:
            logger.error(f"Database backup failed: {e}")
            raise

    def close(self):
        """Close database connections"""
        # Close any thread-local adapter if present
        try:
            if hasattr(self._local, "adapter") and self._local.adapter:
                try:
                    self._local.adapter.close()
                except DB_ERRORS as e:
                    logger.debug(
                        f"Failed to close thread-local adapter (non-critical): {e}"
                    )

                # For PostgreSQL, also shutdown the connection pool
                try:
                    if isinstance(self._local.adapter, PostgreSQLAdapter):
                        self._local.adapter.shutdown_pool()
                except DB_ERRORS as e:
                    logger.debug(
                        f"Failed to shutdown PostgreSQL pool (non-critical): {e}"
                    )

                self._local.adapter = None
                logger.info("Database connection closed")
        except (
            Exception
        ):  # Intentional catch-all: thread-local access may raise AttributeError
            pass

        # Also ensure the shared adapter instance is closed to release file handles (important on Windows)
        try:
            if hasattr(self, "adapter") and self.adapter:
                try:
                    self.adapter.close()
                except DB_ERRORS as e:
                    logger.debug(f"Failed to close shared adapter (non-critical): {e}")
        except (
            Exception
        ):  # Intentional catch-all: attribute access guard during teardown
            pass
        # Force garbage collection and a tiny pause to ensure OS releases file handles (helps on Windows)
        try:
            import gc
            import time

            gc.collect()
            # Increase pause to help Windows release file handles reliably
            time.sleep(0.2)
        except (
            Exception
        ):  # Intentional catch-all: gc/time imports must not crash teardown
            pass
        # Remove instance from registry to allow tests to cleanup files
        try:
            key = str(self.db_path) if hasattr(self, "db_path") else "default"
            with self.__class__._global_lock:
                if key in self.__class__._instances:
                    del self.__class__._instances[key]
        except Exception:  # Intentional catch-all: registry cleanup must not crash
            pass


# Singleton instance
db_manager = DatabaseManager()


# Export public interface
__all__ = ["DatabaseManager", "db_manager"]
