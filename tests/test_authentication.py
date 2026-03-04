"""
Test Suite for Authentication System
Tests Argon2 password hashing, account lockout, subscription verification, and session management
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import shutil

from core.authentication import (
    AuthenticationManager,
    AuthenticationError,
    InvalidCredentialsError,
    AccountLockedError,
    SubscriptionError,
    hash_session_token,
)
from storage.database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    shutil.rmtree(temp_dir)


@pytest.fixture
def auth_manager(temp_db):
    """Create authentication manager with test database"""
    usb_path = Path(tempfile.mkdtemp())
    auth = AuthenticationManager(temp_db, usb_path)
    yield auth
    shutil.rmtree(usb_path)


class TestAccountCreation:
    """Test parent account creation functionality"""
    
    def test_create_account_success(self, auth_manager):
        """Test successful account creation"""
        success, parent_id = auth_manager.create_parent_account(
            username="testparent",
            password="SecurePass123!",
            email="test@example.com"
        )
        
        assert success is True
        assert parent_id is not None
        assert len(parent_id) == 32  # UUID hex format
    
    def test_create_account_duplicate_username(self, auth_manager):
        """Test account creation fails with duplicate username"""
        # Create first account
        auth_manager.create_parent_account("testparent", "SecurePass123!")
        
        # Try to create duplicate
        success, error = auth_manager.create_parent_account("testparent", "OtherPass123!")
        
        assert success is False
        assert "already exists" in error.lower()
    
    def test_create_account_short_username(self, auth_manager):
        """Test account creation fails with short username"""
        success, error = auth_manager.create_parent_account("ab", "SecurePass123!")
        
        assert success is False
        assert "at least 3 characters" in error.lower()
    
    def test_create_account_short_password(self, auth_manager):
        """Test account creation fails with short password"""
        success, error = auth_manager.create_parent_account("testparent", "short")
        
        assert success is False
        assert "at least 8 characters" in error.lower()
    
    def test_password_hashing_uses_argon2(self, auth_manager):
        """Test that Argon2 is used for password hashing"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent", "SecurePass123!"
        )
        
        # Query database for password hash
        result = auth_manager.db.execute_query(
            "SELECT password_hash FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        password_hash = result[0]['password_hash']
        
        # Argon2 hashes start with $argon2
        assert password_hash.startswith('$argon2')
    
    def test_create_account_with_email(self, auth_manager):
        """Test account creation with optional email"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent",
            "SecurePass123!",
            email="test@example.com"
        )
        
        assert success is True

        # Verify plaintext email is NOT stored (COPPA compliance)
        result = auth_manager.db.execute_query(
            "SELECT email, encrypted_email FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )

        assert result[0]['email'] is None
        assert result[0]['encrypted_email'] is not None


class TestAuthentication:
    """Test login and authentication functionality"""
    
    @pytest.fixture
    def test_account(self, auth_manager):
        """Create test account for authentication tests"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent", "SecurePass123!"
        )
        return parent_id
    
    def test_login_success(self, auth_manager, test_account):
        """Test successful login"""
        success, session_data = auth_manager.authenticate_parent(
            "testparent", "SecurePass123!"
        )
        
        assert success is True
        assert session_data is not None
        assert session_data['parent_id'] == test_account
        assert 'session_token' in session_data
    
    def test_login_wrong_password(self, auth_manager, test_account):
        """Test login fails with wrong password"""
        success, error = auth_manager.authenticate_parent(
            "testparent", "WrongPassword"
        )
        
        assert success is False
        assert isinstance(error, str)
    
    def test_login_nonexistent_user(self, auth_manager):
        """Test login fails with non-existent username"""
        success, error = auth_manager.authenticate_parent(
            "nonexistent", "AnyPassword"
        )
        
        assert success is False
        assert error is not None
    
    def test_session_token_generated(self, auth_manager, test_account):
        """Test that session token is generated on login"""
        success, session_data = auth_manager.authenticate_parent(
            "testparent", "SecurePass123!"
        )
        
        token = session_data['session_token']
        
        assert len(token) == 64  # 32 bytes = 64 hex chars
        assert all(c in '0123456789abcdef' for c in token)
    
    def test_session_token_unique_per_login(self, auth_manager, test_account):
        """Test each login generates unique session token"""
        _, session1 = auth_manager.authenticate_parent("testparent", "SecurePass123!")
        _, session2 = auth_manager.authenticate_parent("testparent", "SecurePass123!")
        
        assert session1['session_token'] != session2['session_token']


class TestAccountLockout:
    """Test account lockout protection"""
    
    @pytest.fixture
    def test_account(self, auth_manager):
        """Create test account"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent", "SecurePass123!"
        )
        return parent_id
    
    def test_failed_login_increments_counter(self, auth_manager, test_account):
        """Test failed login attempts are tracked"""
        # First failed attempt
        auth_manager.authenticate_parent("testparent", "wrong1")
        
        result = auth_manager.db.execute_query(
            "SELECT failed_login_attempts FROM accounts WHERE parent_id = ?",
            (test_account,)
        )
        
        assert result[0]['failed_login_attempts'] == 1
    
    def test_account_locks_after_5_failures(self, auth_manager, test_account):
        """Test account locks after 5 failed attempts"""
        # Attempt 5 failed logins
        for i in range(5):
            auth_manager.authenticate_parent("testparent", f"wrong{i}")
        
        # Check account is locked
        result = auth_manager.db.execute_query(
            "SELECT account_locked_until FROM accounts WHERE parent_id = ?",
            (test_account,)
        )
        
        locked_until = result[0]['account_locked_until']
        assert locked_until is not None
        
        # Verify locked_until is in the future (30 minutes)
        locked_time = datetime.fromisoformat(locked_until)
        if locked_time.tzinfo is None:
            locked_time = locked_time.replace(tzinfo=timezone.utc)
        assert locked_time > datetime.now(timezone.utc)
    
    def test_locked_account_rejects_login(self, auth_manager, test_account):
        """Test locked account rejects correct password with generic error (prevents user enumeration)"""
        # Lock the account
        for i in range(5):
            auth_manager.authenticate_parent("testparent", f"wrong{i}")

        # Try with correct password — should still fail (locked)
        success, error = auth_manager.authenticate_parent("testparent", "SecurePass123!")

        assert success is False
        # Generic error message to prevent user enumeration (no "locked" disclosure)
        assert "invalid" in str(error).lower()
    
    def test_successful_login_resets_counter(self, auth_manager, test_account):
        """Test successful login resets failed attempt counter"""
        # Failed attempts
        auth_manager.authenticate_parent("testparent", "wrong1")
        auth_manager.authenticate_parent("testparent", "wrong2")
        
        # Successful login
        auth_manager.authenticate_parent("testparent", "SecurePass123!")
        
        # Check counter reset
        result = auth_manager.db.execute_query(
            "SELECT failed_login_attempts FROM accounts WHERE parent_id = ?",
            (test_account,)
        )
        
        assert result[0]['failed_login_attempts'] == 0


class TestSessionManagement:
    """Test session token management"""
    
    @pytest.fixture
    def authenticated_session(self, auth_manager):
        """Create and authenticate test account"""
        auth_manager.create_parent_account("testparent", "SecurePass123!")
        _, session_data = auth_manager.authenticate_parent("testparent", "SecurePass123!")
        return session_data
    
    def test_validate_session_token(self, auth_manager, authenticated_session):
        """Test valid session token validation"""
        is_valid, parent_id = auth_manager.validate_session_token(
            authenticated_session['session_token']
        )
        
        assert is_valid is True
        assert parent_id == authenticated_session['parent_id']
    
    def test_validate_invalid_token(self, auth_manager):
        """Test invalid token rejection"""
        is_valid, _ = auth_manager.validate_session_token("invalid_token_12345")
        
        assert is_valid is False
    
    def test_logout_invalidates_token(self, auth_manager, authenticated_session):
        """Test logout invalidates session token"""
        # Logout
        auth_manager.logout(authenticated_session['session_token'])

        # Try to validate token
        is_valid, _ = auth_manager.validate_session_token(
            authenticated_session['session_token']
        )

        assert is_valid is False
    
    def test_session_token_expiry(self, auth_manager, authenticated_session):
        """Test session tokens expire after configured time"""
        token = authenticated_session['session_token']

        # Manually expire the token in database (match by hashed token)
        auth_manager.db.execute_update(
            """UPDATE auth_tokens
               SET expires_at = ?
               WHERE session_token = ?""",
            (datetime.now().isoformat(), hash_session_token(token))
        )
        
        # Validate should fail
        is_valid, _ = auth_manager.validate_session_token(token)
        
        assert is_valid is False


class TestPasswordChange:
    """Test password change functionality"""
    
    @pytest.fixture
    def test_account(self, auth_manager):
        """Create test account"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent", "OldPassword123!"
        )
        return parent_id
    
    def test_change_password_success(self, auth_manager, test_account):
        """Test successful password change"""
        success, error = auth_manager.change_password(
            test_account,
            "OldPassword123!",
            "NewPassword456!"
        )
        
        assert success is True
        assert error is None
        
        # Verify new password works
        login_success, _ = auth_manager.authenticate_parent(
            "testparent", "NewPassword456!"
        )
        assert login_success is True
    
    def test_change_password_wrong_old_password(self, auth_manager, test_account):
        """Test password change fails with wrong old password"""
        success, error = auth_manager.change_password(
            test_account,
            "WrongOldPassword",
            "NewPassword456!"
        )
        
        assert success is False
        assert error is not None
    
    def test_old_password_no_longer_works(self, auth_manager, test_account):
        """Test old password stops working after change"""
        # Change password
        auth_manager.change_password(
            test_account,
            "OldPassword123!",
            "NewPassword456!"
        )
        
        # Old password should fail
        success, _ = auth_manager.authenticate_parent("testparent", "OldPassword123!")
        
        assert success is False


class TestDeviceAuthentication:
    """Test device-specific authentication"""
    
    def test_device_id_generated(self, auth_manager):
        """Test device ID is generated for new accounts"""
        success, parent_id = auth_manager.create_parent_account(
            "testparent", "SecurePass123!"
        )
        
        result = auth_manager.db.execute_query(
            "SELECT device_id FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        device_id = result[0]['device_id']
        
        assert device_id is not None
        assert len(device_id) > 16  # Should be substantial length
    
    def test_device_id_unique_per_account(self, auth_manager):
        """Test each account gets unique device ID"""
        success1, parent1 = auth_manager.create_parent_account("parent1", "SecurePass123!")
        assert success1, f"Failed to create parent1: {parent1}"
        success2, parent2 = auth_manager.create_parent_account("parent2", "SecurePass123!")
        assert success2, f"Failed to create parent2: {parent2}"
        
        result = auth_manager.db.execute_query(
            "SELECT device_id FROM accounts WHERE parent_id IN (?, ?)",
            (parent1, parent2)
        )
        
        device_ids = [row['device_id'] for row in result]
        
        assert len(set(device_ids)) == 2  # Both unique


# Run tests with: pytest tests/test_authentication.py -v
