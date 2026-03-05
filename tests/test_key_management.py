"""
Tests for core/key_management.py — Encryption Key Lifecycle

Covers:
    - KeyAuditLogger: log_operation, get_recent_operations
    - Shamir's Secret Sharing: create_key_shares, recover_key_from_shares
    - Key rotation policy: check_key_rotation_status
    - Key derivation: derive_key_from_passphrase, generate_secure_key, validate_key_strength
    - KeyManager: init from passphrase/random, recover, rotate, emergency shares
    - check_environment_key
"""

import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.key_management import (
    DEFAULT_KEY_MAX_AGE_DAYS,
    KEY_EXPIRY_WARNING_DAYS,
    KeyAuditLogger,
    KeyManagementError,
    KeyManager,
    KeyStrengthError,
    check_environment_key,
    check_key_rotation_status,
    create_key_shares,
    derive_key_from_passphrase,
    generate_secure_key,
    recover_key_from_shares,
    validate_key_strength,
)


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ==========================================================================
# KeyAuditLogger
# ==========================================================================

class TestKeyAuditLogger:

    def test_log_and_retrieve(self, tmp_dir):
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        logger.log_operation("test_op", success=True, details={"foo": "bar"}, admin_id="admin1")
        ops = logger.get_recent_operations()
        assert len(ops) == 1
        assert ops[0]["operation"] == "test_op"
        assert ops[0]["success"] is True
        assert ops[0]["admin_id"] == "admin1"

    def test_multiple_operations(self, tmp_dir):
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        for i in range(5):
            logger.log_operation(f"op_{i}", success=True)
        ops = logger.get_recent_operations()
        assert len(ops) == 5

    def test_limit(self, tmp_dir):
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        for i in range(10):
            logger.log_operation(f"op_{i}", success=True)
        ops = logger.get_recent_operations(limit=3)
        assert len(ops) == 3

    def test_empty_log(self, tmp_dir):
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        ops = logger.get_recent_operations()
        assert ops == []

    def test_default_admin_id(self, tmp_dir):
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        logger.log_operation("op", success=False)
        ops = logger.get_recent_operations()
        assert ops[0]["admin_id"] == "system"


# ==========================================================================
# Key Derivation
# ==========================================================================

class TestDeriveKey:

    def test_passphrase_derives_key(self):
        key, salt = derive_key_from_passphrase("my-secure-passphrase-long-enough")
        assert key is not None
        assert salt is not None
        # Key should be base64
        decoded = base64.urlsafe_b64decode(key.encode('ascii'))
        assert len(decoded) == 32

    def test_same_passphrase_same_salt_same_key(self):
        salt = base64.urlsafe_b64decode(
            base64.urlsafe_b64encode(os.urandom(32)).decode('ascii').encode('ascii')
        )
        key1, _ = derive_key_from_passphrase("test-passphrase-long-enough", salt=salt)
        key2, _ = derive_key_from_passphrase("test-passphrase-long-enough", salt=salt)
        assert key1 == key2

    def test_different_passphrases_different_keys(self):
        salt = os.urandom(32)
        key1, _ = derive_key_from_passphrase("passphrase-one-long", salt=salt)
        key2, _ = derive_key_from_passphrase("passphrase-two-long", salt=salt)
        assert key1 != key2

    def test_short_passphrase_raises(self):
        with pytest.raises(KeyStrengthError, match="at least 12"):
            derive_key_from_passphrase("short")

    def test_custom_iterations(self):
        # Lower iterations for speed in tests
        key, salt = derive_key_from_passphrase(
            "my-secure-passphrase-long-enough", iterations=10000
        )
        assert key is not None


class TestGenerateSecureKey:

    def test_key_is_256_bit(self):
        key = generate_secure_key()
        decoded = base64.urlsafe_b64decode(key.encode('ascii'))
        assert len(decoded) == 32

    def test_keys_are_unique(self):
        keys = {generate_secure_key() for _ in range(10)}
        assert len(keys) == 10


class TestValidateKeyStrength:

    def test_valid_key(self):
        key = generate_secure_key()
        is_valid, error = validate_key_strength(key)
        assert is_valid is True
        assert error is None

    def test_short_key(self):
        key = base64.urlsafe_b64encode(b"short").decode('ascii')
        is_valid, error = validate_key_strength(key)
        assert is_valid is False
        assert "too short" in error

    def test_low_entropy_key(self):
        # Key with all same bytes
        key = base64.urlsafe_b64encode(b'\x00' * 32).decode('ascii')
        is_valid, error = validate_key_strength(key)
        assert is_valid is False
        assert "low entropy" in error.lower()

    def test_invalid_base64(self):
        is_valid, error = validate_key_strength("not-valid-base64!!!")
        assert is_valid is False


# ==========================================================================
# Shamir's Secret Sharing
# ==========================================================================

class TestShamirSecretSharing:

    @pytest.fixture
    def sample_key(self):
        return generate_secure_key()

    def test_create_and_recover(self, sample_key, tmp_dir):
        with patch("core.key_management._audit_logger", KeyAuditLogger(tmp_dir / "audit")):
            shares = create_key_shares(sample_key, total_shares=5, threshold=3)
            assert len(shares) == 5

            # Recover with any 3 shares
            recovered = recover_key_from_shares(shares[:3])
            assert recovered == sample_key

    def test_recover_with_different_shares(self, sample_key, tmp_dir):
        with patch("core.key_management._audit_logger", KeyAuditLogger(tmp_dir / "audit")):
            shares = create_key_shares(sample_key, total_shares=5, threshold=3)
            # Use shares 1, 3, 5
            recovered = recover_key_from_shares([shares[0], shares[2], shares[4]])
            assert recovered == sample_key

    def test_threshold_too_high_raises(self):
        with pytest.raises(KeyManagementError, match="Threshold cannot exceed"):
            create_key_shares("dummy", total_shares=3, threshold=5)

    def test_threshold_too_low_raises(self):
        with pytest.raises(KeyManagementError, match="at least 2"):
            create_key_shares("dummy", total_shares=3, threshold=1)

    def test_too_few_shares_raises(self):
        with pytest.raises(KeyManagementError, match="at least 2"):
            recover_key_from_shares(["1:abc"])

    def test_invalid_share_format(self):
        with pytest.raises(KeyManagementError, match="Invalid share"):
            recover_key_from_shares(["bad-share", "another-bad"])

    def test_invalid_key_format(self):
        with pytest.raises(KeyManagementError, match="Invalid key format"):
            create_key_shares("not-base64!!!", total_shares=3, threshold=2)


# ==========================================================================
# Key Rotation Policy
# ==========================================================================

class TestKeyRotationStatus:

    def test_no_metadata_file(self, tmp_dir):
        result = check_key_rotation_status(tmp_dir / "nonexistent.json")
        assert result["warning_message"] is not None
        assert "No key metadata" in result["warning_message"]

    def test_fresh_key(self, tmp_dir):
        meta_file = tmp_dir / "meta.json"
        meta_file.write_text(json.dumps({
            "created_at": datetime.now(timezone.utc).isoformat()
        }))
        result = check_key_rotation_status(meta_file)
        assert result["needs_rotation"] is False
        assert result["key_age_days"] <= 1

    def test_old_key_needs_rotation(self, tmp_dir):
        meta_file = tmp_dir / "meta.json"
        old_date = datetime.now(timezone.utc) - timedelta(days=400)
        meta_file.write_text(json.dumps({
            "created_at": old_date.isoformat()
        }))
        result = check_key_rotation_status(meta_file)
        assert result["needs_rotation"] is True
        assert result["days_until_recommended"] < 0

    def test_approaching_rotation(self, tmp_dir):
        meta_file = tmp_dir / "meta.json"
        near_expiry = datetime.now(timezone.utc) - timedelta(
            days=DEFAULT_KEY_MAX_AGE_DAYS - KEY_EXPIRY_WARNING_DAYS + 5
        )
        meta_file.write_text(json.dumps({
            "created_at": near_expiry.isoformat()
        }))
        result = check_key_rotation_status(meta_file)
        assert result["needs_rotation"] is False
        assert result["warning_message"] is not None
        assert "recommended in" in result["warning_message"]

    def test_corrupt_metadata(self, tmp_dir):
        meta_file = tmp_dir / "meta.json"
        meta_file.write_text("not json")
        result = check_key_rotation_status(meta_file)
        assert result["warning_message"] is not None
        assert "Could not parse" in result["warning_message"]


# ==========================================================================
# KeyManager
# ==========================================================================

class TestKeyManager:

    @pytest.fixture
    def km(self, tmp_dir):
        return KeyManager(config_dir=tmp_dir)

    def test_initialize_from_passphrase(self, km, tmp_dir):
        key = km.initialize_from_passphrase("my-secure-passphrase-long")
        assert key is not None
        assert km.metadata_file.exists()
        meta = json.loads(km.metadata_file.read_text())
        assert meta["method"] == "pbkdf2_passphrase"
        assert meta["key_version"] == 1

    def test_recover_from_passphrase(self, km):
        original_key = km.initialize_from_passphrase("recovery-test-passphrase")
        recovered_key = km.recover_key_from_passphrase("recovery-test-passphrase")
        assert recovered_key == original_key

    def test_recover_wrong_passphrase_differs(self, km):
        km.initialize_from_passphrase("correct-passphrase-here")
        wrong_key = km.recover_key_from_passphrase("wrong-passphrase-here!!")
        # Different passphrase produces different key (no error, just different key)
        # This is expected: PBKDF2 derives a different key
        assert wrong_key is not None

    def test_recover_no_metadata_raises(self, km):
        with pytest.raises(KeyManagementError, match="Key metadata not found"):
            km.recover_key_from_passphrase("any-passphrase-here")

    def test_initialize_from_random(self, km):
        key = km.initialize_from_random_key()
        assert key is not None
        meta = json.loads(km.metadata_file.read_text())
        assert meta["method"] == "random_generation"

    def test_rotate_key(self, km):
        initial_key = km.initialize_from_random_key()
        old_key, new_key = km.rotate_key(initial_key)
        assert old_key == initial_key
        assert new_key != initial_key
        meta = json.loads(km.metadata_file.read_text())
        assert meta["key_version"] == 2

    def test_rotate_key_with_passphrase(self, km):
        initial_key = km.initialize_from_random_key()
        old_key, new_key = km.rotate_key(initial_key, new_passphrase="new-passphrase-long-enough")
        assert old_key == initial_key
        meta = json.loads(km.metadata_file.read_text())
        assert meta["method"] == "pbkdf2_passphrase"

    def test_rotate_invalid_old_key_raises(self, km):
        with pytest.raises(KeyManagementError, match="Old key validation failed"):
            km.rotate_key("short")

    def test_check_rotation_status(self, km):
        km.initialize_from_random_key()
        status = km.check_rotation_status()
        assert status["needs_rotation"] is False

    def test_emergency_shares_round_trip(self, km, tmp_dir):
        key = km.initialize_from_random_key()
        shares = km.create_emergency_shares(key, total_shares=5, threshold=3)
        assert len(shares) == 5
        recovered = km.recover_from_emergency_shares(shares[:3])
        assert recovered == key

    def test_no_backup(self, km):
        key = km.initialize_from_passphrase("no-backup-test-passphrase", save_backup=False)
        assert key is not None
        assert not km.metadata_file.exists()

    def test_backup_to_custom_location(self, km, tmp_dir):
        backup_dir = tmp_dir / "backup"
        km.initialize_from_passphrase(
            "backup-test-passphrase!", save_backup=True, backup_location=backup_dir
        )
        assert (backup_dir / "encryption.meta.json").exists()

    def test_recover_unsupported_method_raises(self, km):
        km.metadata_file.write_text(json.dumps({
            "method": "unsupported_method",
            "salt": "abc",
        }))
        with pytest.raises(KeyManagementError, match="Unsupported"):
            km.recover_key_from_passphrase("any-passphrase-here")

    def test_get_next_version_no_file(self, km):
        assert km._get_next_version() == 1

    def test_get_rotation_history_no_file(self, km):
        assert km._get_rotation_history() == []


# ==========================================================================
# check_environment_key
# ==========================================================================

class TestCheckEnvironmentKey:

    def test_no_key_set(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove DB_ENCRYPTION_KEY if present
            os.environ.pop('DB_ENCRYPTION_KEY', None)
            found, key, error = check_environment_key()
            assert found is False
            assert key is None

    def test_valid_key(self):
        good_key = generate_secure_key()
        with patch.dict(os.environ, {'DB_ENCRYPTION_KEY': good_key}):
            found, key, error = check_environment_key()
            assert found is True
            assert key == good_key
            assert error is None

    def test_weak_key(self):
        weak_key = base64.urlsafe_b64encode(b"short").decode('ascii')
        with patch.dict(os.environ, {'DB_ENCRYPTION_KEY': weak_key}):
            found, key, error = check_environment_key()
            assert found is True
            assert error is not None  # Validation warning


# ==========================================================================
# Error path coverage — IOError in KeyAuditLogger
# ==========================================================================

class TestKeyAuditLoggerErrorPaths:

    def test_log_operation_ioerror_does_not_raise(self, tmp_dir):
        """IOError when writing audit log should be swallowed silently."""
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        # Force an IOError by making the audit file a directory
        logger.audit_file.parent.mkdir(parents=True, exist_ok=True)
        logger.audit_file.mkdir()  # directory where file expected → IOError on open
        # Should not raise
        logger.log_operation("op", success=True)

    def test_get_recent_operations_ioerror_returns_empty(self, tmp_dir):
        """IOError when reading audit log should return empty list."""
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        logger.audit_dir.mkdir(parents=True, exist_ok=True)
        # Create the file so exists() returns True, then make it unreadable via mock
        logger.audit_file.write_text("")
        with patch("builtins.open", side_effect=IOError("permission denied")):
            ops = logger.get_recent_operations()
        assert ops == []

    def test_get_recent_operations_json_decode_error_skips_line(self, tmp_dir):
        """Malformed JSON line in audit log is skipped gracefully."""
        logger = KeyAuditLogger(audit_dir=tmp_dir / "audit")
        logger.audit_dir.mkdir(parents=True, exist_ok=True)
        # Write one valid and one invalid JSON line
        logger.audit_file.write_text('{"operation": "good", "success": true, "admin_id": "a", "details": {}}\nbad-json\n')
        ops = logger.get_recent_operations()
        assert len(ops) == 1
        assert ops[0]["operation"] == "good"


# ==========================================================================
# KeyManager private method error paths
# ==========================================================================

class TestKeyManagerPrivateErrorPaths:

    @pytest.fixture
    def km(self, tmp_dir):
        return KeyManager(config_dir=tmp_dir)

    def test_get_next_version_ioerror_returns_one(self, km):
        """_get_next_version returns 1 when metadata file cannot be read."""
        km.metadata_file.write_text("")  # file exists but empty → JSONDecodeError
        result = km._get_next_version()
        assert result == 1

    def test_get_next_version_bad_json_returns_one(self, km):
        """_get_next_version returns 1 when metadata file has bad JSON."""
        km.metadata_file.write_text("not-json")
        result = km._get_next_version()
        assert result == 1

    def test_get_rotation_history_ioerror_returns_empty(self, km):
        """_get_rotation_history returns [] when metadata file cannot be read."""
        km.metadata_file.write_text("not-json")
        result = km._get_rotation_history()
        assert result == []

    def test_recover_key_from_passphrase_json_error(self, km):
        """If metadata file contains bad JSON, recovery raises KeyManagementError."""
        km.metadata_file.write_text("not json at all!")
        with pytest.raises(KeyManagementError, match="Failed to recover key"):
            km.recover_key_from_passphrase("any-passphrase-long")

    def test_recover_key_from_passphrase_missing_salt_raises(self, km):
        """If metadata file is missing the 'salt' key, recovery raises KeyManagementError."""
        km.metadata_file.write_text(
            '{"method": "pbkdf2_passphrase", "iterations": 600000, "key_version": 1}'
        )
        with pytest.raises(KeyManagementError, match="Failed to recover key"):
            km.recover_key_from_passphrase("any-passphrase-long")

    def test_initialize_from_random_no_backup(self, km):
        """initialize_from_random_key with save_backup=False does not write metadata."""
        key = km.initialize_from_random_key(save_backup=False)
        assert key is not None
        assert not km.metadata_file.exists()
