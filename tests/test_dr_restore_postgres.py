"""PostgreSQL disaster-recovery end-to-end test.

Proves the production DB tier's backup -> restore path actually works:
seed -> pg_dump (custom format) -> drop table (simulated disaster) ->
pg_restore -> data + schema survive. Mirrors the SQLite DR test
(test_dr_restore_end_to_end.py) for the Postgres tier.

Skips gracefully when Postgres / client tools are unavailable (local dev), so
the default suite is unaffected; it RUNS in CI against the postgres service
container (see .github/workflows/ci.yml).
"""
import os
import shutil

import pytest

psycopg2 = pytest.importorskip("psycopg2")


def _pg_params():
    return dict(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "snflwr"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        dbname=os.getenv("POSTGRES_DATABASE", os.getenv("POSTGRES_DB", "snflwr_test")),
    )


def _can_connect():
    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        return False
    try:
        conn = psycopg2.connect(connect_timeout=3, **_pg_params())
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _can_connect(),
    reason="PostgreSQL server + client tools not available (set POSTGRES_* env)",
)


@pytest.fixture
def pg(monkeypatch, tmp_path):
    """Point system_config at the test Postgres + a temp backup dir."""
    from config import system_config

    p = _pg_params()
    monkeypatch.setattr(system_config, "DATABASE_TYPE", "postgresql")
    monkeypatch.setattr(system_config, "POSTGRES_HOST", p["host"])
    monkeypatch.setattr(system_config, "POSTGRES_PORT", p["port"])
    monkeypatch.setattr(system_config, "POSTGRES_USER", p["user"])
    monkeypatch.setattr(system_config, "POSTGRES_PASSWORD", p["password"])
    monkeypatch.setattr(system_config, "POSTGRES_DB", p["dbname"])
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    (tmp_path / "backups").mkdir()

    conn = psycopg2.connect(**p)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dr_probe")
        cur.execute("CREATE TABLE dr_probe (id INT PRIMARY KEY, note TEXT)")
        cur.execute("INSERT INTO dr_probe (id, note) VALUES (1, 'survive-the-disaster')")
    yield conn, p
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dr_probe")
    conn.close()


def _table_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (name,))
        return cur.fetchone()[0] is not None


def test_postgres_backup_restore_recovers_data(pg):
    from pathlib import Path
    from scripts.backup_database import DatabaseBackup, restore_postgresql

    conn, _ = pg

    # Phase 1: backup (pg_dump custom format)
    bak = DatabaseBackup()
    ok, backup_file = bak.backup_postgresql()
    assert ok, f"pg backup failed: {backup_file}"
    assert Path(backup_file).exists() and Path(backup_file).stat().st_size > 0

    # Phase 2: disaster — drop the table
    with conn.cursor() as cur:
        cur.execute("DROP TABLE dr_probe")
    assert not _table_exists(conn, "dr_probe")

    # Phase 3: restore
    assert restore_postgresql(Path(backup_file)) is True

    # Phase 4 + 5: schema + data survived the round-trip
    assert _table_exists(conn, "dr_probe"), "table not restored"
    with conn.cursor() as cur:
        cur.execute("SELECT note FROM dr_probe WHERE id = 1")
        row = cur.fetchone()
    assert row and row[0] == "survive-the-disaster"
