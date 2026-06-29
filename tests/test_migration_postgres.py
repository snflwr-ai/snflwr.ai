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


def _val(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def test_postgres_fresh_upgrade_to_head():
    from database.migrations import runner

    mgr = _PgManager()
    steps = {}
    try:
        # Self-isolate: the snflwr_test DB is shared with the DR-restore test, so
        # reset to an empty public schema for a genuinely fresh upgrade.
        conn = mgr.adapter.connect()
        cur = conn.cursor()
        steps["pid_initial"] = conn.get_backend_pid()
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        conn.commit()
        cur.execute("SELECT to_regclass('public.accounts')")
        steps["accounts_after_reset"] = _val(cur.fetchone())

        steps["applied"] = runner.upgrade("head", manager=mgr)

        conn2 = mgr.adapter.connect()
        steps["pid_after_upgrade"] = conn2.get_backend_pid()
        cur2 = conn2.cursor()
        cur2.execute("SELECT to_regclass('public.accounts')")
        steps["accounts_after_upgrade"] = _val(cur2.fetchone())
        cur2.execute("SELECT to_regclass('public.schema_migrations')")
        steps["sm_table"] = _val(cur2.fetchone())
        if steps["sm_table"] is not None:
            steps["sm_rows"] = sorted(runner.applied_versions(cur2))
        else:
            steps["sm_rows"] = None

        assert steps["accounts_after_upgrade"] is not None, steps
        assert steps["sm_rows"] and "0001" in steps["sm_rows"], steps
    finally:
        mgr.adapter.close()
