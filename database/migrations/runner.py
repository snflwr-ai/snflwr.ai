"""Lightweight, versioned, reversible DB migration runner.

Runs ordered Python migration modules through the existing db_manager.adapter
(so encrypted SQLite and Postgres both work). Tracks applied revisions in a
schema_migrations table. No SQLAlchemy / Alembic.
"""

import importlib
import re
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent
_FILENAME_RE = re.compile(r"^(\d{4})_.+\.py$")


class IrreversibleMigration(Exception):
    """Raised by a migration's down() when it cannot be reversed."""


def dialect_for(db_type):
    return "postgresql" if db_type == "postgresql" else "sqlite"


def discover():
    """Import all NNNN_*.py migration modules, sorted by revision."""
    modules = []
    for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.py")):
        if not _FILENAME_RE.match(path.name):
            continue
        mod = importlib.import_module(f"database.migrations.{path.stem}")
        modules.append(mod)
    modules.sort(key=lambda m: m.revision)
    return modules


def ensure_version_table(cursor, dialect):
    if dialect == "postgresql":
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT,
                applied_at TIMESTAMP
            )
            """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT,
                applied_at TEXT
            )
            """)


def applied_versions(cursor):
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def current_version(cursor):
    cursor.execute("SELECT version FROM schema_migrations")
    versions = [row[0] for row in cursor.fetchall()]
    return max(versions) if versions else None


def _now():
    return datetime.now(timezone.utc).isoformat()


_LOCK_ID = 1  # matches the pg_advisory lock id used by the legacy startup path


def core_tables_exist(cursor, dialect):
    try:
        if dialect == "postgresql":
            cursor.execute("SELECT to_regclass('public.accounts')")
            return cursor.fetchone()[0] is not None
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
        )
        return cursor.fetchone() is not None
    except Exception:
        return False


def _acquire_lock(conn, cursor, dialect):
    if dialect == "postgresql":
        cursor.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))


def _release_lock(conn, cursor, dialect):
    if dialect == "postgresql":
        cursor.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))


def _insert_version(cursor, dialect, rev, name):
    placeholder = "%s" if dialect == "postgresql" else "?"
    cursor.execute(
        f"INSERT INTO schema_migrations (version, name, applied_at) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder})",
        (rev, name, _now()),
    )


def stamp(rev, *, manager=None):
    from storage.database import db_manager as _default

    manager = manager or _default
    dialect = dialect_for(manager.db_type)
    conn = manager.adapter.connect()
    cur = conn.cursor()
    ensure_version_table(cur, dialect)
    name = next((m.name for m in discover() if m.revision == rev), "")
    _insert_version(cur, dialect, rev, name)
    conn.commit()


def upgrade(target="head", *, manager=None, migrations=None):
    from storage.database import db_manager as _default

    manager = manager or _default
    migrations = migrations if migrations is not None else discover()
    dialect = dialect_for(manager.db_type)

    conn = manager.adapter.connect()
    cur = conn.cursor()
    newly_applied = []
    try:
        _acquire_lock(conn, cur, dialect)
        ensure_version_table(cur, dialect)
        conn.commit()

        already = applied_versions(cur)

        # First-run baseline detection: existing pre-migration DB.
        if not already and migrations and core_tables_exist(cur, dialect):
            base = migrations[0]
            _insert_version(cur, dialect, base.revision, base.name)
            conn.commit()
            already = {base.revision}

        for mod in migrations:
            if mod.revision in already:
                continue
            if target != "head" and mod.revision > target:
                break
            logger.info("Applying migration %s (%s)", mod.revision, mod.name)
            mod.up(cur, dialect)
            _insert_version(cur, dialect, mod.revision, mod.name)
            conn.commit()
            newly_applied.append(mod.revision)
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            _release_lock(conn, cur, dialect)
            conn.commit()
        except Exception:
            pass
    return newly_applied


def downgrade(target, *, manager=None, migrations=None):
    from storage.database import db_manager as _default

    manager = manager or _default
    migrations = migrations if migrations is not None else discover()
    dialect = dialect_for(manager.db_type)
    placeholder = "%s" if dialect == "postgresql" else "?"

    conn = manager.adapter.connect()
    cur = conn.cursor()
    reverted = []
    try:
        _acquire_lock(conn, cur, dialect)
        ensure_version_table(cur, dialect)
        already = applied_versions(cur)
        # Highest revision first.
        for mod in sorted(migrations, key=lambda m: m.revision, reverse=True):
            if mod.revision not in already:
                continue
            if mod.revision <= target:
                break
            logger.info("Reverting migration %s (%s)", mod.revision, mod.name)
            mod.down(cur, dialect)  # may raise IrreversibleMigration
            cur.execute(
                f"DELETE FROM schema_migrations WHERE version = {placeholder}",
                (mod.revision,),
            )
            conn.commit()
            reverted.append(mod.revision)
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            _release_lock(conn, cur, dialect)
            conn.commit()
        except Exception:
            pass
    return reverted


def status(*, manager=None, migrations=None):
    from storage.database import db_manager as _default

    manager = manager or _default
    migrations = migrations if migrations is not None else discover()
    dialect = dialect_for(manager.db_type)
    conn = manager.adapter.connect()
    cur = conn.cursor()
    ensure_version_table(cur, dialect)
    conn.commit()
    applied = applied_versions(cur)
    all_revs = [m.revision for m in migrations]
    return {
        "current": max(applied) if applied else None,
        "applied": sorted(r for r in all_revs if r in applied),
        "pending": [r for r in all_revs if r not in applied],
    }
