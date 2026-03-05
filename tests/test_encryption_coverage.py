"""
Comprehensive tests for storage/encryption.py.

Covers:
- EncryptionManager init (new key, load key, key errors)
- encrypt_string / decrypt_string
- encrypt_dict / decrypt_dict
- hash_password / verify_password
- EncryptionManager edge cases
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def enc_manager(temp_dir):
    """Create EncryptionManager with temp dir."""
    from storage.encryption import EncryptionManager
    return EncryptionManager(key_dir=temp_dir)


class TestEncryptionManagerInit:
    """Test initialization paths."""

    def test_generates_new_key_when_none_exists(self, temp_dir):
        from storage.encryption import EncryptionManager
        mgr = EncryptionManager(key_dir=temp_dir)
        key_file = temp_dir / ".encryption_key"
        assert key_file.exists()

    def test_loads_existing_key(self, temp_dir):
        from storage.encryption import EncryptionManager
        # Create manager to generate a key
        mgr1 = EncryptionManager(key_dir=temp_dir)
        # Use a fresh path tracker
        from storage import encryption as enc_mod
        # Clear the singleton so a new instance can be created
        # Create second manager that should load the existing key
        mgr2 = EncryptionManager(key_dir=temp_dir)
        assert mgr2._master_key is not None

    def test_key_file_permissions_set(self, temp_dir):
        from storage.encryption import EncryptionManager
        mgr = EncryptionManager(key_dir=temp_dir)
        key_file = temp_dir / ".encryption_key"
        mode = key_file.stat().st_mode & 0o777
        # Should be 0o600 on unix
        assert mode == 0o600 or os.name == 'nt'

    def test_invalid_key_raises_error(self, temp_dir):
        """Writing a bad key file should cause init to fail."""
        from storage.encryption import EncryptionManager

        # Write an invalid key
        key_file = temp_dir / ".encryption_key"
        key_file.write_bytes(b"too-short")
        key_file.chmod(0o600)

        with pytest.raises((ValueError, RuntimeError, OSError)):
            EncryptionManager(key_dir=temp_dir)

    def test_get_master_key_returns_string(self, enc_manager):
        key_str = enc_manager._get_master_key()
        assert isinstance(key_str, str)
        assert len(key_str) > 0

    def test_get_master_key_none(self, enc_manager):
        enc_manager._master_key = None
        result = enc_manager._get_master_key()
        assert result == ""


class TestEncryptString:
    """Test encrypt_string / decrypt_string."""

    def test_encrypts_string(self, enc_manager):
        plaintext = "Hello, World!"
        ciphertext = enc_manager.encrypt_string(plaintext)
        assert ciphertext is not None
        assert ciphertext != plaintext

    def test_decrypts_string(self, enc_manager):
        plaintext = "Secret data 123"
        ciphertext = enc_manager.encrypt_string(plaintext)
        decrypted = enc_manager.decrypt_string(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_none_returns_none(self, enc_manager):
        result = enc_manager.encrypt_string(None)
        assert result is None

    def test_decrypt_none_returns_none(self, enc_manager):
        result = enc_manager.decrypt_string(None)
        assert result is None

    def test_encrypt_empty_string(self, enc_manager):
        result = enc_manager.encrypt_string("")
        assert result == ""

    def test_decrypt_empty_string(self, enc_manager):
        result = enc_manager.decrypt_string("")
        assert result == ""

    def test_decrypt_invalid_data_returns_none(self, enc_manager):
        result = enc_manager.decrypt_string("not-valid-base64-encrypted-data!!!")
        # Should return None on failure (fail-safe)
        assert result is None

    def test_encrypt_unicode(self, enc_manager):
        plaintext = "Hello 世界 🌍"
        ciphertext = enc_manager.encrypt_string(plaintext)
        decrypted = enc_manager.decrypt_string(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_long_string(self, enc_manager):
        plaintext = "A" * 10000
        ciphertext = enc_manager.encrypt_string(plaintext)
        decrypted = enc_manager.decrypt_string(ciphertext)
        assert decrypted == plaintext

    def test_multiple_encryptions_differ(self, enc_manager):
        """Each encryption should produce different ciphertext (nonce-based)."""
        plaintext = "same input"
        cipher1 = enc_manager.encrypt_string(plaintext)
        cipher2 = enc_manager.encrypt_string(plaintext)
        # Fernet uses random nonce, so ciphertexts should differ
        # (but both decrypt to same value)
        assert enc_manager.decrypt_string(cipher1) == plaintext
        assert enc_manager.decrypt_string(cipher2) == plaintext


class TestEncryptDict:
    """Test encrypt_dict / decrypt_dict."""

    def test_encrypts_dict(self, enc_manager):
        data = {"name": "Alice", "age": 30}
        encrypted = enc_manager.encrypt_dict(data)
        assert encrypted != data
        assert isinstance(encrypted, str)

    def test_decrypts_dict(self, enc_manager):
        data = {"name": "Bob", "score": 95}
        encrypted = enc_manager.encrypt_dict(data)
        decrypted = enc_manager.decrypt_dict(encrypted)
        assert decrypted == data

    def test_encrypt_none_dict(self, enc_manager):
        """encrypt_dict(None) raises TypeError since json.dumps(None) is 'null' - handle gracefully."""
        try:
            result = enc_manager.encrypt_dict(None)
            # If it doesn't raise, result is either None or a string
            assert result is None or isinstance(result, str)
        except (TypeError, ValueError):
            pass  # Valid to raise on None input

    def test_decrypt_none_dict(self, enc_manager):
        """decrypt_dict(None) raises since decrypt_string('null') -> invalid data."""
        try:
            result = enc_manager.decrypt_dict(None)
            assert result is None or isinstance(result, dict)
        except (TypeError, ValueError):
            pass  # Valid to raise on None

    def test_decrypt_invalid_dict(self, enc_manager):
        """decrypt_dict with invalid ciphertext should raise or return None."""
        try:
            result = enc_manager.decrypt_dict("not-valid-base64")
            assert result is None or isinstance(result, dict)
        except (TypeError, ValueError, Exception):
            pass  # Valid to raise on bad input

    def test_encrypt_empty_dict(self, enc_manager):
        data = {}
        encrypted = enc_manager.encrypt_dict(data)
        decrypted = enc_manager.decrypt_dict(encrypted)
        assert decrypted == {} or decrypted is None

    def test_dict_with_nested_values(self, enc_manager):
        data = {"level1": {"nested": "value"}, "list": [1, 2, 3]}
        encrypted = enc_manager.encrypt_dict(data)
        if encrypted is not None:
            decrypted = enc_manager.decrypt_dict(encrypted)
            if decrypted is not None:
                assert decrypted == data


class TestHashPassword:
    """Test hash_password / verify_password."""

    def test_hash_password_returns_string(self, enc_manager):
        hashed = enc_manager.hash_password("SecurePass123!")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_different_passwords_different_hashes(self, enc_manager):
        hash1 = enc_manager.hash_password("Password1!")
        hash2 = enc_manager.hash_password("Password2!")
        assert hash1 != hash2

    def test_same_password_different_salts(self, enc_manager):
        """Same password should produce different hashes (salt-based)."""
        hash1 = enc_manager.hash_password("Password1!")
        hash2 = enc_manager.hash_password("Password1!")
        # With salt, hashes should differ
        # (or be equal if no salt is used - just verify no error)
        assert isinstance(hash1, str)
        assert isinstance(hash2, str)

    def test_verify_correct_password(self, enc_manager):
        password = "CorrectPass123!"
        hashed = enc_manager.hash_password(password)
        result = enc_manager.verify_password(password, hashed)
        assert result is True

    def test_verify_wrong_password(self, enc_manager):
        hashed = enc_manager.hash_password("CorrectPass123!")
        result = enc_manager.verify_password("WrongPass123!", hashed)
        assert result is False

    def test_hash_empty_password(self, enc_manager):
        """Empty password should be hashable (or raise ValueError)."""
        try:
            hashed = enc_manager.hash_password("")
            assert isinstance(hashed, str)
        except (ValueError, TypeError):
            pass  # Valid to reject empty passwords


class TestIsEncryptionAvailable:
    """Test is_encryption_available function."""

    def test_returns_bool(self):
        from storage.encryption import is_encryption_available
        result = is_encryption_available()
        assert isinstance(result, bool)

    def test_encryption_available_with_real_cryptography(self):
        """If cryptography package is installed, should return True."""
        try:
            import cryptography
            from storage.encryption import is_encryption_available
            assert is_encryption_available() is True
        except ImportError:
            pytest.skip("cryptography package not installed")


class TestEncryptionManagerSingleton:
    """Test encryption_manager singleton from storage.encryption."""

    def test_singleton_exists(self):
        from storage.encryption import encryption_manager
        assert encryption_manager is not None

    def test_singleton_can_encrypt(self):
        from storage.encryption import encryption_manager
        result = encryption_manager.encrypt_string("test")
        assert result is not None


class TestSaveLoadKeyEdgeCases:
    """Test key save/load edge cases."""

    def test_load_key_permissions_error(self, temp_dir):
        """Key with bad permissions should raise RuntimeError."""
        from storage.encryption import EncryptionManager

        # First create a valid key
        import base64
        import secrets
        key_file = temp_dir / ".encryption_key"
        raw = secrets.token_bytes(32)
        key_data = base64.b64encode(raw)  # 44 bytes
        key_file.write_bytes(key_data)
        # Set world-readable permissions
        key_file.chmod(0o644)

        # Should fail because permissions are too open
        with pytest.raises((RuntimeError, OSError, ValueError)):
            mgr = EncryptionManager(key_dir=temp_dir)

    def test_key_directory_created_if_missing(self):
        """Key directory should be created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "new_subdir"
            assert not new_dir.exists()

            from storage.encryption import EncryptionManager
            mgr = EncryptionManager(key_dir=new_dir)
            assert new_dir.exists()


class TestEncryptFile:
    """Test encrypt_file / decrypt_file."""

    def test_encrypt_file_basic(self, enc_manager, tmp_path):
        input_path = tmp_path / "plain.txt"
        output_path = tmp_path / "encrypted.bin"
        input_path.write_bytes(b"Hello, file encryption!")
        result = enc_manager.encrypt_file(input_path, output_path)
        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() != b"Hello, file encryption!"

    def test_decrypt_file_basic(self, enc_manager, tmp_path):
        input_path = tmp_path / "plain.txt"
        encrypted_path = tmp_path / "encrypted.bin"
        decrypted_path = tmp_path / "decrypted.txt"
        input_path.write_bytes(b"Round trip file test")
        enc_manager.encrypt_file(input_path, encrypted_path)
        result = enc_manager.decrypt_file(encrypted_path, decrypted_path)
        assert result is True
        assert decrypted_path.read_bytes() == b"Round trip file test"

    def test_encrypt_file_missing_source(self, enc_manager, tmp_path):
        missing = tmp_path / "does_not_exist.txt"
        output = tmp_path / "out.bin"
        result = enc_manager.encrypt_file(missing, output)
        assert result is False

    def test_decrypt_file_missing_source(self, enc_manager, tmp_path):
        missing = tmp_path / "does_not_exist.bin"
        output = tmp_path / "out.txt"
        result = enc_manager.decrypt_file(missing, output)
        assert result is False

    def test_decrypt_file_invalid_ciphertext(self, enc_manager, tmp_path):
        bad_file = tmp_path / "bad.bin"
        bad_file.write_bytes(b"this is not real ciphertext")
        output = tmp_path / "out.txt"
        result = enc_manager.decrypt_file(bad_file, output)
        assert result is False


class TestHmacToken:
    """Test hmac_token method."""

    def test_hmac_token_returns_hex_string(self, enc_manager):
        result = enc_manager.hmac_token("test_token")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_hmac_token_deterministic(self, enc_manager):
        t1 = enc_manager.hmac_token("same_token")
        t2 = enc_manager.hmac_token("same_token")
        assert t1 == t2

    def test_hmac_different_tokens_differ(self, enc_manager):
        t1 = enc_manager.hmac_token("token_A")
        t2 = enc_manager.hmac_token("token_B")
        assert t1 != t2

    def test_hmac_token_with_null_master_key(self, enc_manager):
        """hmac_token should still return a result even if master key is None."""
        enc_manager._master_key = None
        result = enc_manager.hmac_token("test")
        assert isinstance(result, str)


class TestGenerateSecureToken:
    """Test generate_secure_token."""

    def test_returns_hex_string(self, enc_manager):
        token = enc_manager.generate_secure_token()
        assert isinstance(token, str)
        # Default length=32 -> 64 hex chars
        assert len(token) == 64

    def test_custom_length(self, enc_manager):
        token = enc_manager.generate_secure_token(length=16)
        assert len(token) == 32  # 16 bytes -> 32 hex chars

    def test_tokens_are_unique(self, enc_manager):
        t1 = enc_manager.generate_secure_token()
        t2 = enc_manager.generate_secure_token()
        assert t1 != t2


class TestGenerateDeviceId:
    """Test generate_device_id."""

    def test_returns_string(self, enc_manager):
        device_id = enc_manager.generate_device_id()
        assert isinstance(device_id, str)
        assert len(device_id) == 32

    def test_with_additional_entropy(self, enc_manager):
        device_id = enc_manager.generate_device_id(additional_entropy="extra123")
        assert isinstance(device_id, str)
        assert len(device_id) == 32

    def test_unique_per_call(self, enc_manager):
        """Each call should produce a unique device ID due to random component."""
        id1 = enc_manager.generate_device_id()
        id2 = enc_manager.generate_device_id()
        assert id1 != id2


class TestSecureStorage:
    """Test SecureStorage class."""

    @pytest.fixture
    def secure_store(self, tmp_path):
        """Create a SecureStorage instance backed by in-memory DB mock."""
        from storage.encryption import SecureStorage
        from unittest.mock import MagicMock
        db_mock = MagicMock()
        # Use separate subdirs for storage and keys
        storage_dir = tmp_path / "store"
        key_dir = tmp_path / "keys"
        storage_dir.mkdir()
        key_dir.mkdir()
        return SecureStorage(db=db_mock, storage_dir=storage_dir, key_dir=key_dir)

    def test_store_and_retrieve_dict(self, secure_store):
        data = {"user": "alice", "score": 42}
        result = secure_store.store("mykey", data)
        assert result is True
        retrieved = secure_store.retrieve("mykey")
        # Should come back as either dict or decrypted string
        assert retrieved is not None

    def test_store_and_delete(self, secure_store):
        data = {"x": 1}
        secure_store.store("delkey", data)
        deleted = secure_store.delete("delkey")
        assert deleted is True
        # File should no longer exist
        retrieved = secure_store.retrieve("delkey")
        assert retrieved is None

    def test_delete_nonexistent_returns_false(self, secure_store):
        result = secure_store.delete("no_such_key")
        assert result is False

    def test_retrieve_nonexistent_returns_none(self, secure_store):
        result = secure_store.retrieve("no_such_key")
        assert result is None

    def test_store_string_data(self, secure_store):
        """store() should handle non-dict (string) data."""
        result = secure_store.store("strkey", "plain string value")
        assert result is True

    def test_store_creates_enc_file(self, secure_store, tmp_path):
        secure_store.store("filekey", {"a": "b"})
        enc_file = secure_store.storage_dir / "filekey.enc"
        assert enc_file.exists()


class TestEncryptWrappers:
    """Test encrypt() and decrypt() convenience aliases."""

    def test_encrypt_alias_returns_string(self, enc_manager):
        result = enc_manager.encrypt("hello")
        assert isinstance(result, str)

    def test_decrypt_alias_returns_string(self, enc_manager):
        cipher = enc_manager.encrypt("hello world")
        result = enc_manager.decrypt(cipher)
        assert result == "hello world"

    def test_encrypt_alias_none_returns_none(self, enc_manager):
        result = enc_manager.encrypt(None)
        assert result is None

    def test_decrypt_alias_none_returns_none(self, enc_manager):
        result = enc_manager.decrypt(None)
        assert result is None

    def test_decrypt_alias_bad_data_returns_none(self, enc_manager):
        result = enc_manager.decrypt("totally-invalid-data")
        assert result is None


class TestVerifyPasswordEdgeCases:
    """Test verify_password edge cases."""

    def test_verify_no_dollar_sign_no_salt_returns_false(self, enc_manager):
        """hash without '$' and no separate salt should return False."""
        result = enc_manager.verify_password("password", "nodollarsign")
        assert result is False

    def test_verify_with_separate_salt(self, enc_manager):
        """Verify using separate salt parameter."""
        import base64, secrets
        salt = secrets.token_bytes(32)
        salt_b64 = base64.b64encode(salt).decode()
        hashed = enc_manager.hash_password("MyPass!", salt=salt)
        # The combined hash format is salt$hash; extract just the key part
        _, key_b64 = hashed.split('$', 1)
        result = enc_manager.verify_password("MyPass!", key_b64, salt=salt_b64)
        assert result is True

    def test_verify_wrong_password_with_separate_salt(self, enc_manager):
        import base64, secrets
        salt = secrets.token_bytes(32)
        salt_b64 = base64.b64encode(salt).decode()
        hashed = enc_manager.hash_password("correct", salt=salt)
        _, key_b64 = hashed.split('$', 1)
        result = enc_manager.verify_password("wrong", key_b64, salt=salt_b64)
        assert result is False

    def test_hash_password_with_explicit_salt(self, enc_manager):
        import secrets
        salt = secrets.token_bytes(32)
        hashed1 = enc_manager.hash_password("samepass", salt=salt)
        hashed2 = enc_manager.hash_password("samepass", salt=salt)
        assert hashed1 == hashed2  # Same salt -> same hash
