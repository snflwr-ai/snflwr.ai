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
