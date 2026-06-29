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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT,
                applied_at TIMESTAMP
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT,
                applied_at TEXT
            )
            """
        )


def applied_versions(cursor):
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def current_version(cursor):
    cursor.execute("SELECT version FROM schema_migrations")
    versions = [row[0] for row in cursor.fetchall()]
    return max(versions) if versions else None


def _now():
    return datetime.now(timezone.utc).isoformat()


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
