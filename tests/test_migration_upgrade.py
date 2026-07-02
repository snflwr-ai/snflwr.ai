import types

import pytest

from storage.db_adapters import create_adapter
from database.migrations import runner


class FakeManager:
    """Minimal manager exposing .db_type and .adapter for a temp sqlite file."""
    def __init__(self, db_path):
        self.db_type = "sqlite"
        self.adapter = create_adapter("sqlite", db_path=str(db_path))


def _fake_migration(rev, created_table):
    m = types.ModuleType(f"mig_{rev}")
    m.revision = rev
    m.name = f"m{rev}"

    def up(cursor, dialect, _t=created_table):
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {_t} (id INTEGER PRIMARY KEY)")

    def down(cursor, dialect, _t=created_table):
        cursor.execute(f"DROP TABLE IF EXISTS {_t}")

    m.up, m.down = up, down
    return m


def test_upgrade_applies_all_from_empty(tmp_path):
    mgr = FakeManager(tmp_path / "t.db")
    migs = [_fake_migration("0001", "t_one"), _fake_migration("0002", "t_two")]
    applied = runner.upgrade(manager=mgr, migrations=migs)
    assert applied == ["0001", "0002"]
    conn = mgr.adapter.connect()
    cur = conn.cursor()
    assert runner.applied_versions(cur) == {"0001", "0002"}
    mgr.adapter.close()


def test_upgrade_is_idempotent(tmp_path):
    mgr = FakeManager(tmp_path / "t.db")
    migs = [_fake_migration("0001", "t_one")]
    runner.upgrade(manager=mgr, migrations=migs)
    second = runner.upgrade(manager=mgr, migrations=migs)
    assert second == []  # nothing new applied


def test_upgrade_to_target(tmp_path):
    mgr = FakeManager(tmp_path / "t.db")
    migs = [_fake_migration("0001", "t_one"), _fake_migration("0002", "t_two")]
    applied = runner.upgrade(target="0001", manager=mgr, migrations=migs)
    assert applied == ["0001"]


def test_first_run_stamps_baseline_when_core_tables_exist(tmp_path):
    """An existing pre-migration DB (accounts table already present, no
    schema_migrations) must be STAMPED at 0001, not have 0001 re-run."""
    mgr = FakeManager(tmp_path / "t.db")
    # Simulate an existing deployment: create the accounts table directly.
    conn = mgr.adapter.connect()
    conn.cursor().execute("CREATE TABLE accounts (parent_id TEXT PRIMARY KEY)")
    conn.commit()
    mgr.adapter.close()

    ran = {"baseline": False}
    baseline = _fake_migration("0001", "should_not_be_created")
    _orig_up = baseline.up
    def tracking_up(cursor, dialect):
        ran["baseline"] = True
        _orig_up(cursor, dialect)
    baseline.up = tracking_up

    applied = runner.upgrade(manager=mgr, migrations=[baseline])
    assert applied == []          # baseline was stamped, not applied
    assert ran["baseline"] is False
    conn = mgr.adapter.connect()
    cur = conn.cursor()
    assert runner.applied_versions(cur) == {"0001"}
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "should_not_be_created" not in tables
    mgr.adapter.close()
