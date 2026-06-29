"""Postgres-tier migration drill.

Runs the migration runner against a real PostgreSQL server. Skips unless
POSTGRES_HOST is set (the CI test job and DR drill provide a postgres service
container).

The CI unit-test job runs with DB_TYPE=sqlite (POSTGRES_* are set only for the
postgres-tier DR/migration tests), so the global ``db_manager`` singleton is a
SQLite manager. We therefore build a Postgres adapter DIRECTLY here — the same
way tests/test_dr_restore_postgres.py connects — instead of going through
DatabaseManager() (whose per-db_path singleton would hand back the cached
SQLite "default" instance).
"""

import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_HOST"),
    reason="POSTGRES_HOST not set — postgres-tier test",
)


class _PgManager:
    """Minimal manager (db_type + adapter) the runner needs, bound to Postgres."""

    def __init__(self):
        from storage.db_adapters import create_adapter

        self.db_type = "postgresql"
        self.adapter = create_adapter(
            "postgresql",
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv(
                "POSTGRES_DATABASE", os.getenv("POSTGRES_DB", "snflwr_test")
            ),
            user=os.getenv("POSTGRES_USER", "snflwr"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )


def test_postgres_fresh_upgrade_to_head():
    from database.migrations import runner

    mgr = _PgManager()
    try:
        runner.upgrade("head", manager=mgr)
        conn = mgr.adapter.connect()
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.accounts')")
        assert cur.fetchone()[0] is not None
        assert "0001" in runner.applied_versions(cur)
    finally:
        mgr.adapter.close()
