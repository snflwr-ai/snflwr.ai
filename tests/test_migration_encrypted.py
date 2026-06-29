"""The runner must upgrade an ENCRYPTED (SQLCipher) database end-to-end."""

import pytest

from storage.encrypted_db_adapter import SQLCIPHER_AVAILABLE

pytestmark = pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE, reason="SQLCipher not installed"
)

LONG_KEY = "a" * 32 + "b" * 32


def test_upgrade_encrypted_sqlite(tmp_path, monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "DB_TYPE", "sqlite")
    monkeypatch.setattr(system_config, "DB_PATH", str(tmp_path / "enc.db"))
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_KEY", LONG_KEY)

    import storage.database as dbmod

    mgr = dbmod.DatabaseManager()
    from database.migrations import runner

    runner.upgrade("head", manager=mgr)

    conn = mgr.adapter.connect()
    cur = conn.cursor()
    names = {
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "accounts" in names and "schema_migrations" in names
    mgr.adapter.close()
