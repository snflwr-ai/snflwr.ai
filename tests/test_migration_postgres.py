"""Postgres-tier migration drill. Skips unless POSTGRES_HOST is set (matches the
DR-drill / CI service container)."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_HOST"),
    reason="POSTGRES_HOST not set — postgres-tier test",
)


def _pg_manager(monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "DB_TYPE", "postgresql")
    import storage.database as dbmod

    return dbmod.DatabaseManager()


def test_postgres_fresh_upgrade_to_head(monkeypatch):
    from database.migrations import runner

    mgr = _pg_manager(monkeypatch)
    runner.upgrade("head", manager=mgr)
    conn = mgr.adapter.connect()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.accounts')")
    assert cur.fetchone()[0] is not None
    assert "0001" in runner.applied_versions(cur)
    mgr.adapter.close()
