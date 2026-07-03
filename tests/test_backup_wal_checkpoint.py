"""
WAL-safe backup: an uncheckpointed write (only in the -wal file) must survive
the backup. Without a checkpoint before shutil.copy2, WAL-only data is silently
missing from the backup file.
"""

import sqlite3

import pytest


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


def test_encrypted_backup_captures_uncheckpointed_wal_write(tmp_path, monkeypatch):
    """Encrypted path: a WAL-only committed write must survive backup.

    Uses a keyed SQLCipher connection to create an encrypted WAL DB, writes a
    committed-but-uncheckpointed row, then runs backup_sqlite with
    DB_ENCRYPTION_ENABLED=True and asserts the row is present in the backup.

    Skips if SQLCipher is not installed in this environment (the code is still
    correct; the unencrypted test above already validates the mechanism).
    """
    # Skip if neither SQLCipher package is installed — match the adapter's
    # import precedence: pysqlcipher3 first, then sqlcipher3.
    try:
        from pysqlcipher3 import dbapi2 as _sqlcipher
    except ImportError:
        try:
            from sqlcipher3 import dbapi2 as _sqlcipher  # type: ignore[import,no-redef]
        except ImportError:
            pytest.skip("SQLCipher (pysqlcipher3 / sqlcipher3) not installed")

    TEST_KEY = "A" * 32  # 32-char minimum required by EncryptedSQLiteAdapter
    db = tmp_path / "encrypted.db"

    # Create an encrypted WAL DB with a committed-but-uncheckpointed row.
    # Keep the connection open (don't close before backup) — mirroring the
    # unencrypted test above. Closing the LAST connection triggers an implicit
    # passive checkpoint in SQLite/SQLCipher (small data = no readers left →
    # WAL merges into the main file), so we must leave raw open until after
    # the backup runs to ensure the data is still WAL-only at copy time.
    raw = _sqlcipher.connect(str(db), check_same_thread=False)
    raw.execute(f"PRAGMA key = '{TEST_KEY}'")
    raw.execute("PRAGMA kdf_iter = 256000")
    raw.execute("PRAGMA cipher_page_size = 4096")
    raw.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
    raw.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("CREATE TABLE t (v TEXT)")
    raw.commit()
    raw.execute("INSERT INTO t VALUES ('encrypted-in-wal')")
    raw.commit()  # committed but still in -wal (raw held open below)

    # Wire up backup_sqlite for the encrypted path.
    from config import system_config

    monkeypatch.setattr(system_config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", True, raising=False)
    monkeypatch.setenv("DB_ENCRYPTION_KEY", TEST_KEY)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setenv("COMPRESS_BACKUPS", "false")

    import importlib

    import scripts.backup_database as bmod

    importlib.reload(bmod)
    ok, _ = bmod.DatabaseBackup().backup_sqlite()
    assert ok

    backup = sorted((tmp_path / "backups").glob("snflwr_sqlite_*.db"))[-1]

    # Open the BACKUP as a standalone file (no sibling -wal) with the key
    # and assert the row is present.
    b = _sqlcipher.connect(str(backup), check_same_thread=False)
    b.execute(f"PRAGMA key = '{TEST_KEY}'")
    b.execute("PRAGMA kdf_iter = 256000")
    b.execute("PRAGMA cipher_page_size = 4096")
    b.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
    b.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
    row = b.execute("SELECT v FROM t").fetchone()
    b.close()
    raw.close()  # close the writer only after verifying the backup

    assert row is not None and row[0] == "encrypted-in-wal"
