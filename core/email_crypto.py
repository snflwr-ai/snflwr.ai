"""
Email Cryptography Helpers
Provides email hashing and encryption for COPPA compliance
"""

import hashlib
import threading
from storage.encryption import EncryptionManager


class EmailCrypto:
    """Handle email hashing and encryption"""

    def __init__(self):
        self.encryption = EncryptionManager()

    def hash_email(self, email: str) -> str:
        """
        Create SHA256 hash of email for database lookup

        Args:
            email: Email address to hash

        Returns:
            Hex string of SHA256 hash
        """
        return hashlib.sha256(email.lower().strip().encode()).hexdigest()

    def encrypt_email(self, email: str) -> str:
        """
        Encrypt email address for storage

        Args:
            email: Email address to encrypt

        Returns:
            Encrypted email string
        """
        return self.encryption.encrypt_string(email.lower().strip())

    def decrypt_email(self, encrypted_email: str) -> str:
        """
        Decrypt email address

        Args:
            encrypted_email: Encrypted email string

        Returns:
            Decrypted email address
        """
        return self.encryption.decrypt_string(encrypted_email)

    def prepare_email_for_storage(self, email: str) -> tuple[str, str]:
        """
        Prepare email for database storage

        Args:
            email: Plain email address

        Returns:
            Tuple of (email_hash, encrypted_email)
        """
        normalized = email.lower().strip()
        return (
            self.hash_email(normalized),
            self.encrypt_email(normalized)
        )


# Singleton instance
_email_crypto = None
_email_crypto_lock = threading.Lock()


def get_email_crypto() -> EmailCrypto:
    """Get singleton EmailCrypto instance (thread-safe)"""
    global _email_crypto
    if _email_crypto is None:
        with _email_crypto_lock:
            if _email_crypto is None:
                _email_crypto = EmailCrypto()
    return _email_crypto
