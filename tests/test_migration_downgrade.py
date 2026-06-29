import pytest

from database.migrations import runner
from database.migrations.runner import IrreversibleMigration
from tests.test_migration_upgrade import FakeManager, _fake_migration


def test_downgrade_to_target_reverts_higher(tmp_path):
    mgr = FakeManager(tmp_path / "t.db")
    migs = [_fake_migration("0001", "t_one"), _fake_migration("0002", "t_two")]
    runner.upgrade(manager=mgr, migrations=migs)
    reverted = runner.downgrade("0001", manager=mgr, migrations=migs)
    assert reverted == ["0002"]
    conn = mgr.adapter.connect()
    cur = conn.cursor()
    assert runner.applied_versions(cur) == {"0001"}
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "t_two" not in tables and "t_one" in tables
    mgr.adapter.close()


def test_downgrade_irreversible_raises(tmp_path):
    mgr = FakeManager(tmp_path / "t.db")
    bad = _fake_migration("0002", "t_two")
    def bad_down(cursor, dialect):
        raise IrreversibleMigration("nope")
    bad.down = bad_down
    migs = [_fake_migration("0001", "t_one"), bad]
    runner.upgrade(manager=mgr, migrations=migs)
    with pytest.raises(IrreversibleMigration):
        runner.downgrade("0001", manager=mgr, migrations=migs)
