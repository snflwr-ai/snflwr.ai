"""
Tests for database/migrate.py — schema migration.

Regression coverage for the encrypted-database path: when DB encryption is
enabled (SQLCipher), the schema migration must open the database through the
encryption-aware adapter. A plain sqlite3.connect() cannot read a SQLCipher
file and fails with "file is not a database" — see storage/database.py for the
same lesson in the initializer.
"""

import pytest

from storage.encrypted_db_adapter import SQLCIPHER_AVAILABLE


pytestmark = pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE,
    reason="SQLCipher (sqlcipher3) not installed — encrypted-path test cannot run",
)


LONG_KEY = "a" * 32 + "b" * 32  # 64-char key, well above the 32-char minimum


def _configure_encrypted(monkeypatch, db_path):
    """Point system_config at an encrypted SQLite DB at db_path."""
    from config import system_config

    monkeypatch.setattr(system_config, "DB_TYPE", "sqlite")
    monkeypatch.setattr(system_config, "DB_PATH", str(db_path))
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(system_config, "DB_ENCRYPTION_KEY", LONG_KEY)


def test_run_schema_migration_opens_encrypted_db(tmp_path, monkeypatch):
    """
    run_schema_migration() must succeed against an encrypted database.

    Reproduces the regression where _migrate_sqlite used a plain
    sqlite3.connect() and raised "file is not a database".
    """
    db_path = tmp_path / "encrypted.db"
    _configure_encrypted(monkeypatch, db_path)

    # Create the DB the same way the app does, so the SQLCipher KDF params match.
    from storage.db_adapters import create_adapter

    adapter = create_adapter("sqlite", db_path=str(db_path))
    conn = adapter.connect()
    # 'accounts' is one of the tables schema.sql defines; give the migration a
    # real, existing table to inspect.
    conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")
    conn.commit()
    adapter.close()

    from database.migrate import run_schema_migration

    # Must not raise "file is not a database".
    changes = run_schema_migration()
    assert isinstance(changes, list)
