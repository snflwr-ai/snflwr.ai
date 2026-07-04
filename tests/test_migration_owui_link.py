"""Tests for migration 0003: partial unique index on owui_user_id + accounts.phone."""
import sqlite3
import importlib


def _load_up():
    mod = importlib.import_module("database.migrations.0003_owui_link_and_phone")
    return mod


def _base_schema(cur):
    cur.execute(
        "CREATE TABLE child_profiles (profile_id TEXT PRIMARY KEY, "
        "parent_id TEXT, owui_user_id TEXT, is_active INTEGER DEFAULT 1)"
    )
    cur.execute("CREATE TABLE accounts (parent_id TEXT PRIMARY KEY, username TEXT)")


def test_migration_adds_phone_and_unique_owui_index():
    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    _base_schema(cur)
    mod = _load_up()
    mod.up(cur, "sqlite")

    # phone column exists
    cols = [r[1] for r in cur.execute("PRAGMA table_info(accounts)")]
    assert "phone" in cols

    # unique index rejects duplicate non-null owui_user_id
    cur.execute("INSERT INTO child_profiles VALUES ('p1', 'par', 'owui-1', 1)")
    with_dup = False
    try:
        cur.execute("INSERT INTO child_profiles VALUES ('p2', 'par', 'owui-1', 1)")
    except sqlite3.IntegrityError:
        with_dup = True
    assert with_dup, "duplicate owui_user_id must be rejected"

    # NULLs are allowed multiple times (partial index)
    cur.execute("INSERT INTO child_profiles VALUES ('p3', 'par', NULL, 1)")
    cur.execute("INSERT INTO child_profiles VALUES ('p4', 'par', NULL, 1)")


def test_migration_is_idempotent():
    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    _base_schema(cur)
    mod = _load_up()
    mod.up(cur, "sqlite")
    mod.up(cur, "sqlite")  # second run must not raise
    cols = [r[1] for r in cur.execute("PRAGMA table_info(accounts)")]
    assert cols.count("phone") == 1
