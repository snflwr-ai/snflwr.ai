import pytest

from storage.db_adapters import create_adapter
from database.migrations import runner


def _cur(tmp_path):
    adapter = create_adapter("sqlite", db_path=str(tmp_path / "t.db"))
    conn = adapter.connect()
    return adapter, conn, conn.cursor()


def test_dialect_for():
    assert runner.dialect_for("postgresql") == "postgresql"
    assert runner.dialect_for("sqlite") == "sqlite"
    assert runner.dialect_for("anything-else") == "sqlite"


def test_discover_includes_baseline_in_order():
    revs = [m.revision for m in runner.discover()]
    assert revs[0] == "0001"
    assert revs == sorted(revs)


def test_ensure_version_table_and_current_version(tmp_path):
    adapter, conn, cur = _cur(tmp_path)
    runner.ensure_version_table(cur, "sqlite")
    conn.commit()
    assert runner.current_version(cur) is None
    cur.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
        ("0001", "baseline", "2026-06-29T00:00:00Z"),
    )
    conn.commit()
    assert runner.current_version(cur) == "0001"
    assert runner.applied_versions(cur) == {"0001"}
    adapter.close()
