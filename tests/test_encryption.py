"""
Test Suite for Encryption System
Tests AES-256 encryption, key generation, secure storage, and data protection
"""

import pytest
from pathlib import Path
import tempfile
import shutil
import os
import json

from storage.encryption import (
    EncryptionManager,
    SecureStorage,
    encryption_manager
)
from storage.database import DatabaseManager


@pytest.fixture
def temp_dir():
    """Create temporary directory for encryption tests"""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def encryption_mgr(temp_dir):
    """Create encryption manager with temporary directory"""
    return EncryptionManager(temp_dir)


@pytest.fixture
def temp_db(temp_dir):
    """Create temporary database"""
    db_path = temp_dir / "test.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    db.close()


class TestKeyGeneration:
    """Test encryption key generation and management"""

    def test_generate_master_key_with_permissions(self, temp_dir):
        """Test master key generation and file permissions"""
        EncryptionManager(temp_dir)

        key_file = temp_dir / ".encryption_key"
        assert key_file.exists()

        if os.name != 'nt':
            stat = key_file.stat()
            assert (stat.st_mode & 0o077) == 0  # No group/other access

    def test_key_is_base64_encoded(self, encryption_mgr):
        """Test encryption key is properly base64 encoded"""
        key = encryption_mgr._get_master_key()
        assert len(key) == 44  # Fernet key length
        assert all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in key)

    def test_key_persistence_across_instances(self, temp_dir):
        """Test encryption key persists across instances"""
        key1 = EncryptionManager(temp_dir)._get_master_key()
        key2 = EncryptionManager(temp_dir)._get_master_key()
        assert key1 == key2


class TestBasicEncryption:
    """Test basic encryption and decryption operations"""

    @pytest.mark.parametrize("plaintext", [
        "Hello, World!",
        "",
        "Hello 世界 🌻 émojis",
        "A" * (1024 * 1024),  # 1MB
    ])
    def test_encrypt_decrypt_round_trip(self, encryption_mgr, plaintext):
        """Test encrypt/decrypt round trip for various inputs"""
        encrypted = encryption_mgr.encrypt(plaintext)
        decrypted = encryption_mgr.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypted_data_differs_each_time(self, encryption_mgr):
        """Test same plaintext produces different ciphertext (random IV)"""
        plaintext = "Same message"
        encrypted1 = encryption_mgr.encrypt(plaintext)
        encrypted2 = encryption_mgr.encrypt(plaintext)

        assert encrypted1 != encrypted2
        assert encryption_mgr.decrypt(encrypted1) == plaintext
        assert encryption_mgr.decrypt(encrypted2) == plaintext

    def test_decrypt_invalid_data_returns_none(self, encryption_mgr):
        """Test decrypting invalid data returns None"""
        assert encryption_mgr.decrypt("not_valid_encrypted_data") is None

    def test_decrypt_tampered_data_returns_none(self, encryption_mgr):
        """Test decrypting tampered data returns None"""
        encrypted = encryption_mgr.encrypt("Original message")
        tampered = encrypted[:-5] + "XXXXX"
        assert encryption_mgr.decrypt(tampered) is None

    def test_encrypt_json_data(self, encryption_mgr):
        """Test encrypting JSON data structures"""
        data = {
            "name": "Emma",
            "age": 10,
            "subjects": ["math", "science"],
            "scores": {"math": 95, "science": 88}
        }
        plaintext = json.dumps(data)
        encrypted = encryption_mgr.encrypt(plaintext)
        decrypted = encryption_mgr.decrypt(encrypted)
        assert json.loads(decrypted) == data


class TestFileEncryption:
    """Test file encryption and decryption"""

    def test_encrypt_decrypt_text_file(self, encryption_mgr, temp_dir):
        """Test text file encrypt/decrypt round trip"""
        test_file = temp_dir / "test.txt"
        content = "This is secret content"
        test_file.write_text(content)

        encrypted_file = temp_dir / "test.txt.enc"
        assert encryption_mgr.encrypt_file(test_file, encrypted_file) is True
        assert encrypted_file.read_text() != content

        decrypted_file = temp_dir / "test_decrypted.txt"
        assert encryption_mgr.decrypt_file(encrypted_file, decrypted_file) is True
        assert decrypted_file.read_text() == content

    def test_encrypt_decrypt_binary_file(self, encryption_mgr, temp_dir):
        """Test binary file encrypt/decrypt round trip"""
        test_file = temp_dir / "test.bin"
        binary_data = bytes([i % 256 for i in range(1000)])
        test_file.write_bytes(binary_data)

        encrypted_file = temp_dir / "test.bin.enc"
        encryption_mgr.encrypt_file(test_file, encrypted_file)

        decrypted_file = temp_dir / "test_decrypted.bin"
        encryption_mgr.decrypt_file(encrypted_file, decrypted_file)

        assert decrypted_file.read_bytes() == binary_data


class TestSecureStorage:
    """Test SecureStorage class for database encryption"""

    def test_store_and_retrieve(self, temp_db, temp_dir):
        """Test storing and retrieving encrypted value"""
        storage = SecureStorage(temp_db, temp_dir)
        storage.store("test_key", "secret_value")
        assert storage.retrieve("test_key") == "secret_value"

    def test_retrieve_nonexistent_key(self, temp_db, temp_dir):
        """Test retrieving non-existent key returns None"""
        storage = SecureStorage(temp_db, temp_dir)
        assert storage.retrieve("nonexistent_key") is None

    def test_update_value(self, temp_db, temp_dir):
        """Test updating encrypted value"""
        storage = SecureStorage(temp_db, temp_dir)
        storage.store("test_key", "original_value")
        storage.store("test_key", "updated_value")
        assert storage.retrieve("test_key") == "updated_value"

    def test_delete_value(self, temp_db, temp_dir):
        """Test deleting encrypted value"""
        storage = SecureStorage(temp_db, temp_dir)
        storage.store("test_key", "value_to_delete")
        assert storage.delete("test_key") is True
        assert storage.retrieve("test_key") is None

    def test_store_complex_data(self, temp_db, temp_dir):
        """Test storing complex nested data"""
        storage = SecureStorage(temp_db, temp_dir)
        data = {
            "profiles": [
                {"name": "Emma", "scores": [95, 88]},
                {"name": "Alex", "scores": [85, 90]}
            ],
        }
        storage.store("complex_data", json.dumps(data))
        assert json.loads(storage.retrieve("complex_data")) == data


class TestEncryptionErrorHandling:
    """Test encryption error handling and edge cases"""

    def test_encrypt_none_returns_none(self, encryption_mgr):
        """Test encrypting None returns None"""
        assert encryption_mgr.encrypt(None) is None

    def test_decrypt_none_returns_none(self, encryption_mgr):
        """Test decrypting None returns None"""
        assert encryption_mgr.decrypt(None) is None

    def test_decrypt_empty_string(self, encryption_mgr):
        """Test decrypting empty string"""
        assert encryption_mgr.decrypt("") == ""

    def test_encrypt_file_nonexistent_source(self, encryption_mgr, temp_dir):
        """Test encrypting non-existent file returns False"""
        assert encryption_mgr.encrypt_file(temp_dir / "nope.txt", temp_dir / "out.enc") is False

    def test_decrypt_file_nonexistent_source(self, encryption_mgr, temp_dir):
        """Test decrypting non-existent file returns False"""
        assert encryption_mgr.decrypt_file(temp_dir / "nope.enc", temp_dir / "out.txt") is False


class TestPasswordHashing:
    """Test password hashing"""

    def test_hash_and_verify_password(self, encryption_mgr):
        """Test password hashing and verification round trip"""
        password = "SecurePassword123"
        hashed = encryption_mgr.hash_password(password)

        assert hashed is not None
        assert hashed != password
        assert len(hashed) > 50
        assert encryption_mgr.verify_password(password, hashed) is True
        assert encryption_mgr.verify_password("WrongPassword", hashed) is False

    def test_same_password_different_hashes(self, encryption_mgr):
        """Test same password produces different hashes (salted)"""
        password = "SecurePassword123"
        hash1 = encryption_mgr.hash_password(password)
        hash2 = encryption_mgr.hash_password(password)

        assert hash1 != hash2
        assert encryption_mgr.verify_password(password, hash1) is True
        assert encryption_mgr.verify_password(password, hash2) is True


class TestHmacToken:
    """Test HMAC token hashing for encrypted search index."""

    def test_hmac_produces_hex_string(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        result = enc.hmac_token("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_hmac_deterministic(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        assert enc.hmac_token("hello") == enc.hmac_token("hello")

    def test_hmac_different_tokens_differ(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        assert enc.hmac_token("hello") != enc.hmac_token("world")

    def test_hmac_same_key_same_result(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc1 = EncryptionManager(key_dir=tmp_path)
        enc2 = EncryptionManager(key_dir=tmp_path)
        assert enc1.hmac_token("test") == enc2.hmac_token("test")

    def test_hmac_different_key_different_result(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc1 = EncryptionManager(key_dir=tmp_path / "k1")
        enc2 = EncryptionManager(key_dir=tmp_path / "k2")
        assert enc1.hmac_token("test") != enc2.hmac_token("test")

    def test_hmac_empty_string(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        result = enc.hmac_token("")
        assert isinstance(result, str)
        assert len(result) == 64
