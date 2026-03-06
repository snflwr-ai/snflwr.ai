import uuid
import secrets
import threading
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional, Dict
from dataclasses import dataclass

from storage.db_adapters import DB_ERRORS

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError

try:
    from argon2 import PasswordHasher, exceptions as argon2_exceptions
except ImportError:
    # Secure fallback PasswordHasher using PBKDF2 for environments without argon2
    from storage.encryption import EncryptionManager

    class PasswordHasher:
        def __init__(self):
            self._enc_manager = EncryptionManager()

        def hash(self, password: str) -> str:
            """Hash password using PBKDF2-HMAC-SHA256 with 100k iterations and random salt"""
            pbkdf2_hash = self._enc_manager.hash_password(password)
            return f"$pbkdf2-fallback${pbkdf2_hash}"

        def verify(self, stored: str, password: str) -> bool:
            """Verify password using constant-time comparison"""
            if not stored.startswith("$pbkdf2-fallback$"):
                # Handle legacy SHA256 hashes (should not exist in production)
                return False

            # Extract PBKDF2 hash
            pbkdf2_hash = stored.replace("$pbkdf2-fallback$", "", 1)
            return self._enc_manager.verify_password(password, pbkdf2_hash)


from utils.logger import get_logger, mask_email
from core.email_crypto import get_email_crypto

logger = get_logger(__name__)


def hash_session_token(token: str) -> str:
    """Hash a session token with SHA-256 for secure database storage.

    Raw tokens are returned to the client; only the hash is persisted.
    SHA-256 is appropriate (vs. bcrypt/argon2) because session tokens
    are high-entropy random values, not user-chosen passwords.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class AuthenticationError(Exception):
    pass


class InvalidCredentialsError(AuthenticationError):
    pass


class AccountLockedError(AuthenticationError):
    pass


class SubscriptionError(AuthenticationError):
    pass


@dataclass
class AuthSession:
    """Authenticated session information"""

    user_id: str  # parent_id
    role: str  # 'parent' or 'admin'
    session_token: str
    email: Optional[str] = None
    created_at: Optional[str] = None


class AuthenticationManager:
    """Lightweight Authentication manager used by tests.

    This implementation focuses on the operations exercised by unit tests:
    - `create_parent_account(username, password, email=None, role='parent')` -> (success, parent_id|error)
    - `authenticate_parent(username, password)` -> (success, session_data|error)
    Behavior: uses `storage.database.DatabaseManager` methods `execute_query` and `execute_write`.
    """

    def __init__(self, db_manager, storage_path=None):
        self.db = db_manager
        self.storage_path = storage_path
        self.ph = PasswordHasher()
        self._session_lock = threading.RLock()  # Thread-safe access
        self._redis = None
        self._fallback_sessions = {}  # In-memory fallback if Redis unavailable
        self._session_ttl = 86400  # 24 hours in seconds
        self._initialize_redis()

    def _initialize_redis(self):
        """Initialize Redis for distributed session caching"""
        try:
            from utils.cache import cache

            if cache.enabled and cache._client:
                self._redis = cache._client
                logger.info("[OK] Session cache using Redis (distributed mode)")
            else:
                logger.warning(
                    "[WARN] Session cache using in-memory fallback (single-instance only)"
                )
        except (ImportError, RedisError) as e:
            logger.warning(
                f"[WARN] Session cache Redis init failed, using fallback: {e}"
            )

    def _get_session_from_cache(self, session_token: str) -> Optional[dict]:
        """Get session from Redis or fallback cache, checking expiry"""
        session_data = None
        if self._redis:
            try:
                import json

                redis_key = f"snflwr:session:{session_token}"
                data = self._redis.get(redis_key)
                if data:
                    session_data = json.loads(data)
            except RedisError as e:
                logger.debug(f"Redis session get failed: {e}")
        else:
            with self._session_lock:
                session_data = self._fallback_sessions.get(session_token)

        # Check expiry if present in cached data
        if session_data and "expires_at" in session_data:
            try:
                expires_at = datetime.fromisoformat(session_data["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < datetime.now(timezone.utc):
                    # Expired - remove from cache and return None
                    self._delete_session_from_cache(session_token)
                    return None
            except ValueError as e:
                logger.debug(f"Failed to check cached session expiry: {e}")

        return session_data

    def _set_session_in_cache(self, session_token: str, session_data: dict):
        """Store session in Redis or fallback cache"""
        if self._redis:
            try:
                import json

                redis_key = f"snflwr:session:{session_token}"
                self._redis.setex(
                    redis_key, self._session_ttl, json.dumps(session_data)
                )
                # Maintain reverse index for O(1) bulk invalidation on password change
                user_key = f"snflwr:user_sessions:{session_data['parent_id']}"
                self._redis.sadd(user_key, session_token)
                self._redis.expire(user_key, self._session_ttl)
            except RedisError as e:
                logger.debug(f"Redis session set failed: {e}")
        else:
            with self._session_lock:
                self._fallback_sessions[session_token] = session_data

    def _delete_session_from_cache(self, session_token: str):
        """Remove session from Redis or fallback cache"""
        if self._redis:
            try:
                redis_key = f"snflwr:session:{session_token}"
                self._redis.delete(redis_key)
            except RedisError as e:
                logger.debug(f"Redis session delete failed: {e}")
        else:
            with self._session_lock:
                self._fallback_sessions.pop(session_token, None)

    def _delete_user_sessions_from_cache(self, user_id: str):
        """Remove all sessions for a user from cache.

        Uses reverse index snflwr:user_sessions:{user_id} for O(k) deletion
        where k = number of sessions for this user (typically 1-5).
        """
        if self._redis:
            try:
                user_key = f"snflwr:user_sessions:{user_id}"
                tokens = self._redis.smembers(user_key)
                if tokens:
                    session_keys = [
                        f"snflwr:session:{t.decode() if isinstance(t, bytes) else t}"
                        for t in tokens
                    ]
                    self._redis.delete(*session_keys)
                self._redis.delete(user_key)
            except RedisError as e:
                logger.debug(f"Redis user sessions delete failed: {e}")
        else:
            with self._session_lock:
                to_remove = [
                    sid
                    for sid, data in self._fallback_sessions.items()
                    if data.get("parent_id") == user_id
                ]
                for sid in to_remove:
                    del self._fallback_sessions[sid]

    def _validate_password_strength(self, password: str) -> Tuple[bool, Optional[str]]:
        if not password or len(password) < 8:
            return False, "Password must be at least 8 characters"
        if not any(c.isupper() for c in password):
            return False, "Password must contain at least one uppercase letter"
        if not any(c.islower() for c in password):
            return False, "Password must contain at least one lowercase letter"
        if not any(c.isdigit() for c in password):
            return False, "Password must contain at least one number"
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            return (
                False,
                "Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)",
            )
        return True, None

    def create_parent_account(
        self,
        username: str,
        password: str,
        email: Optional[str] = None,
        role: str = "parent",
    ) -> Tuple[bool, Optional[str]]:
        # Basic validation
        if not username or len(username) < 3:
            return False, "Username must be at least 3 characters"
        if not password or len(password) < 8:
            return False, "Password must be at least 8 characters"

        # Validate email format if provided
        if email:
            import re

            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_pattern, email):
                return False, "Invalid email format"

        # Check duplicate username
        existing = self.db.execute_query(
            "SELECT parent_id FROM accounts WHERE username = ?", (username,)
        )
        if existing:
            return False, "Username already exists"

        # Validate password strength
        valid, err = self._validate_password_strength(password)
        if not valid:
            return False, err

        parent_id = uuid.uuid4().hex
        device_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            password_hash = self.ph.hash(password)
        except (
            Exception
        ) as e:  # Intentional catch-all: argon2 can raise varied internal errors
            logger.error(f"Password hashing failed (argon2 unavailable?): {e}")
            return False, "Password hashing failed - server configuration error"

        # Prepare email_hash and encrypted_email for secure storage/lookup
        email_hash = None
        encrypted_email = None
        if email:
            try:
                email_crypto = get_email_crypto()
                email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
                    email
                )
            except Exception as e:
                logger.warning(f"Email encryption failed (non-fatal): {e}")

        try:
            self.db.execute_write(
                "INSERT INTO accounts (parent_id, username, password_hash, email, "
                "email_hash, encrypted_email, device_id, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    parent_id,
                    username,
                    password_hash,
                    None,
                    email_hash,
                    encrypted_email,
                    device_id,
                    role,
                    created_at,
                ),
            )
        except DB_ERRORS as e:
            logger.error(f"Failed to create parent account: {e}")
            return False, "Database error"

        return True, parent_id

    def authenticate_parent(
        self, username: str, password: str
    ) -> Tuple[bool, Optional[dict]]:
        rows = self.db.execute_query(
            "SELECT parent_id, password_hash, failed_login_attempts, account_locked_until FROM accounts WHERE username = ?",
            (username,),
        )
        if not rows:
            return False, "User not found"

        row = rows[0]

        # row may be sqlite3.Row or dict-like
        def rget(idx, key=None):
            if isinstance(row, dict):
                return row.get(key) if key is not None else None
            try:
                return row[idx]
            except (IndexError, KeyError, TypeError) as e:
                logger.warning(
                    f"Failed to extract field '{key}' (index {idx}) from auth row: {e}"
                )
                return None

        parent_id = rget(0, "parent_id")
        password_hash = rget(1, "password_hash")

        # Validate critical fields are present - fail early with clear error
        if not parent_id:
            logger.error(
                "Authentication failed: parent_id is None - database row malformed"
            )
            return False, "Authentication system error"
        if not password_hash:
            logger.error(
                "Authentication failed: password_hash is None - database row malformed"
            )
            return False, "Authentication system error"

        failed = rget(2, "failed_login_attempts") or 0
        locked_until = rget(3, "account_locked_until")

        # Check lockout
        if locked_until:
            try:
                locked_time = datetime.fromisoformat(locked_until)
                if locked_time.tzinfo is None:
                    locked_time = locked_time.replace(tzinfo=timezone.utc)
                if locked_time > datetime.now(timezone.utc):
                    return False, "Invalid username or password"
            except ValueError as e:
                logger.warning(f"Failed to check account lock status: {e}")

        # Verify password
        try:
            verified = self.ph.verify(password_hash, password)
        except (ValueError, TypeError) as e:
            # Invalid hash format or type - treat as mismatch
            logger.debug(f"Password verification failed due to invalid format: {e}")
            verified = False
        except (
            Exception
        ) as e:  # Intentional catch-all: argon2 can raise varied internal errors
            logger.warning(f"Unexpected error during password verification: {e}")
            verified = False

        if not verified:
            # increment failed counter
            failed = (failed or 0) + 1
            try:
                if failed >= 5:
                    lock_until = (
                        datetime.now(timezone.utc) + timedelta(minutes=30)
                    ).isoformat()
                else:
                    lock_until = None
                self.db.execute_write(
                    "UPDATE accounts SET failed_login_attempts = ?, account_locked_until = ? WHERE parent_id = ?",
                    (failed, lock_until, parent_id),
                )
            except DB_ERRORS as e:
                logger.error(
                    f"CRITICAL: Failed to update failed login attempts for {parent_id}: {e}"
                )
                # Don't return credentials error - return system error to prevent bypass
                return False, "Authentication system error. Please try again later."
            return False, "Invalid username or password"

        # Successful login: reset counters
        try:
            self.db.execute_write(
                "UPDATE accounts SET failed_login_attempts = 0, account_locked_until = NULL, last_login = ? WHERE parent_id = ?",
                (datetime.now(timezone.utc).isoformat(), parent_id),
            )
        except DB_ERRORS as e:
            logger.error(
                f"Failed to reset login counters for {parent_id} after successful login: {e}"
            )

        # Create session token
        session_token = secrets.token_hex(32)

        # Calculate expiry time (24 hours from now)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        session_data = {
            "parent_id": parent_id,
            "session_token": session_token,
            "expires_at": expires_at,  # Include expiry for cache validation
        }

        # Persist hashed token in auth_tokens table (never store raw token)
        hashed_token = hash_session_token(session_token)
        db_persisted = False
        try:
            token_id = uuid.uuid4().hex
            self.db.execute_write(
                """INSERT INTO auth_tokens
                   (token_id, user_id, parent_id, token_type, session_token, created_at, expires_at, is_valid)
                   VALUES (?, ?, ?, 'session', ?, ?, ?, 1)""",
                (
                    token_id,
                    parent_id,
                    parent_id,
                    hashed_token,
                    datetime.now(timezone.utc).isoformat(),
                    expires_at,
                ),
            )
            db_persisted = True
        except DB_ERRORS as e:
            logger.warning(
                f"Failed to persist session token for {parent_id} to database: {e}"
            )

        # Always cache the session (Redis or fallback) to ensure it's valid
        # This is critical when DB persistence fails - session still works until cache expires
        self._set_session_in_cache(session_token, session_data)

        if not db_persisted:
            logger.warning(
                f"Session for {parent_id} is cached only - will not survive server restart"
            )

        return True, session_data

    def logout(self, session_id: str) -> bool:
        """
        End session

        Args:
            session_id: Session to terminate

        Returns:
            True if successful
        """
        try:
            # Remove from cache (Redis or fallback)
            self._delete_session_from_cache(session_id)

            # Deactivate in database (match by hashed token)
            hashed_token = hash_session_token(session_id)
            self.db.execute_write(
                "UPDATE auth_tokens SET is_valid = 0 WHERE session_token = ?",
                (hashed_token,),
            )

            logger.info("Session logged out successfully")
            return True

        except DB_ERRORS as e:
            logger.error(f"Logout failed: {e}")
            return False

    def validate_session_token(self, session_token: str) -> Tuple[bool, Optional[str]]:
        """
        Validate a session token

        Args:
            session_token: Session token to validate

        Returns:
            Tuple of (is_valid: bool, parent_id: Optional[str])
        """
        try:
            # Check cache first for parent_id (performance optimization)
            cached_session = self._get_session_from_cache(session_token)
            cached_parent_id = (
                cached_session.get("parent_id") if cached_session else None
            )

            # Always verify token validity and expiry from database
            # This ensures immediate effect when tokens are invalidated or expired
            hashed_token = hash_session_token(session_token)
            rows = self.db.execute_query(
                "SELECT parent_id, expires_at FROM auth_tokens WHERE session_token = ? AND is_valid = 1",
                (hashed_token,),
            )

            if not rows:
                # Clear stale cache entry if it exists
                if cached_session:
                    self._delete_session_from_cache(session_token)
                return False, None

            row = rows[0]
            parent_id = row[0] if isinstance(row, tuple) else row["parent_id"]
            expires_str = row[1] if isinstance(row, tuple) else row["expires_at"]

            # Check if token is expired
            try:
                expires_at = datetime.fromisoformat(expires_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < datetime.now(timezone.utc):
                    return False, None
            except ValueError as e:
                logger.warning(f"Failed to parse token expiration time: {e}")

            session_data = {
                "parent_id": parent_id,
                "session_token": session_token,
                "expires_at": expires_str,  # Include expiry for cache validation
            }

            # Cache it (Redis or fallback) - only if not already cached
            if not cached_session:
                self._set_session_in_cache(session_token, session_data)

            return True, parent_id

        except DB_ERRORS as e:
            logger.error(f"Session validation failed: {e}")
            return False, None

    def validate_session(
        self, session_token: str
    ) -> Tuple[bool, Optional[AuthSession]]:
        """
        Validate a session token and return AuthSession object

        Args:
            session_token: Session token to validate

        Returns:
            Tuple of (is_valid: bool, session: Optional[AuthSession])
        """
        is_valid, parent_id = self.validate_session_token(session_token)

        if not is_valid or not parent_id:
            return False, None

        # Get parent info for email (decrypt from encrypted_email, never read plaintext)
        try:
            rows = self.db.execute_query(
                "SELECT encrypted_email, role FROM accounts WHERE parent_id = ?",
                (parent_id,),
            )
            encrypted = rows[0]["encrypted_email"] if rows else None
            email = get_email_crypto().decrypt_email(encrypted) if encrypted else None
            role = rows[0]["role"] if rows else "parent"
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(
                "Could not retrieve role/email for session (parent_id=%s): %s",
                parent_id,
                e,
            )
            email = None
            role = "parent"
        except DB_ERRORS as e:
            logger.error(
                f"Database error retrieving role/email for session (parent_id={parent_id}): {e}"
            )
            email = None
            role = "parent"

        # Create AuthSession object
        session = AuthSession(
            user_id=parent_id,
            role=role,
            session_token=session_token,
            email=email,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        return True, session

    def update_parent_email(self, parent_id: str, new_email: str) -> bool:
        """
        Update parent's email address

        Args:
            parent_id: Parent ID
            new_email: New email address

        Returns:
            bool: True if successful, False otherwise
        """
        # Validate email format
        import re

        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, new_email):
            logger.warning(f"Invalid email format: {mask_email(new_email)}")
            return False

        try:
            # Prepare hashed/encrypted versions for lookup and storage
            email_crypto = get_email_crypto()
            new_hash, new_encrypted = email_crypto.prepare_email_for_storage(new_email)

            # Check if email already exists (use email_hash for lookup)
            rows = self.db.execute_query(
                "SELECT parent_id FROM accounts WHERE email_hash = ? AND parent_id != ?",
                (new_hash, parent_id),
            )

            if rows:
                logger.warning("Email already in use")
                return False

            # Update email_hash and encrypted_email; clear plaintext email column
            self.db.execute_write(
                "UPDATE accounts SET email = NULL, email_hash = ?, encrypted_email = ? "
                "WHERE parent_id = ?",
                (new_hash, new_encrypted, parent_id),
            )

            logger.info(f"Email updated for parent {parent_id[:8]}...")
            return True

        except DB_ERRORS as e:
            logger.error(f"Email update failed: {e}")
            return False

    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Change parent password

        Args:
            user_id: Parent identifier (parent_id)
            current_password: Current password for verification
            new_password: New password

        Returns:
            Tuple of (success, error_message or None)
        """
        try:
            # Validate new password strength
            valid, error_msg = self._validate_password_strength(new_password)
            if not valid:
                return False, error_msg

            # Get current password hash from accounts table
            rows = self.db.execute_query(
                "SELECT password_hash FROM accounts WHERE parent_id = ?", (user_id,)
            )

            if not rows:
                return False, "Parent not found"

            row = rows[0]
            password_hash = row[0] if isinstance(row, tuple) else row["password_hash"]

            # Verify current password
            try:
                verified = self.ph.verify(password_hash, current_password)
            except (
                Exception
            ) as e:  # Intentional catch-all: argon2 can raise varied internal errors
                logger.debug(
                    f"Password verification failed during password change: {e}"
                )
                verified = False

            if not verified:
                return False, "Current password is incorrect"

            # Hash new password
            try:
                new_hash = self.ph.hash(new_password)
            except (
                Exception
            ) as e:  # Intentional catch-all: argon2 can raise varied internal errors
                logger.error(f"Password hashing failed during password change: {e}")
                return (
                    False,
                    "Password change failed due to server error. Please try again.",
                )

            # Update database
            self.db.execute_write(
                "UPDATE accounts SET password_hash = ? WHERE parent_id = ?",
                (new_hash, user_id),
            )

            # Invalidate all sessions for security
            self.db.execute_write(
                "UPDATE auth_tokens SET is_valid = 0 WHERE parent_id = ?", (user_id,)
            )

            # Remove from cache (Redis or fallback)
            self._delete_user_sessions_from_cache(user_id)

            logger.info(f"Password changed for user: {user_id}")
            return True, None

        except DB_ERRORS as e:
            logger.error(f"Password change failed: {e}")
            return False, "Password change failed. Please try again."

    def get_user_info(self, user_id: str) -> Optional[Dict]:
        """
        Get user information

        Args:
            user_id: User identifier

        Returns:
            User info dictionary or None
        """
        try:
            result = self.db.execute_read(
                """
                SELECT parent_id, username, encrypted_email, created_at, last_login, role, email_verified
                FROM accounts WHERE parent_id = ?
                """,
                (user_id,),
            )

            if not result:
                return None

            row = result[0]
            if isinstance(row, tuple):
                (
                    parent_id,
                    username,
                    encrypted_email,
                    created_at,
                    last_login,
                    role,
                    email_verified,
                ) = row
            else:
                parent_id = row["parent_id"]
                username = row["username"]
                encrypted_email = row["encrypted_email"]
                created_at = row["created_at"]
                last_login = row["last_login"]
                try:
                    role = row["role"]
                except (KeyError, IndexError, TypeError):
                    role = "parent"
                try:
                    email_verified = row["email_verified"]
                except (KeyError, IndexError, TypeError):
                    email_verified = False

            # Decrypt email from encrypted_email; never read plaintext column
            email = (
                get_email_crypto().decrypt_email(encrypted_email)
                if encrypted_email
                else None
            )

            return {
                "user_id": parent_id,
                "parent_id": parent_id,
                "username": username,
                "email": email,
                "role": role or "parent",
                "created_at": created_at,
                "last_login": last_login,
                "email_verified": bool(email_verified),
            }

        except DB_ERRORS as e:
            logger.error(f"Failed to get user info: {e}")
            return None

    def generate_verification_token(
        self, user_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Generate email verification token

        Args:
            user_id: User identifier

        Returns:
            Tuple: (success, token, error_message)
        """
        try:
            # Generate secure random token (URL-safe)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            token_id = f"verify_{secrets.token_hex(8)}"

            # Token expires in 24 hours
            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(hours=24)

            # Store token in database
            self.db.execute_write(
                """
                INSERT INTO auth_tokens (token_id, user_id, token_type, token_hash, created_at, expires_at)
                VALUES (?, ?, 'email_verification', ?, ?, ?)
                """,
                (
                    token_id,
                    user_id,
                    token_hash,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )

            logger.info(f"Email verification token generated for user: {user_id}")
            return True, token, None

        except DB_ERRORS as e:
            logger.error(f"Failed to generate verification token: {e}")
            return False, None, str(e)

    def verify_email_token(
        self, token: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify email verification token and mark email as verified

        Args:
            token: Verification token from email link

        Returns:
            Tuple: (success, user_id, error_message)
        """
        try:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            now = datetime.now(timezone.utc)

            # Find valid token
            result = self.db.execute_read(
                """
                SELECT token_id, user_id, expires_at
                FROM auth_tokens
                WHERE token_hash = ? AND token_type = 'email_verification'
                  AND is_valid = 1 AND used_at IS NULL
                """,
                (token_hash,),
            )

            if not result:
                return False, None, "Invalid or expired verification token"

            token_id, user_id, expires_at = result[0]

            # Check expiration using proper datetime comparison
            expires_dt = (
                datetime.fromisoformat(expires_at)
                if isinstance(expires_at, str)
                else expires_at
            )
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt < now:
                return False, None, "Verification token has expired"

            # Mark token as used
            self.db.execute_write(
                "UPDATE auth_tokens SET used_at = ?, is_valid = 0 WHERE token_id = ?",
                (now.isoformat(), token_id),
            )

            # Mark email as verified
            self.db.execute_write(
                "UPDATE accounts SET email_verified = 1 WHERE parent_id = ?", (user_id,)
            )

            logger.info(f"Email verified for user: {user_id}")
            return True, user_id, None

        except DB_ERRORS as e:
            logger.error(f"Email verification failed: {e}")
            return False, None, str(e)

    def generate_password_reset_token(
        self, email: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Generate password reset token

        Args:
            email: User's email address

        Returns:
            Tuple: (success, token, error_message)
        """
        try:
            # Look up user by email hash
            email_crypto = get_email_crypto()
            email_hash = email_crypto.hash_email(email)

            result = self.db.execute_read(
                "SELECT parent_id FROM accounts WHERE email_hash = ?", (email_hash,)
            )

            if not result:
                # Don't reveal if email exists - return success anyway for security
                logger.warning(f"Password reset requested for non-existent email")
                return True, None, None

            user_id = (
                result[0]["parent_id"] if isinstance(result[0], dict) else result[0][0]
            )

            # Invalidate any existing password reset tokens for this user
            self.db.execute_write(
                """
                UPDATE auth_tokens
                SET is_valid = 0
                WHERE user_id = ? AND token_type = 'password_reset' AND is_valid = 1
                """,
                (user_id,),
            )

            # Generate secure random token
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            token_id = f"reset_{secrets.token_hex(8)}"

            # Token expires in 1 hour (shorter for security)
            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(hours=1)

            # Store token
            self.db.execute_write(
                """
                INSERT INTO auth_tokens (token_id, user_id, token_type, token_hash, created_at, expires_at)
                VALUES (?, ?, 'password_reset', ?, ?, ?)
                """,
                (
                    token_id,
                    user_id,
                    token_hash,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )

            logger.info(f"Password reset token generated for user: {user_id}")
            return True, token, None

        except DB_ERRORS as e:
            logger.error(f"Failed to generate password reset token: {e}")
            return False, None, str(e)

    def reset_password_with_token(
        self, token: str, new_password: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Reset password using reset token

        Args:
            token: Password reset token from email
            new_password: New password

        Returns:
            Tuple: (success, error_message)
        """
        try:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            now = datetime.now(timezone.utc)

            # Find valid token
            result = self.db.execute_read(
                """
                SELECT token_id, user_id, expires_at
                FROM auth_tokens
                WHERE token_hash = ? AND token_type = 'password_reset'
                  AND is_valid = 1 AND used_at IS NULL
                """,
                (token_hash,),
            )

            if not result:
                return False, "Invalid or expired reset token"

            token_id, user_id, expires_at = result[0]

            # Check expiration using proper datetime comparison
            expires_dt = (
                datetime.fromisoformat(expires_at)
                if isinstance(expires_at, str)
                else expires_at
            )
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt < now:
                return False, "Reset token has expired. Please request a new one."

            # Validate new password strength
            valid, error_msg = self._validate_password_strength(new_password)
            if not valid:
                return False, error_msg

            # Hash new password using PasswordHasher (Argon2/PBKDF2 includes salt internally)
            try:
                new_hash = self.ph.hash(new_password)
            except (
                Exception
            ) as e:  # Intentional catch-all: argon2 can raise varied internal errors
                logger.error(f"Password hashing failed during reset: {e}")
                return False, "Password reset failed. Please try again."

            # Update password (note: modern hashers include salt in the hash)
            self.db.execute_write(
                "UPDATE accounts SET password_hash = ? WHERE parent_id = ?",
                (new_hash, user_id),
            )

            # Mark token as used
            self.db.execute_write(
                "UPDATE auth_tokens SET used_at = ?, is_valid = 0 WHERE token_id = ?",
                (now.isoformat(), token_id),
            )

            # Invalidate all sessions for security
            self.db.execute_write(
                "UPDATE auth_tokens SET is_valid = 0 WHERE parent_id = ?", (user_id,)
            )

            # Clear session cache (Redis or fallback)
            self._delete_user_sessions_from_cache(user_id)

            logger.info(f"Password reset successful for user: {user_id}")
            return True, None

        except DB_ERRORS as e:
            logger.error(f"Password reset failed: {e}")
            return False, str(e)

    def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired sessions

        Returns:
            Number of sessions cleaned up
        """
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Delete expired sessions
            result = self.db.execute_write(
                "DELETE FROM auth_tokens WHERE expires_at < ? OR is_valid = 0", (now,)
            )

            count = result if result else 0
            logger.info(f"Cleaned up {count} expired sessions")
            return count

        except DB_ERRORS as e:
            logger.error(f"Session cleanup failed: {e}")
            return 0


# Create a default auth_manager instance for tests and runtime convenience.
try:
    from storage.database import db_manager as default_db_manager

    auth_manager = AuthenticationManager(default_db_manager)
except ImportError:
    # Fall back to an in-memory manager if DB not available during import
    auth_manager = AuthenticationManager(db_manager=None)
