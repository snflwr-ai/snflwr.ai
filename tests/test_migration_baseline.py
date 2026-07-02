"""0001_baseline applies the full current schema, idempotently, on both dialects
(sqlite exercised here; postgres covered in the integration test)."""
import importlib

import pytest

from storage.db_adapters import create_adapter


def _sqlite_cursor(tmp_path):
    adapter = create_adapter("sqlite", db_path=str(tmp_path / "t.db"))
    conn = adapter.connect()
    return adapter, conn, conn.cursor()


# NOTE: the module name starts with a digit, so it CANNOT be imported with
# dotted `import` syntax — always load it via importlib.import_module(<string>).
BASELINE = "database.migrations.0001_baseline"


def test_baseline_up_creates_core_tables(tmp_path):
    mod = importlib.import_module(BASELINE)
    adapter, conn, cur = _sqlite_cursor(tmp_path)
    mod.up(cur, "sqlite")
    conn.commit()
    names = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    adapter.close()
    assert {"accounts", "child_profiles", "safety_incidents"} <= names


def test_baseline_up_is_idempotent(tmp_path):
    mod = importlib.import_module(BASELINE)
    adapter, conn, cur = _sqlite_cursor(tmp_path)
    mod.up(cur, "sqlite")
    conn.commit()
    mod.up(cur, "sqlite")  # second run must not raise
    conn.commit()
    adapter.close()


def test_baseline_down_is_irreversible(tmp_path):
    mod = importlib.import_module(BASELINE)
    from database.migrations.runner import IrreversibleMigration
    adapter, conn, cur = _sqlite_cursor(tmp_path)
    with pytest.raises(IrreversibleMigration):
        mod.down(cur, "sqlite")
    adapter.close()
