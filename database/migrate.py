"""
Database Migration Script
Migrates existing snflwr.ai databases to the current schema.
Safe to run multiple times — only adds missing columns.

Reads database/schema.sql to determine what columns each table SHOULD have,
then compares against the live database and adds any missing columns via
ALTER TABLE ... ADD COLUMN.  Works for both SQLite and PostgreSQL.

Usage:
    python -m database.migrate
    python database/migrate.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

def parse_schema_columns(schema_sql: str) -> dict:
    """
    Parse CREATE TABLE statements from schema_sql.

    Returns:
        {table_name: {col_name: col_type_string, ...}, ...}

    Only column definitions are returned; PRIMARY KEY / FOREIGN KEY /
    UNIQUE / CHECK / CONSTRAINT lines are skipped.
    """
    tables = {}
    # Match CREATE TABLE ... ( body ) where body may contain nested parens
    # (e.g. FOREIGN KEY, CHECK constraints).  We locate the matching closing
    # paren manually to avoid the nested-paren problem with a simple regex.
    pattern = re.compile(
        r'CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(',
        re.IGNORECASE,
    )
    for match in pattern.finditer(schema_sql):
        table_name = match.group(1)
        # Walk forward from the opening paren to find the matching closing paren
        start = match.end()  # position just after '('
        depth = 1
        pos = start
        while pos < len(schema_sql) and depth > 0:
            ch = schema_sql[pos]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            pos += 1
        table_body = schema_sql[start:pos - 1]  # contents between outer ( )
        tables[table_name] = _parse_column_definitions(table_body)
    return tables


def _parse_column_definitions(table_body: str) -> dict:
    """
    Parse the body of a CREATE TABLE statement.

    Returns:
        {col_name: col_type, ...}  e.g. {'session_id': 'TEXT', 'tokens': 'INTEGER'}
    """
    defs = {}
    for line in table_body.split('\n'):
        line = line.strip().rstrip(',')
        if not line:
            continue
        # Skip inline comments
        if line.startswith('--'):
            continue
        upper = line.upper().lstrip()
        if upper.startswith(('PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'CONSTRAINT')):
            continue
        parts = line.split()
        if len(parts) >= 2:
            col_name = parts[0]
            col_type = parts[1]
            defs[col_name] = col_type
    return defs


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _get_sqlite_columns(conn, table_name: str) -> list:
    """Return list of column names for a SQLite table."""
    return [row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()]


def _get_sqlite_tables(conn) -> list:
    """Return list of table names in the SQLite database."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]


def _migrate_sqlite(schema_tables: dict) -> list:
    """
    Apply missing column additions to the SQLite database.

    Returns:
        List of strings describing each change made.
    """
    import sqlite3

    db_path = Path(system_config.DB_PATH)
    if not db_path.exists():
        logger.info(f"SQLite DB does not exist yet at {db_path} — nothing to migrate.")
        return []

    changes = []
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")  # allow schema changes

    try:
        existing_tables = set(_get_sqlite_tables(conn))

        for table_name, expected_cols in schema_tables.items():
            if table_name not in existing_tables:
                # Table will be created by init_db; skip
                logger.debug(f"Table '{table_name}' does not exist yet — will be created by init_db.")
                continue

            existing_cols = set(_get_sqlite_columns(conn, table_name))

            for col_name, col_type in expected_cols.items():
                if col_name not in existing_cols:
                    stmt = f'ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}'
                    logger.info(f"  {stmt}")
                    conn.execute(stmt)
                    changes.append(f"Added column '{col_name}' ({col_type}) to table '{table_name}'")

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"SQLite migration failed: {e}")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    return changes


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------

def _get_pg_columns(cur, table_name: str) -> list:
    """Return list of column names for a PostgreSQL table."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table_name,),
    )
    return [row[0] for row in cur.fetchall()]


def _get_pg_tables(cur) -> list:
    """Return list of table names in the PostgreSQL public schema."""
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    return [row[0] for row in cur.fetchall()]


def _migrate_postgresql(schema_tables: dict) -> list:
    """
    Apply missing column additions to the PostgreSQL database.

    Uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS so it is idempotent
    even without the Python-level check.

    Returns:
        List of strings describing each change made.
    """
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError(
            "psycopg2 is required for PostgreSQL migrations. "
            "Install it with: pip install psycopg2-binary"
        )

    changes = []
    conn = psycopg2.connect(
        host=system_config.POSTGRES_HOST,
        port=system_config.POSTGRES_PORT,
        dbname=system_config.POSTGRES_DB,
        user=system_config.POSTGRES_USER,
        password=system_config.POSTGRES_PASSWORD,
        sslmode=system_config.POSTGRES_SSLMODE,
    )
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            existing_tables = set(_get_pg_tables(cur))

            for table_name, expected_cols in schema_tables.items():
                if table_name not in existing_tables:
                    logger.debug(
                        f"Table '{table_name}' does not exist yet — will be created by init_db."
                    )
                    continue

                existing_cols = set(_get_pg_columns(cur, table_name))

                for col_name, col_type in expected_cols.items():
                    if col_name not in existing_cols:
                        # PostgreSQL supports IF NOT EXISTS for ADD COLUMN (PG 9.6+)
                        stmt = (
                            f'ALTER TABLE {table_name} '
                            f'ADD COLUMN IF NOT EXISTS {col_name} {col_type}'
                        )
                        logger.info(f"  {stmt}")
                        cur.execute(stmt)
                        changes.append(
                            f"Added column '{col_name}' ({col_type}) to table '{table_name}'"
                        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"PostgreSQL migration failed: {e}")
        raise
    finally:
        conn.close()

    return changes


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_schema_migration() -> list:
    """
    Detect and apply missing columns to all existing tables.

    Reads database/schema.sql, compares it against the live database,
    and adds any missing columns.  Safe to call multiple times.

    Returns:
        List of human-readable strings describing each change made.
        Empty list means the database was already up to date.
    """
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_FILE}")

    schema_sql = SCHEMA_FILE.read_text()
    schema_tables = parse_schema_columns(schema_sql)

    logger.info(
        f"Running schema migration for DB type '{system_config.DB_TYPE}' ..."
    )

    if system_config.DB_TYPE == 'postgresql':
        changes = _migrate_postgresql(schema_tables)
    else:
        changes = _migrate_sqlite(schema_tables)

    if changes:
        logger.info(f"Migration complete — {len(changes)} column(s) added.")
        for change in changes:
            logger.info(f"  {change}")
    else:
        logger.info("Migration complete — database schema is already up to date.")

    return changes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    print("=" * 60)
    print("snflwr.ai — Database Schema Migration")
    print("=" * 60)
    print(f"DB type  : {system_config.DB_TYPE}")
    if system_config.DB_TYPE != 'postgresql':
        print(f"DB path  : {system_config.DB_PATH}")
    print()

    try:
        changes = run_schema_migration()
    except Exception as e:
        print(f"\nMigration FAILED: {e}")
        return 1

    if changes:
        print(f"Changes applied ({len(changes)}):")
        for change in changes:
            print(f"  + {change}")
    else:
        print("No changes needed — schema is already up to date.")

    print()
    print("Migration finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
