"""
Tests for storage/encrypted_db_adapter.py — SQLCipher Encrypted Database

Covers:
    - EncryptedSQLiteAdapter: init, key validation, connect, encryption info
    - Fallback behavior when SQLCipher is unavailable
    - create_encrypted_database helper
    - test_encryption_key helper

Since SQLCipher (pysqlcipher3) is typically not installed in test environments,
these tests exercise both the "available" and "unavailable" code paths by
mocking SQLCIPHER_AVAILABLE.
"""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


@pytest.fixture
def tmp_db():
    d = tempfile.mkdtemp()
    yield Path(d) / "test_encrypted.db"
    import shutil
    shutil.rmtree(d, ignore_errors=True)


LONG_KEY = "a" * 32 + "b" * 32  # 64-char key (well above minimum)


# ==========================================================================
# Init — Key Validation
# ==========================================================================

class TestEncryptedAdapterInit:

    def test_no_key_raises(self, tmp_db):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DB_ENCRYPTION_KEY', None)
            with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
                from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
                with pytest.raises(ValueError, match="encryption key not provided"):
                    EncryptedSQLiteAdapter(tmp_db, encryption_key=None)

    def test_short_key_warns(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            # Short key (< 32) should warn but not raise in dev mode
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key="short-key-20chars!!", require_encryption=False
            )
            assert adapter.encryption_key == "short-key-20chars!!"

    def test_key_from_env(self, tmp_db):
        with patch.dict(os.environ, {'DB_ENCRYPTION_KEY': LONG_KEY}):
            with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
                from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
                adapter = EncryptedSQLiteAdapter(
                    tmp_db, encryption_key=None, require_encryption=False
                )
                assert adapter.encryption_key == LONG_KEY


# ==========================================================================
# SQLCipher Not Available
# ==========================================================================

class TestSqlcipherUnavailable:

    def test_dev_mode_allows_unencrypted(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            assert adapter.is_encrypted is False

    def test_production_mode_raises(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            with pytest.raises(RuntimeError, match="FERPA"):
                EncryptedSQLiteAdapter(
                    tmp_db, encryption_key=LONG_KEY, require_encryption=True
                )

    def test_auto_detect_production(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            with patch.dict(os.environ, {'ENVIRONMENT': 'production'}):
                from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
                with pytest.raises(RuntimeError, match="FERPA"):
                    EncryptedSQLiteAdapter(tmp_db, encryption_key=LONG_KEY)

    def test_auto_detect_development(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            with patch.dict(os.environ, {'ENVIRONMENT': 'development'}):
                from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
                # Should not raise in development
                adapter = EncryptedSQLiteAdapter(tmp_db, encryption_key=LONG_KEY)
                assert adapter.require_encryption is False

    def test_connect_falls_back_to_sqlite(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            conn = adapter.connect()
            assert conn is not None
            # Should be standard sqlite connection
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.execute("INSERT INTO test VALUES (1)")
            cursor.execute("SELECT * FROM test")
            assert cursor.fetchone()[0] == 1
            cursor.close()
            adapter.close()


# ==========================================================================
# Encryption Info
# ==========================================================================

class TestEncryptionInfo:

    def test_info_when_encrypted(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", True):
            # Don't actually connect — just check info
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter.__new__(EncryptedSQLiteAdapter)
            adapter.is_encrypted = True
            adapter.kdf_iter = 256000
            adapter.db_path = tmp_db
            info = adapter.get_encryption_info()
            assert info["encrypted"] is True
            assert info["cipher"] == "AES-256"
            assert info["kdf_iterations"] == 256000

    def test_info_when_not_encrypted(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            info = adapter.get_encryption_info()
            assert info["encrypted"] is False
            assert info["cipher"] == "None"

    def test_is_database_encrypted(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            assert adapter.is_database_encrypted() is False


# ==========================================================================
# Helper Functions
# ==========================================================================

class TestHelperFunctions:

    def test_create_encrypted_database(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import create_encrypted_database
            adapter = create_encrypted_database(tmp_db, encryption_key=LONG_KEY)
            assert adapter.connection is not None
            adapter.close()

    def test_test_encryption_key_valid(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import create_encrypted_database, test_encryption_key
            # Create the database first
            adapter = create_encrypted_database(tmp_db, encryption_key=LONG_KEY)
            adapter.close()
            # Test the key
            result = test_encryption_key(tmp_db, LONG_KEY)
            assert result is True

    def test_test_encryption_key_invalid(self, tmp_db):
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import test_encryption_key
            # Non-existent DB with bogus key should still work in non-encrypted mode
            result = test_encryption_key(tmp_db, LONG_KEY)
            assert result is True  # SQLite fallback always "works"


# ==========================================================================
# Connect with SQLCipher available (mocked)
# ==========================================================================

class TestConnectWithSqlcipher:

    def test_connect_with_sqlcipher(self, tmp_db):
        """Test that SQLCipher connection path works when available (mocked)."""
        mock_sqlcipher = MagicMock()
        mock_conn = MagicMock()
        mock_sqlcipher.connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", True), \
             patch("storage.encrypted_db_adapter.sqlcipher", mock_sqlcipher, create=True):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            adapter.is_encrypted = True
            conn = adapter.connect()
            # Should have called sqlcipher.connect
            mock_sqlcipher.connect.assert_called_once()
            # Should have set encryption key
            mock_conn.execute.assert_any_call(f"PRAGMA key = '{LONG_KEY}'")

    def test_connect_invalid_key_characters(self, tmp_db):
        """Key with SQL injection characters should be rejected."""
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", True):
            mock_sqlcipher = MagicMock()
            mock_conn = MagicMock()
            mock_sqlcipher.connect.return_value = mock_conn
            with patch("storage.encrypted_db_adapter.sqlcipher", mock_sqlcipher, create=True):
                from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
                adapter = EncryptedSQLiteAdapter(
                    tmp_db, encryption_key=LONG_KEY + ";DROP TABLE", require_encryption=False
                )
                adapter.is_encrypted = True
                with pytest.raises(ValueError, match="invalid characters"):
                    adapter.connect()

    def test_connect_invalid_kdf_iter(self, tmp_db):
        """Invalid kdf_iter should be rejected."""
        with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", False):
            from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
            adapter = EncryptedSQLiteAdapter(
                tmp_db, encryption_key=LONG_KEY, require_encryption=False
            )
            adapter.kdf_iter = "not_an_int"
            adapter.is_encrypted = True
            adapter.connection = None  # Force reconnect path

            mock_sqlcipher = MagicMock()
            mock_conn = MagicMock()
            mock_sqlcipher.connect.return_value = mock_conn

            with patch("storage.encrypted_db_adapter.SQLCIPHER_AVAILABLE", True), \
                 patch("storage.encrypted_db_adapter.sqlcipher", mock_sqlcipher, create=True):
                with pytest.raises(ValueError, match="Invalid kdf_iter"):
                    adapter.connect()
