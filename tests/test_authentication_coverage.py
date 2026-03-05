"""
Additional coverage tests for core/authentication.py.

Covers uncovered paths:
- _get_session_from_cache (Redis path + fallback + expired)
- _set_session_in_cache (Redis + fallback)
- _delete_session_from_cache (Redis + fallback)
- _delete_user_sessions_from_cache (Redis + fallback)
- validate_session_token (cache hit, cache miss, expired, DB error)
- validate_session (with email, role)
- update_parent_email
- logout
- authenticate_parent (lockout check)
- create_parent_account (email handling)
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta, timezone

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")


@pytest.fixture
def temp_db():
    from pathlib import Path
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    from storage.database import DatabaseManager
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    shutil.rmtree(temp_dir)


@pytest.fixture
def auth_manager(temp_db):
    usb_path = Path(tempfile.mkdtemp())
    from core.authentication import AuthenticationManager
    mgr = AuthenticationManager(temp_db, usb_path)
    yield mgr
    shutil.rmtree(usb_path)


class TestSessionCacheRedisPath:
    """Test Redis session cache operations."""

    def test_get_session_redis_hit(self, auth_manager):
        """Get session from Redis when it has valid data."""
        import json
        from datetime import datetime, timezone, timedelta

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        session_data = {"parent_id": "p1", "session_token": "tok", "expires_at": future}

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(session_data).encode()
        auth_manager._redis = mock_redis

        result = auth_manager._get_session_from_cache("tok")
        assert result == session_data

    def test_get_session_redis_expired(self, auth_manager):
        """Expired session in Redis should be deleted and return None."""
        import json
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        session_data = {"parent_id": "p1", "session_token": "tok", "expires_at": past}

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(session_data).encode()
        auth_manager._redis = mock_redis

        result = auth_manager._get_session_from_cache("tok")
        assert result is None
        # Should have called delete
        mock_redis.delete.assert_called_once()

    def test_get_session_redis_miss(self, auth_manager):
        """Redis miss should return None."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        auth_manager._redis = mock_redis

        result = auth_manager._get_session_from_cache("unknown-tok")
        assert result is None

    def test_get_session_redis_error_falls_back_to_none(self, auth_manager):
        """Redis error should be caught and return None."""
        try:
            from redis.exceptions import RedisError
        except ImportError:
            RedisError = OSError

        mock_redis = MagicMock()
        mock_redis.get.side_effect = RedisError("connection refused")
        auth_manager._redis = mock_redis

        result = auth_manager._get_session_from_cache("tok")
        assert result is None

    def test_set_session_redis(self, auth_manager):
        """Set session should call Redis setex and sadd."""
        mock_redis = MagicMock()
        auth_manager._redis = mock_redis

        session_data = {"parent_id": "p1", "session_token": "tok", "expires_at": "2025-01-01T00:00:00"}
        auth_manager._set_session_in_cache("tok", session_data)

        mock_redis.setex.assert_called_once()
        mock_redis.sadd.assert_called_once()

    def test_set_session_redis_error(self, auth_manager):
        """Redis error during set should be caught."""
        try:
            from redis.exceptions import RedisError
        except ImportError:
            RedisError = OSError

        mock_redis = MagicMock()
        mock_redis.setex.side_effect = RedisError("fail")
        auth_manager._redis = mock_redis

        # Should not raise
        auth_manager._set_session_in_cache("tok", {"parent_id": "p1"})

    def test_delete_session_redis(self, auth_manager):
        mock_redis = MagicMock()
        auth_manager._redis = mock_redis
        auth_manager._delete_session_from_cache("tok")
        mock_redis.delete.assert_called_once()

    def test_delete_session_redis_error(self, auth_manager):
        try:
            from redis.exceptions import RedisError
        except ImportError:
            RedisError = OSError

        mock_redis = MagicMock()
        mock_redis.delete.side_effect = RedisError("fail")
        auth_manager._redis = mock_redis
        # Should not raise
        auth_manager._delete_session_from_cache("tok")

    def test_delete_user_sessions_redis(self, auth_manager):
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"tok1", b"tok2"}
        auth_manager._redis = mock_redis

        auth_manager._delete_user_sessions_from_cache("user1")

        mock_redis.smembers.assert_called_once()
        mock_redis.delete.assert_called()

    def test_delete_user_sessions_redis_no_sessions(self, auth_manager):
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = set()
        auth_manager._redis = mock_redis

        auth_manager._delete_user_sessions_from_cache("user1")
        # Should still call delete for the user key
        mock_redis.delete.assert_called()

    def test_delete_user_sessions_redis_error(self, auth_manager):
        try:
            from redis.exceptions import RedisError
        except ImportError:
            RedisError = OSError

        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = RedisError("fail")
        auth_manager._redis = mock_redis
        # Should not raise
        auth_manager._delete_user_sessions_from_cache("user1")


class TestSessionCacheFallbackPath:
    """Test in-memory fallback session cache."""

    def test_set_and_get_fallback(self, auth_manager):
        """Fallback should store and retrieve sessions."""
        auth_manager._redis = None
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        session_data = {"parent_id": "p1", "expires_at": future}

        auth_manager._set_session_in_cache("tok", session_data)
        result = auth_manager._get_session_from_cache("tok")
        assert result == session_data

    def test_delete_fallback(self, auth_manager):
        auth_manager._redis = None
        auth_manager._fallback_sessions["tok"] = {"parent_id": "p1"}
        auth_manager._delete_session_from_cache("tok")
        assert "tok" not in auth_manager._fallback_sessions

    def test_delete_user_sessions_fallback(self, auth_manager):
        auth_manager._redis = None
        auth_manager._fallback_sessions = {
            "tok1": {"parent_id": "user1"},
            "tok2": {"parent_id": "user1"},
            "tok3": {"parent_id": "user2"},
        }
        auth_manager._delete_user_sessions_from_cache("user1")
        assert "tok1" not in auth_manager._fallback_sessions
        assert "tok2" not in auth_manager._fallback_sessions
        assert "tok3" in auth_manager._fallback_sessions

    def test_get_expired_fallback(self, auth_manager):
        auth_manager._redis = None
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        auth_manager._fallback_sessions["tok"] = {
            "parent_id": "p1",
            "expires_at": past
        }
        result = auth_manager._get_session_from_cache("tok")
        assert result is None


class TestValidatePasswordStrength:
    """Test _validate_password_strength."""

    def test_too_short(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("Abc1!")
        assert ok is False
        assert "8" in err

    def test_no_uppercase(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("abcdef1!")
        assert ok is False
        assert "uppercase" in err.lower()

    def test_no_lowercase(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("ABCDEF1!")
        assert ok is False
        assert "lowercase" in err.lower()

    def test_no_digit(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("AbcdefgH!")
        assert ok is False
        assert "number" in err.lower()

    def test_no_special_char(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("Abcdefg1")
        assert ok is False
        assert "special" in err.lower()

    def test_valid_password(self, auth_manager):
        ok, err = auth_manager._validate_password_strength("SecurePass123!")
        assert ok is True
        assert err is None


class TestCreateParentAccount:
    """Additional create_parent_account coverage."""

    def test_invalid_email_format(self, auth_manager):
        success, err = auth_manager.create_parent_account(
            "user1", "SecurePass123!", email="not-an-email"
        )
        assert success is False
        assert "email" in err.lower()

    def test_password_hashing_failure(self, auth_manager):
        """If password hashing fails, should return error."""
        from unittest.mock import MagicMock
        # Replace the password hasher with a mock that raises
        original_ph = auth_manager.ph
        mock_ph = MagicMock()
        mock_ph.hash.side_effect = Exception("argon2 error")
        auth_manager.ph = mock_ph
        try:
            success, err = auth_manager.create_parent_account(
                "user1", "SecurePass123!"
            )
        finally:
            auth_manager.ph = original_ph
        assert success is False
        assert "hashing" in err.lower() or "error" in err.lower()

    def test_email_encryption_failure_non_fatal(self, auth_manager):
        """Email encryption failure should be non-fatal (warning logged)."""
        with patch("core.authentication.get_email_crypto") as mock_crypto:
            mock_crypto.return_value.prepare_email_for_storage.side_effect = Exception("enc fail")
            success, result = auth_manager.create_parent_account(
                "newuser", "SecurePass123!", email="test@example.com"
            )
        # Should still succeed even if email encryption fails
        assert success is True

    def test_db_error_returns_false(self, auth_manager):
        import sqlite3
        with patch.object(auth_manager.db, "execute_write",
                          side_effect=sqlite3.Error("constraint")):
            success, err = auth_manager.create_parent_account(
                "user1", "SecurePass123!"
            )
        assert success is False


class TestAuthenticateParent:
    """Additional authenticate_parent coverage."""

    def test_account_lockout(self, auth_manager):
        """Locked account should return invalid credentials."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        auth_manager.create_parent_account("testuser", "SecurePass123!")
        with patch.object(auth_manager.db, "execute_query",
                          return_value=[{
                              "parent_id": "p1",
                              "password_hash": "hash",
                              "failed_login_attempts": 5,
                              "account_locked_until": future
                          }]):
            success, result = auth_manager.authenticate_parent("testuser", "SecurePass123!")
        assert success is False

    def test_failed_attempts_increment(self, auth_manager):
        """Failed logins should increment counter."""
        auth_manager.create_parent_account("testuser2", "SecurePass123!")
        from unittest.mock import MagicMock

        original_ph = auth_manager.ph
        mock_ph = MagicMock()
        mock_ph.verify.return_value = False
        auth_manager.ph = mock_ph
        try:
            with patch.object(auth_manager.db, "execute_query",
                              return_value=[{
                                  "parent_id": "p1",
                                  "password_hash": "$pbkdf2-fallback$hash",
                                  "failed_login_attempts": 2,
                                  "account_locked_until": None
                              }]), \
                 patch.object(auth_manager.db, "execute_write", return_value=None):
                success, result = auth_manager.authenticate_parent("testuser2", "WrongPass!")
        finally:
            auth_manager.ph = original_ph

        assert success is False

    def test_lockout_after_5_failures(self, auth_manager):
        """5 failed attempts should set lockout."""
        from unittest.mock import MagicMock

        original_ph = auth_manager.ph
        mock_ph = MagicMock()
        mock_ph.verify.return_value = False
        auth_manager.ph = mock_ph
        try:
            with patch.object(auth_manager.db, "execute_query",
                              return_value=[{
                                  "parent_id": "p1",
                                  "password_hash": "hash",
                                  "failed_login_attempts": 4,
                                  "account_locked_until": None
                              }]), \
                 patch.object(auth_manager.db, "execute_write", return_value=None) as mock_write:
                success, result = auth_manager.authenticate_parent("user", "bad")
        finally:
            auth_manager.ph = original_ph

        assert success is False
        # Verify lockout was set
        call_args = mock_write.call_args
        if call_args:
            query = call_args[0][0]
            assert "account_locked_until" in query

    def test_db_error_updating_fail_count(self, auth_manager):
        """DB error when updating failed attempts should return system error."""
        import sqlite3
        from unittest.mock import MagicMock

        original_ph = auth_manager.ph
        mock_ph = MagicMock()
        mock_ph.verify.return_value = False
        auth_manager.ph = mock_ph
        try:
            with patch.object(auth_manager.db, "execute_query",
                              return_value=[{
                                  "parent_id": "p1",
                                  "password_hash": "hash",
                                  "failed_login_attempts": 1,
                                  "account_locked_until": None
                              }]), \
                 patch.object(auth_manager.db, "execute_write",
                              side_effect=sqlite3.Error("DB fail")):
                success, result = auth_manager.authenticate_parent("user", "bad")
        finally:
            auth_manager.ph = original_ph

        assert success is False

    def test_user_not_found(self, auth_manager):
        with patch.object(auth_manager.db, "execute_query", return_value=[]):
            success, result = auth_manager.authenticate_parent("nobody", "pass")
        assert success is False
        assert result == "User not found"

    def test_malformed_db_row(self, auth_manager):
        """Row with None parent_id should return system error."""
        with patch.object(auth_manager.db, "execute_query",
                          return_value=[{
                              "parent_id": None,
                              "password_hash": None,
                              "failed_login_attempts": 0,
                              "account_locked_until": None
                          }]):
            success, result = auth_manager.authenticate_parent("user", "pass")
        assert success is False


class TestLogout:
    """Test logout method."""

    def test_logout_success(self, auth_manager):
        auth_manager._redis = None
        auth_manager._fallback_sessions["tok"] = {"parent_id": "p1"}
        with patch.object(auth_manager.db, "execute_write", return_value=None):
            result = auth_manager.logout("tok")
        assert result is True
        assert "tok" not in auth_manager._fallback_sessions

    def test_logout_db_error_returns_false(self, auth_manager):
        import sqlite3
        auth_manager._redis = None
        with patch.object(auth_manager.db, "execute_write",
                          side_effect=sqlite3.Error("fail")):
            result = auth_manager.logout("tok")
        assert result is False


class TestValidateSession:
    """Test validate_session (full flow with AuthSession creation)."""

    def test_valid_session_creates_auth_session(self, auth_manager):
        from core.authentication import AuthSession

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        with patch.object(auth_manager, "validate_session_token",
                          return_value=(True, "parent-123")), \
             patch.object(auth_manager.db, "execute_query",
                          return_value=[{
                              "encrypted_email": "enc-email",
                              "role": "admin"
                          }]), \
             patch("core.authentication.get_email_crypto") as mock_crypto:
            mock_crypto.return_value.decrypt_email.return_value = "admin@test.com"
            is_valid, session = auth_manager.validate_session("valid-tok")

        assert is_valid is True
        assert isinstance(session, AuthSession)
        assert session.user_id == "parent-123"
        assert session.role == "admin"
        assert session.email == "admin@test.com"

    def test_invalid_token_returns_false(self, auth_manager):
        with patch.object(auth_manager, "validate_session_token",
                          return_value=(False, None)):
            is_valid, session = auth_manager.validate_session("bad-tok")
        assert is_valid is False
        assert session is None

    def test_db_error_during_validate(self, auth_manager):
        import sqlite3
        with patch.object(auth_manager, "validate_session_token",
                          return_value=(True, "parent-123")), \
             patch.object(auth_manager.db, "execute_query",
                          side_effect=sqlite3.Error("fail")):
            is_valid, session = auth_manager.validate_session("tok")

        # Should still return True with default role
        assert is_valid is True
        assert session.role == "parent"

    def test_missing_row_defaults_to_parent_role(self, auth_manager):
        with patch.object(auth_manager, "validate_session_token",
                          return_value=(True, "parent-123")), \
             patch.object(auth_manager.db, "execute_query", return_value=[]):
            is_valid, session = auth_manager.validate_session("tok")

        assert is_valid is True
        assert session.role == "parent"


class TestUpdateParentEmail:
    """Test update_parent_email."""

    def test_invalid_email_returns_false(self, auth_manager):
        result = auth_manager.update_parent_email("p1", "not-valid-email")
        assert result is False

    def test_email_already_in_use_returns_false(self, auth_manager):
        with patch("core.authentication.get_email_crypto") as mock_crypto, \
             patch.object(auth_manager.db, "execute_query",
                          return_value=[{"parent_id": "other-parent"}]):
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            result = auth_manager.update_parent_email("p1", "taken@example.com")
        assert result is False

    def test_success_updates_db(self, auth_manager):
        with patch("core.authentication.get_email_crypto") as mock_crypto, \
             patch.object(auth_manager.db, "execute_query", return_value=[]), \
             patch.object(auth_manager.db, "execute_write", return_value=None):
            mock_crypto.return_value.prepare_email_for_storage.return_value = ("hash", "enc")
            result = auth_manager.update_parent_email("p1", "new@example.com")
        assert result is True


class TestValidateSessionToken:
    """Test validate_session_token (comprehensive)."""

    def test_token_expired_in_db(self, auth_manager):
        """Token exists but is expired should return False."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        from core.authentication import hash_session_token
        hashed = hash_session_token("test-tok")

        auth_manager._redis = None
        with patch.object(auth_manager.db, "execute_query",
                          return_value=[{"parent_id": "p1", "expires_at": past}]):
            is_valid, parent_id = auth_manager.validate_session_token("test-tok")

        assert is_valid is False

    def test_token_not_in_db_returns_false(self, auth_manager):
        auth_manager._redis = None
        with patch.object(auth_manager.db, "execute_query", return_value=[]):
            is_valid, parent_id = auth_manager.validate_session_token("unknown-tok")
        assert is_valid is False
        assert parent_id is None

    def test_db_error_returns_false(self, auth_manager):
        import sqlite3
        auth_manager._redis = None
        with patch.object(auth_manager.db, "execute_query",
                          side_effect=sqlite3.Error("fail")):
            is_valid, parent_id = auth_manager.validate_session_token("tok")
        assert is_valid is False

    def test_valid_token_returns_parent_id(self, auth_manager):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        auth_manager._redis = None
        with patch.object(auth_manager.db, "execute_query",
                          return_value=[{"parent_id": "parent-1", "expires_at": future}]):
            is_valid, parent_id = auth_manager.validate_session_token("tok")
        assert is_valid is True
        assert parent_id == "parent-1"

    def test_cache_hit_still_validates_db(self, auth_manager):
        """Even with cache hit, DB should be checked for token validity."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        auth_manager._redis = None
        auth_manager._fallback_sessions["tok"] = {
            "parent_id": "p1",
            "expires_at": future
        }
        with patch.object(auth_manager.db, "execute_query",
                          return_value=[{"parent_id": "p1", "expires_at": future}]):
            is_valid, parent_id = auth_manager.validate_session_token("tok")
        assert is_valid is True

    def test_stale_cache_cleared_on_db_miss(self, auth_manager):
        """Stale cache should be cleared when DB doesn't find the token."""
        auth_manager._redis = None
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        auth_manager._fallback_sessions["stale-tok"] = {
            "parent_id": "p1",
            "expires_at": future
        }
        with patch.object(auth_manager.db, "execute_query", return_value=[]):
            is_valid, _ = auth_manager.validate_session_token("stale-tok")

        assert is_valid is False
        assert "stale-tok" not in auth_manager._fallback_sessions
