"""Baseline schema — the full current snflwr.ai schema as a single revision.

Delegates to storage/schema.py (the existing CREATE TABLE helpers) plus the
historical idempotent ALTERs that used to run inline in
storage/database.py._initialize_database(). Idempotent and irreversible.
"""

revision = "0001"
name = "baseline"

try:
    from database.migrations.runner import IrreversibleMigration
except Exception:  # pragma: no cover - import order safety before runner exists
    class IrreversibleMigration(Exception):
        pass

from storage.schema import (
    ACCOUNT_MIGRATION_COLUMNS,
    PROFILE_MIGRATION_COLUMNS,
    create_postgres_tables,
    create_sqlite_tables,
)


def up(cursor, dialect):
    if dialect == "sqlite":
        create_sqlite_tables(cursor)
        # parents -> accounts rename (legacy DBs); ignore if already renamed.
        try:
            cursor.execute("ALTER TABLE parents RENAME TO accounts")
        except Exception:
            pass
        for col_def in ACCOUNT_MIGRATION_COLUMNS:
            try:
                cursor.execute(f"ALTER TABLE accounts ADD COLUMN {col_def}")
            except Exception:
                pass
        for col_def in PROFILE_MIGRATION_COLUMNS:
            try:
                cursor.execute(f"ALTER TABLE child_profiles ADD COLUMN {col_def}")
            except Exception:
                pass
        # Additional tables present in schema.sql but not in storage/schema.py
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_search_index (
                id INTEGER PRIMARY KEY,
                message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_quotas (
                quota_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                quota_type TEXT NOT NULL CHECK (quota_type IN ('daily_messages', 'daily_tokens', 'session_duration')),
                limit_value INTEGER NOT NULL,
                current_value INTEGER DEFAULT 0,
                reset_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parental_controls (
                control_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL UNIQUE,
                allowed_models TEXT,
                blocked_topics TEXT,
                time_restrictions TEXT,
                daily_message_limit INTEGER DEFAULT -1,
                require_approval INTEGER DEFAULT 0,
                enable_web_search INTEGER DEFAULT 1,
                enable_file_upload INTEGER DEFAULT 0,
                enable_code_execution INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                log_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                session_id TEXT,
                activity_type TEXT NOT NULL,
                description TEXT NOT NULL,
                metadata TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS safety_filter_cache (
                cache_id TEXT PRIMARY KEY,
                content_hash TEXT UNIQUE NOT NULL,
                is_safe INTEGER NOT NULL,
                severity TEXT,
                reason TEXT,
                triggered_keywords TEXT,
                cached_at TEXT NOT NULL,
                hit_count INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_usage (
                usage_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_duration_seconds INTEGER DEFAULT 0,
                last_used TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
            )
        """)
    else:
        create_postgres_tables(cursor)
        try:
            cursor.execute("SAVEPOINT rename_parents")
            cursor.execute("ALTER TABLE parents RENAME TO accounts")
            cursor.execute("RELEASE SAVEPOINT rename_parents")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT rename_parents")
        for col_def in ACCOUNT_MIGRATION_COLUMNS:
            try:
                cursor.execute("SAVEPOINT add_col")
                cursor.execute(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col_def}")
                cursor.execute("RELEASE SAVEPOINT add_col")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT add_col")
        for col_def in PROFILE_MIGRATION_COLUMNS:
            try:
                cursor.execute("SAVEPOINT add_col")
                cursor.execute(f"ALTER TABLE child_profiles ADD COLUMN IF NOT EXISTS {col_def}")
                cursor.execute("RELEASE SAVEPOINT add_col")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT add_col")


def down(cursor, dialect):
    raise IrreversibleMigration("0001_baseline cannot be downgraded")
