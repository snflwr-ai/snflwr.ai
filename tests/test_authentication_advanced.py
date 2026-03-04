"""
Advanced authentication tests covering session management, password changes,
email verification, password reset, and session caching.
These complement test_authentication.py which covers basic create/login flow.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile
import shutil

import pytest

from core.authentication import (
    AuthenticationManager,
    AuthSession,
    hash_session_token,
)
from storage.database import DatabaseManager


@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_auth.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    shutil.rmtree(temp_dir)


@pytest.fixture
def auth(temp_db):
    mgr = AuthenticationManager(temp_db)
    return mgr


def _create_account(auth, username="testuser", password="SecurePass123!", email=None):
    return auth.create_parent_account(username, password, email=email)


# ---------------------------------------------------------------------------
# hash_session_token
# ---------------------------------------------------------------------------


class TestHashSessionToken:
    def test_produces_sha256(self):
        token = "abc123"
        expected = hashlib.sha256(token.encode()).hexdigest()
        assert hash_session_token(token) == expected

    def test_deterministic(self):
        assert hash_session_token("test") == hash_session_token("test")

    def test_different_tokens_different_hashes(self):
        assert hash_session_token("a") != hash_session_token("b")


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------


class TestPasswordValidation:
    def test_too_short(self, auth):
        valid, err = auth._validate_password_strength("Abc1!")
        assert not valid
        assert "8 characters" in err

    def test_no_uppercase(self, auth):
        valid, err = auth._validate_password_strength("lowercase1!")
        assert not valid
        assert "uppercase" in err

    def test_no_lowercase(self, auth):
        valid, err = auth._validate_password_strength("UPPERCASE1!")
        assert not valid
        assert "lowercase" in err

    def test_no_digit(self, auth):
        valid, err = auth._validate_password_strength("NoDigits!!")
        assert not valid
        assert "number" in err

    def test_no_special(self, auth):
        valid, err = auth._validate_password_strength("NoSpecial1a")
        assert not valid
        assert "special" in err

    def test_valid_password(self, auth):
        valid, err = auth._validate_password_strength("StrongP@ss1")
        assert valid
        assert err is None


# ---------------------------------------------------------------------------
# Session caching (fallback in-memory — no Redis needed)
# ---------------------------------------------------------------------------


class TestSessionCaching:
    def test_set_and_get_fallback(self, auth):
        auth._redis = None  # force fallback
        auth._set_session_in_cache("tok123", {"parent_id": "p1"})
        result = auth._get_session_from_cache("tok123")
        assert result["parent_id"] == "p1"

    def test_delete_from_fallback(self, auth):
        auth._redis = None
        auth._set_session_in_cache("tok123", {"parent_id": "p1"})
        auth._delete_session_from_cache("tok123")
        assert auth._get_session_from_cache("tok123") is None

    def test_delete_user_sessions_from_fallback(self, auth):
        auth._redis = None
        auth._set_session_in_cache("tok1", {"parent_id": "user1"})
        auth._set_session_in_cache("tok2", {"parent_id": "user1"})
        auth._set_session_in_cache("tok3", {"parent_id": "user2"})
        auth._delete_user_sessions_from_cache("user1")
        assert auth._get_session_from_cache("tok1") is None
        assert auth._get_session_from_cache("tok2") is None
        assert auth._get_session_from_cache("tok3") is not None

    def test_expired_session_removed_from_cache(self, auth):
        auth._redis = None
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        auth._set_session_in_cache("tok_exp", {"parent_id": "p1", "expires_at": expired})
        result = auth._get_session_from_cache("tok_exp")
        assert result is None

    def test_valid_session_not_expired(self, auth):
        auth._redis = None
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        auth._set_session_in_cache("tok_valid", {"parent_id": "p1", "expires_at": future})
        result = auth._get_session_from_cache("tok_valid")
        assert result is not None


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_success(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        assert success
        token = session["session_token"]

        result = auth.logout(token)
        assert result is True

        # Session should be invalid now
        valid, _ = auth.validate_session_token(token)
        assert not valid

    def test_logout_db_error(self, auth, temp_db):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        token = session["session_token"]

        with patch.object(temp_db, "execute_write", side_effect=sqlite3.OperationalError("db fail")):
            # Should not raise, returns False
            result = auth.logout(token)
            assert result is False


# ---------------------------------------------------------------------------
# validate_session_token
# ---------------------------------------------------------------------------


class TestValidateSessionToken:
    def test_valid_token(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        token = session["session_token"]

        valid, parent_id = auth.validate_session_token(token)
        assert valid
        assert parent_id is not None

    def test_invalid_token(self, auth):
        valid, parent_id = auth.validate_session_token("nonexistent_token")
        assert not valid
        assert parent_id is None


# ---------------------------------------------------------------------------
# validate_session (returns AuthSession)
# ---------------------------------------------------------------------------


class TestValidateSession:
    def test_returns_auth_session(self, auth):
        _create_account(auth, email="test@example.com")
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        token = session["session_token"]

        valid, auth_session = auth.validate_session(token)
        assert valid
        assert isinstance(auth_session, AuthSession)
        assert auth_session.role == "parent"

    def test_invalid_session(self, auth):
        valid, auth_session = auth.validate_session("bad_token")
        assert not valid
        assert auth_session is None


# ---------------------------------------------------------------------------
# Account lockout after 5 failed attempts
# ---------------------------------------------------------------------------


class TestAccountLockout:
    def test_locks_after_5_failures(self, auth):
        _create_account(auth)
        for _ in range(5):
            success, msg = auth.authenticate_parent("testuser", "WrongPassword1!")
            assert not success

        # 6th attempt should fail (account locked, but generic error to prevent enumeration)
        success, msg = auth.authenticate_parent("testuser", "SecurePass123!")
        assert not success
        assert "invalid" in msg.lower()

    def test_resets_on_success(self, auth):
        _create_account(auth)
        # 4 failures (not enough to lock)
        for _ in range(4):
            auth.authenticate_parent("testuser", "WrongPassword1!")
        # Correct password should succeed
        success, _ = auth.authenticate_parent("testuser", "SecurePass123!")
        assert success


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------


class TestChangePassword:
    def test_successful_change(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        ok, err = auth.change_password(parent_id, "SecurePass123!", "NewSecure@456")
        assert ok
        assert err is None

        # Old password should fail
        success, _ = auth.authenticate_parent("testuser", "SecurePass123!")
        assert not success

        # New password should work
        success, _ = auth.authenticate_parent("testuser", "NewSecure@456")
        assert success

    def test_wrong_current_password(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        ok, err = auth.change_password(parent_id, "WrongCurrent1!", "NewSecure@456")
        assert not ok
        assert "incorrect" in err.lower()

    def test_weak_new_password(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        ok, err = auth.change_password(parent_id, "SecurePass123!", "weak")
        assert not ok

    def test_invalidates_all_sessions(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]
        old_token = session["session_token"]

        auth.change_password(parent_id, "SecurePass123!", "NewSecure@456")

        # Old session should be invalid
        valid, _ = auth.validate_session_token(old_token)
        assert not valid

    def test_nonexistent_user(self, auth):
        ok, err = auth.change_password("nonexistent", "OldSecure@Pass1", "NewSecure@Pass2")
        assert not ok
        assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# update_parent_email
# ---------------------------------------------------------------------------


class TestUpdateParentEmail:
    def test_invalid_email_format(self, auth):
        result = auth.update_parent_email("parent123", "not-an-email")
        assert result is False

    def test_successful_update(self, auth):
        _create_account(auth, email="old@example.com")
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        result = auth.update_parent_email(parent_id, "new@example.com")
        assert result is True


# ---------------------------------------------------------------------------
# Email verification tokens
# ---------------------------------------------------------------------------


class TestEmailVerification:
    def test_generate_token(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        ok, token, err = auth.generate_verification_token(parent_id)
        assert ok
        assert token is not None
        assert err is None

    def test_verify_valid_token(self, auth):
        _create_account(auth)
        success, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        ok, token, _ = auth.generate_verification_token(parent_id)
        assert ok

        verified, uid, err = auth.verify_email_token(token)
        assert verified
        assert uid == parent_id

    def test_verify_invalid_token(self, auth):
        verified, uid, err = auth.verify_email_token("bogus_token")
        assert not verified
        assert "Invalid" in err

    def test_token_cannot_be_reused(self, auth):
        _create_account(auth)
        _, session = auth.authenticate_parent("testuser", "SecurePass123!")
        parent_id = session["parent_id"]

        _, token, _ = auth.generate_verification_token(parent_id)
        auth.verify_email_token(token)

        # Second use should fail
        verified, _, err = auth.verify_email_token(token)
        assert not verified


# ---------------------------------------------------------------------------
# Password reset flow
# ---------------------------------------------------------------------------


class TestPasswordReset:
    def test_generate_reset_token_existing_email(self, auth):
        _create_account(auth, email="user@example.com")
        ok, token, err = auth.generate_password_reset_token("user@example.com")
        assert ok
        # Token should be non-None for existing email
        assert token is not None

    def test_generate_reset_token_nonexistent_email(self, auth):
        ok, token, err = auth.generate_password_reset_token("nobody@example.com")
        # Should return success (don't reveal if email exists)
        assert ok
        assert token is None  # But no actual token generated

    def test_reset_password_with_valid_token(self, auth):
        _create_account(auth, email="user@example.com")
        ok, token, _ = auth.generate_password_reset_token("user@example.com")
        assert ok and token

        ok, err = auth.reset_password_with_token(token, "BrandNew@Pass1")
        assert ok
        assert err is None

        # Can log in with new password
        success, _ = auth.authenticate_parent("testuser", "BrandNew@Pass1")
        assert success

    def test_reset_with_invalid_token(self, auth):
        ok, err = auth.reset_password_with_token("bad_token", "NewPass@1")
        assert not ok
        assert "Invalid" in err

    def test_reset_invalidates_all_sessions(self, auth):
        _create_account(auth, email="user@example.com")
        _, session = auth.authenticate_parent("testuser", "SecurePass123!")
        old_token = session["session_token"]

        _, reset_token, _ = auth.generate_password_reset_token("user@example.com")
        auth.reset_password_with_token(reset_token, "BrandNew@Pass1")

        valid, _ = auth.validate_session_token(old_token)
        assert not valid

    def test_reset_with_weak_password(self, auth):
        _create_account(auth, email="user@example.com")
        _, token, _ = auth.generate_password_reset_token("user@example.com")
        ok, err = auth.reset_password_with_token(token, "weak")
        assert not ok


# ---------------------------------------------------------------------------
# cleanup_expired_sessions
# ---------------------------------------------------------------------------


class TestCleanupExpiredSessions:
    def test_cleanup(self, auth):
        _create_account(auth)
        auth.authenticate_parent("testuser", "SecurePass123!")
        # Cleanup should not raise
        count = auth.cleanup_expired_sessions()
        assert isinstance(count, int)
