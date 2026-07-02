"""Tests that initialize_database() creates the full schema via the migration runner.

TDD: written RED (before the _initialize_database rewrite), then turned GREEN
by Edit 2 (replacing the inline CREATE/ALTER body with runner.upgrade("head")).
"""


def _fresh_manager(tmp_path, monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "DB_TYPE", "sqlite")
    monkeypatch.setattr(system_config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", False)

    import storage.database as dbmod

    # Clear the singleton cache so each call gets a fresh instance
    dbmod.DatabaseManager._instances.clear()
    mgr = dbmod.DatabaseManager()
    return mgr


def test_initialize_database_creates_schema_and_records_baseline(tmp_path, monkeypatch):
    mgr = _fresh_manager(tmp_path, monkeypatch)
    mgr.initialize_database()

    from database.migrations import runner

    conn = mgr.adapter.connect()
    cur = conn.cursor()

    names = {
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "accounts",
        "child_profiles",
        "safety_incidents",
        "schema_migrations",
    } <= names
    assert "0001" in runner.applied_versions(cur)
    mgr.adapter.close()


def test_initialize_database_is_idempotent(tmp_path, monkeypatch):
    """Calling initialize_database twice must not raise."""
    mgr = _fresh_manager(tmp_path, monkeypatch)
    mgr.initialize_database()
    # Re-create a fresh manager against the same db (simulates a second startup)
    import storage.database as dbmod

    dbmod.DatabaseManager._instances.clear()
    mgr2 = dbmod.DatabaseManager()
    mgr2.initialize_database()  # Must not raise
    mgr2.adapter.close()
