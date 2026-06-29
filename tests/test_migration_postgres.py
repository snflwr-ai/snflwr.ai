"""Postgres-tier migration drill.

Runs the migration runner against a real PostgreSQL server. Skips unless
POSTGRES_HOST is set (the CI test job and DR drill provide a postgres service
container).

Two environment hazards this test deliberately sidesteps:

1. The CI unit-test job runs with DB_TYPE=sqlite, so the global ``db_manager``
   singleton is a SQLite manager — we must NOT go through ``DatabaseManager()``.
2. Other tests in the suite mock ``psycopg2.pool.ThreadedConnectionPool`` (the
   PostgreSQLAdapter's pool), and that patch can leak across tests — so building
   the adapter via ``create_adapter`` could hand back a MagicMock pool instead of
   a real connection. We therefore connect with ``psycopg2.connect`` DIRECTLY
   (exactly like tests/test_dr_restore_postgres.py), using a RealDictCursor to
   match the production adapter, and wrap it in a tiny manager shim.
"""

import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")
psycopg2_extras = pytest.importorskip("psycopg2.extras")

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_HOST"),
    reason="POSTGRES_HOST not set — postgres-tier test",
)


def _val(row):
    """First column of a row for either RealDictCursor (dict) or a tuple."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


class _DirectAdapter:
    """Adapter shim exposing the one connection the runner needs."""

    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def close(self):
        if not self._conn.closed:
            self._conn.close()


class _PgManager:
    """Minimal manager (db_type + adapter) bound to a real PG connection."""

    def __init__(self):
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            dbname=os.getenv(
                "POSTGRES_DATABASE", os.getenv("POSTGRES_DB", "snflwr_test")
            ),
            user=os.getenv("POSTGRES_USER", "snflwr"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            cursor_factory=psycopg2_extras.RealDictCursor,
        )
        self.db_type = "postgresql"
        self.adapter = _DirectAdapter(conn)


def test_postgres_fresh_upgrade_to_head():
    from database.migrations import runner

    mgr = _PgManager()
    try:
        # Self-isolate: snflwr_test is shared with the DR-restore test, so reset
        # to an empty public schema for a genuinely fresh upgrade.
        conn = mgr.adapter.connect()
        cur = conn.cursor()
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        conn.commit()

        applied = runner.upgrade("head", manager=mgr)
        assert "0001" in applied  # baseline ran on the clean DB

        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.accounts')")
        assert _val(cur.fetchone()) is not None
        assert "0001" in runner.applied_versions(cur)
    finally:
        mgr.adapter.close()
