"""Password-management methods for AuthenticationManager (mixin).

Extracted verbatim from core/authentication.py. Composed into
AuthenticationManager alongside SessionCacheMixin / EmailVerificationMixin.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple

from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)


class _PasswordMixin:
    """Password validation, change, and reset for AuthenticationManager."""

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
