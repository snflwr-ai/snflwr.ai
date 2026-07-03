"""
WAL-safe backup: an uncheckpointed write (only in the -wal file) must survive
the backup. Without a checkpoint before shutil.copy2, WAL-only data is silently
missing from the backup file.
"""

import sqlite3


def test_backup_captures_uncheckpointed_wal_write(tmp_path, monkeypatch):
    """With WAL on, a write that hasn't been checkpointed lives in the -wal file.
    backup_sqlite must checkpoint before copy so the backup is complete."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.commit()
    conn.execute("INSERT INTO t VALUES ('in-wal')")
    conn.commit()  # committed but still in -wal (no checkpoint yet)

    from config import system_config

    monkeypatch.setattr(system_config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", False, raising=False)
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setenv("COMPRESS_BACKUPS", "false")

    import importlib

    import scripts.backup_database as bmod

    importlib.reload(bmod)
    ok, _ = bmod.DatabaseBackup().backup_sqlite()
    assert ok

    backup = sorted((tmp_path / "backups").glob("snflwr_sqlite_*.db"))[-1]
    # Open the BACKUP as a standalone file (no sibling -wal) — the row must be there.
    b = sqlite3.connect(str(backup))
    row = b.execute("SELECT v FROM t").fetchone()
    b.close()
    conn.close()
    assert row is not None and row[0] == "in-wal"
