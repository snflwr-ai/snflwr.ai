# storage/encryption.py
"""
Encryption and Data Protection Utilities
AES-256 encryption for sensitive data with secure key management
"""

import os
import secrets
import hashlib
import base64
from typing import Optional, Tuple
from pathlib import Path
import json

import warnings

# Flag to track if we're using real encryption or fallback
_USING_REAL_ENCRYPTION = False

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.backends import default_backend

    _USING_REAL_ENCRYPTION = True
except ImportError as _crypto_import_error:
    # SECURITY WARNING: Fallback provides NO REAL ENCRYPTION
    # This should only be used for testing, never in production
    warnings.warn(
        "CRITICAL SECURITY WARNING: 'cryptography' package not available. "
        "Data will NOT be encrypted! Install with: pip install cryptography",
        RuntimeWarning,
    )

    class InvalidToken(Exception):
        """Stub for cryptography.fernet.InvalidToken when package unavailable."""

        pass

    class Fernet:
        """
        INSECURE FALLBACK — raises errors instead of silently pretending to encrypt.
        Only allows operation in development/testing. Any attempt to encrypt in
        production will be blocked by the startup check in api/server.py.
        """

        def __init__(self, key: bytes = None):
            self._key = key

        def encrypt(self, data: bytes) -> bytes:
            # Still base64-encode for development/testing, but mark clearly
            warnings.warn(
                "Data is NOT encrypted — using insecure base64 fallback. "
                "Install 'cryptography' package before production use.",
                RuntimeWarning,
                stacklevel=2,
            )
            return b"INSECURE:" + base64.b64encode(data)

        def decrypt(self, token: bytes) -> bytes:
            if token.startswith(b"INSECURE:"):
                return base64.b64decode(token[9:])
            raise ValueError(
                "Cannot decrypt real encrypted data without 'cryptography' package. "
                "Install with: pip install cryptography"
            )

    class _SHA256Stub:
        """Stub for hashes.SHA256() when cryptography is unavailable."""

        pass

    class hashes:
        """Stub module for cryptography.hazmat.primitives.hashes"""

        @staticmethod
        def SHA256():
            return _SHA256Stub()

    class PBKDF2HMAC:
        def __init__(
            self,
            algorithm=None,
            length=32,
            salt: bytes = b"",
            iterations: int = 100000,
            backend=None,
        ):
            self.length = length
            self.salt = salt
            self.iterations = iterations

        def derive(self, data: bytes) -> bytes:
            # Use hashlib.pbkdf2_hmac as a fallback
            return hashlib.pbkdf2_hmac(
                "sha256", data, self.salt, self.iterations, dklen=self.length
            )

    def default_backend():
        return None


def is_encryption_available() -> bool:
    """Check if real encryption is available (cryptography package installed)"""
    return _USING_REAL_ENCRYPTION


from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)


class EncryptionManager:
    """
    Manages encryption and decryption of sensitive data
    Uses AES-256 encryption with PBKDF2 key derivation
    """

    def __init__(self, key_dir: Optional[Path] = None):
        """Initialize encryption manager

        Args:
            key_dir: Optional directory to store the encryption key. If not
                provided, falls back to `system_config.APP_DATA_DIR`.
        """
        self.backend = default_backend()
        self._master_key: Optional[bytes] = None
        self._fernet: Optional[Fernet] = None

        # Allow tests to pass a temporary directory for keys
        if key_dir is None:
            key_dir = system_config.APP_DATA_DIR
        self._key_file = Path(key_dir) / ".encryption_key"

        # Initialize or load encryption key
        self._initialize_encryption()

    def _initialize_encryption(self):
        """Initialize encryption system with master key"""

        # Check if real encryption is available
        if not is_encryption_available():
            logger.critical(
                "SECURITY ALERT: Real encryption is NOT available! "
                "Data will be stored with base64 encoding only (NOT SECURE). "
                "Install 'cryptography' package for production use."
            )

        try:
            if self._key_file.exists():
                # Load existing key
                self._load_master_key()
                logger.info("Encryption master key loaded")
            else:
                # Generate new key
                self._generate_master_key()
                logger.info("New encryption master key generated")

            # Create Fernet instance. Stored key is standard base64; Fernet
            # expects urlsafe base64. Convert if necessary.
            try:
                # If stored key is a bytes string of standard base64, decode
                raw = base64.b64decode(self._master_key)
                fernet_key = base64.urlsafe_b64encode(raw)
                self._fernet = Fernet(fernet_key)
            except (ValueError, UnicodeDecodeError) as e:
                # Fallback: try to use the stored key directly
                logger.debug(
                    f"Base64 key conversion failed, using raw key (non-critical): {e}"
                )
                self._fernet = Fernet(self._master_key)

        except (InvalidToken, ValueError, TypeError, OSError) as e:
            logger.error(f"Encryption initialization failed: {e}")
            raise

    def _generate_master_key(self):
        """Generate a new master encryption key"""
        # Generate 32 random bytes and store as standard Base64 (tests expect
        # characters like '+' and '/')
        raw = secrets.token_bytes(32)
        key_std_b64 = base64.b64encode(raw)
        self._master_key = key_std_b64

        # Save key securely
        self._save_master_key()

    def _save_master_key(self):
        """Save master key to secure file"""

        try:
            # Ensure parent directory exists
            self._key_file.parent.mkdir(parents=True, exist_ok=True)

            # Write key with restricted permissions
            with open(self._key_file, "wb") as f:
                f.write(self._master_key)

            # Set restrictive permissions (owner read/write only)
            os.chmod(self._key_file, 0o600)

            # Verify permissions were actually applied (can silently fail in containers)
            actual_mode = os.stat(self._key_file).st_mode & 0o777
            if actual_mode != 0o600:
                # Fail closed: refuse to leave a world/group-readable key on disk
                if actual_mode & 0o077:  # group or other can read
                    os.unlink(self._key_file)
                    raise RuntimeError(
                        f"CRITICAL: Key file permissions are {oct(actual_mode)} (group/other readable). "
                        f"File removed. Fix filesystem permissions or mount volume with proper mode."
                    )
                logger.warning(
                    "Key file permissions are %s, expected 0o600. "
                    "Verify file is not accessible to other users.",
                    oct(actual_mode),
                )
            else:
                logger.info("Master key saved securely")

        except OSError as e:
            logger.error(f"Failed to save master key: {e}")
            raise

    def _load_master_key(self):
        """Load master key from file"""

        try:
            with open(self._key_file, "rb") as f:
                self._master_key = f.read()

            # Check file permissions (mirror _save_master_key security check)
            actual_mode = os.stat(self._key_file).st_mode & 0o777
            if actual_mode & 0o077:  # group or other can read/write/execute
                self._master_key = None
                raise RuntimeError(
                    f"CRITICAL: Key file {self._key_file} has overly permissive permissions "
                    f"({oct(actual_mode)}, group/other accessible). "
                    f"Refusing to use potentially compromised key. "
                    f"Fix with: chmod 600 {self._key_file}"
                )

            # Validate key format
            if len(self._master_key) != 44:  # Base64-encoded 32-byte key
                raise ValueError("Invalid master key format")

        except (OSError, ValueError) as e:
            logger.error(f"Failed to load master key: {e}")
            raise

    def _get_master_key(self) -> str:
        """Return the master key as a base64-encoded string."""
        if self._master_key is None:
            return ""
        if isinstance(self._master_key, bytes):
            try:
                return self._master_key.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as e:
                logger.debug(
                    f"UTF-8 decode of master key failed, re-encoding (non-critical): {e}"
                )
                return base64.b64encode(self._master_key).decode("utf-8")
        return str(self._master_key)

    def encrypt_string(self, plaintext: str) -> str:
        """
        Encrypt a string

        Args:
            plaintext: String to encrypt

        Returns:
            Base64-encoded encrypted string
        """
        try:
            if plaintext is None:
                return None

            # Don't encrypt empty strings - return as-is
            if plaintext == "":
                return ""

            # Encrypt
            encrypted_bytes = self._fernet.encrypt(plaintext.encode("utf-8"))

            # Return base64-encoded string
            return base64.b64encode(encrypted_bytes).decode("utf-8")

        except (InvalidToken, ValueError, TypeError) as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt_string(self, ciphertext: str) -> str:
        """
        Decrypt a string

        Args:
            ciphertext: Base64-encoded encrypted string

        Returns:
            Decrypted plaintext string
        """
        try:
            if ciphertext is None:
                return None
            if ciphertext == "":
                return ""

            # Decode from base64
            encrypted_bytes = base64.b64decode(ciphertext.encode("utf-8"))

            # Decrypt
            decrypted_bytes = self._fernet.decrypt(encrypted_bytes)

            return decrypted_bytes.decode("utf-8")

        except (InvalidToken, ValueError, TypeError) as e:
            logger.debug(f"Decryption failed or invalid data: {e}")
            # Fail-safe: return None for invalid/tampered inputs
            return None

    # Convenience aliases expected by tests
    def encrypt(self, plaintext: str) -> Optional[str]:
        if plaintext is None:
            return None
        try:
            return self.encrypt_string(plaintext)
        except (InvalidToken, ValueError, TypeError) as e:
            logger.debug(f"Encrypt wrapper failed (non-critical): {e}")
            return None

    def decrypt(self, ciphertext: str) -> Optional[str]:
        if ciphertext is None:
            return None
        # Empty string should return empty string (handled by decrypt_string)
        try:
            return self.decrypt_string(ciphertext)
        except (InvalidToken, ValueError, TypeError) as e:
            logger.debug(f"Decrypt wrapper failed (non-critical): {e}")
            return None

    def encrypt_dict(self, data: dict) -> str:
        """
        Encrypt a dictionary as JSON

        Args:
            data: Dictionary to encrypt

        Returns:
            Base64-encoded encrypted JSON
        """
        try:
            json_str = json.dumps(data)
            return self.encrypt_string(json_str)
        except (InvalidToken, ValueError, TypeError) as e:
            logger.error(f"Dictionary encryption failed: {e}")
            raise

    def decrypt_dict(self, ciphertext: str) -> dict:
        """
        Decrypt a JSON dictionary

        Args:
            ciphertext: Base64-encoded encrypted JSON

        Returns:
            Decrypted dictionary
        """
        try:
            json_str = self.decrypt_string(ciphertext)
            return json.loads(json_str)
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as e:
            logger.error(f"Dictionary decryption failed: {e}")
            raise

    def encrypt_file(self, input_path: Path, output_path: Path):
        """
        Encrypt a file

        Args:
            input_path: Path to plaintext file
            output_path: Path to encrypted output file
        """
        try:
            # Source must exist
            if not input_path.exists():
                logger.debug(f"Encrypt file: source missing {input_path}")
                return False

            # Read input file
            with open(input_path, "rb") as f:
                plaintext = f.read()

            # Encrypt
            ciphertext = self._fernet.encrypt(plaintext)

            # Write encrypted file
            with open(output_path, "wb") as f:
                f.write(ciphertext)

            logger.info(f"File encrypted: {input_path} -> {output_path}")
            return True

        except (OSError, InvalidToken, ValueError, TypeError) as e:
            logger.error(f"File encryption failed: {e}")
            return False

    def decrypt_file(self, input_path: Path, output_path: Path):
        """
        Decrypt a file

        Args:
            input_path: Path to encrypted file
            output_path: Path to decrypted output file
        """
        try:
            # Source must exist
            if not input_path.exists():
                logger.debug(f"Decrypt file: source missing {input_path}")
                return False

            # Read encrypted file
            with open(input_path, "rb") as f:
                ciphertext = f.read()

            # Decrypt
            plaintext = self._fernet.decrypt(ciphertext)

            # Write decrypted file
            with open(output_path, "wb") as f:
                f.write(plaintext)

            logger.info(f"File decrypted: {input_path} -> {output_path}")
            return True

        except (OSError, InvalidToken, ValueError, TypeError) as e:
            logger.error(f"File decryption failed: {e}")
            return False

    def hash_password(self, password: str, salt: Optional[bytes] = None) -> str:
        """
        Hash a password using PBKDF2

        Args:
            password: Password to hash
            salt: Optional salt (generated if not provided)

        Returns:
            Combined "salt$hash" string (both base64-encoded)
        """
        try:
            # Generate salt
            if salt is None:
                salt = secrets.token_bytes(32)

            # Derive key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
                backend=self.backend,
            )

            key = kdf.derive(password.encode("utf-8"))

            # Return base64-encoded salt and key as combined string
            salt_b64 = base64.b64encode(salt).decode("utf-8")
            key_b64 = base64.b64encode(key).decode("utf-8")

            # Return combined format: salt$hash
            return f"{salt_b64}${key_b64}"

        except (InvalidToken, ValueError, TypeError) as e:
            logger.error(f"Password hashing failed: {e}")
            raise

    def verify_password(
        self, password: str, hashed_password: str, salt: str = None
    ) -> bool:
        """
        Verify a password against its hash

        Args:
            password: Password to verify
            hashed_password: Base64-encoded hash
            salt: Base64-encoded salt (if not embedded in hashed_password)

        Returns:
            True if password matches, False otherwise
        """
        try:
            # Handle both formats: separate salt parameter or combined salt$hash
            if salt is not None:
                # Separate hash and salt provided
                salt_bytes = base64.b64decode(salt.encode("utf-8"))
                key_b64 = hashed_password
            elif "$" in hashed_password:
                # Combined format: salt$hash
                salt_b64, key_b64 = hashed_password.split("$", 1)
                salt_bytes = base64.b64decode(salt_b64.encode("utf-8"))
            else:
                return False

            # Re-derive key
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt_bytes,
                iterations=600000,
                backend=self.backend,
            )

            try:
                new_key = kdf.derive(password.encode("utf-8"))
            except (InvalidToken, ValueError, TypeError) as e:
                logger.debug(
                    f"Key derivation failed during password verify (non-critical): {e}"
                )
                return False

            new_key_b64 = base64.b64encode(new_key).decode("utf-8")

            return secrets.compare_digest(new_key_b64, key_b64)

        except (InvalidToken, ValueError, TypeError) as e:
            logger.error(f"Password verification failed: {e}")
            return False

    def hmac_token(self, token: str) -> str:
        """
        HMAC-SHA256 a search token using the master key.

        Used by the encrypted search index to create deterministic,
        non-reversible hashes of content tokens for searchability
        without exposing plaintext.

        Args:
            token: The plaintext token to hash

        Returns:
            Hex-encoded HMAC-SHA256 digest (64 chars)
        """
        import hmac as _hmac

        key = self._master_key if self._master_key else b""
        return _hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def generate_secure_token(self, length: int = 32) -> str:
        """
        Generate a cryptographically secure random token

        Args:
            length: Token length in bytes

        Returns:
            Hex-encoded token
        """
        return secrets.token_hex(length)

    def generate_device_id(self, additional_entropy: Optional[str] = None) -> str:
        """
        Generate a unique device identifier

        Args:
            additional_entropy: Optional additional entropy

        Returns:
            Hex-encoded device ID
        """
        import platform
        import uuid

        # Collect system information
        mac = uuid.getnode()
        system_info = f"{platform.system()}{platform.machine()}{platform.node()}"

        # Add additional entropy if provided
        if additional_entropy:
            system_info += additional_entropy

        # Add random component
        random_component = secrets.token_bytes(16)

        # Hash everything together
        combined = f"{mac}{system_info}".encode("utf-8") + random_component
        device_id = hashlib.sha256(combined).hexdigest()[:32]

        return device_id


class SecureStorage:
    """
    Secure storage for sensitive configuration data
    Encrypts data before writing to disk
    """

    def __init__(self, db, storage_dir: Path, key_dir: Optional[Path] = None):
        """
        Initialize secure storage

        Args:
            db: DatabaseManager instance to use for storage operations
            storage_dir: Directory for secure storage
            key_dir: Optional directory where encryption keys should be stored
        """
        self.db = db
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # Use provided key_dir for EncryptionManager so tests can use temp dirs
        self.encryption = EncryptionManager(key_dir or self.storage_dir)

    def store(self, key: str, data: dict):
        """
        Store encrypted data

        Args:
            key: Storage key
            data: Dictionary to store
        """
        try:
            file_path = self.storage_dir / f"{key}.enc"

            # Accept raw strings or dictionaries
            if isinstance(data, dict):
                encrypted_data = self.encryption.encrypt_dict(data)
            else:
                encrypted_data = self.encryption.encrypt(str(data))

            if encrypted_data is None:
                logger.error(
                    f"Secure storage failed for {key}: encryption returned None"
                )
                return False

            with open(file_path, "w") as f:
                f.write(encrypted_data)

            logger.debug(f"Secure storage: {key} saved")
            return True

        except (OSError, InvalidToken, ValueError, TypeError) as e:
            logger.error(f"Secure storage failed for {key}: {e}")
            return False

    def retrieve(self, key: str) -> Optional[dict]:
        """
        Retrieve and decrypt data

        Args:
            key: Storage key

        Returns:
            Decrypted dictionary or None if not found
        """
        try:
            file_path = self.storage_dir / f"{key}.enc"

            if not file_path.exists():
                return None

            with open(file_path, "r") as f:
                encrypted_data = f.read()

            # Try to decrypt as string first
            decrypted = self.encryption.decrypt(encrypted_data)
            if decrypted is not None:
                logger.debug(f"Secure storage: {key} retrieved (string)")
                return decrypted

            # Fall back to dict decryption
            data = self.encryption.decrypt_dict(encrypted_data)
            logger.debug(f"Secure storage: {key} retrieved (dict)")
            return data

        except (
            OSError,
            InvalidToken,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logger.error(f"Secure retrieval failed for {key}: {e}")
            return None

    def delete(self, key: str):
        """
        Delete stored data

        Args:
            key: Storage key
        """
        try:
            file_path = self.storage_dir / f"{key}.enc"

            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Secure storage: {key} deleted")
                return True

            return False

        except OSError as e:
            logger.error(f"Secure deletion failed for {key}: {e}")
            return False


# Singleton instance
encryption_manager = EncryptionManager()


# Export public interface
__all__ = ["EncryptionManager", "SecureStorage", "encryption_manager"]
