"""Email-verification token mixin for AuthenticationManager.

Extracted verbatim from ``core/authentication.py`` (behavior-preserving
refactor). ``EmailVerificationMixin`` issues and verifies the one-time email
verification tokens stored in ``auth_tokens``. It is mixed into
``AuthenticationManager`` and uses only ``self.db`` and module-level imports;
none of these symbols are patched by the test suite via ``core.authentication``
(``verify_email_token`` is only ever stubbed on Mock instances of the manager).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)


class EmailVerificationMixin:
    """Generate and verify one-time email-verification tokens."""

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
