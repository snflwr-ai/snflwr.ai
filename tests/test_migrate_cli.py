import database.migrate as cli


def test_status_command_runs(capsys, tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "DB_TYPE", "sqlite")
    monkeypatch.setattr(system_config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", False)
    # Force a fresh DatabaseManager bound to the temp path.
    import storage.database as dbmod
    monkeypatch.setattr(dbmod, "db_manager", dbmod.DatabaseManager())
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "pending" in out.lower() or "current" in out.lower()


def test_new_scaffolds_migration(tmp_path, monkeypatch):
    from database.migrations import runner
    created = runner._MIGRATIONS_DIR / "9999_example_change.py"
    if created.exists():
        created.unlink()
    rc = cli.main(["new", "example_change"])
    assert rc == 0
    files = list(runner._MIGRATIONS_DIR.glob("*_example_change.py"))
    assert files, "scaffold not created"
    text = files[0].read_text()
    assert "def up(cursor, dialect):" in text and "def down(cursor, dialect):" in text
    files[0].unlink()  # cleanup scaffold
