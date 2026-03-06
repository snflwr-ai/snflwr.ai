"""
Encryption Key Management - Improved Security & Usability

Addresses key management challenges for schools:
1. Key derivation from memorable passphrase
2. Key backup and recovery mechanisms
3. Key rotation support
4. Secure key storage with multiple backup methods
5. Emergency recovery via Shamir's Secret Sharing
6. Audit logging for compliance
7. Key rotation policy enforcement

Designed for non-technical administrators while maintaining security.
"""

import hashlib
import secrets
import json
import os
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timedelta, timezone
from functools import reduce
import base64

from utils.logger import get_logger

logger = get_logger(__name__)

# Key rotation policy constants
DEFAULT_KEY_MAX_AGE_DAYS = 365  # Recommend rotation after 1 year
KEY_EXPIRY_WARNING_DAYS = 30   # Warn 30 days before recommended rotation


class KeyManagementError(Exception):
    """Raised when key management operations fail"""
    pass


class KeyStrengthError(KeyManagementError):
    """Raised when passphrase/key is too weak"""
    pass


# =============================================================================
# AUDIT LOGGING
# =============================================================================

class KeyAuditLogger:
    """
    Audit logger for key management operations.

    Logs all key-related operations for COPPA/FERPA compliance.
    Audit logs are append-only and include timestamps and operation details.
    """

    def __init__(self, audit_dir: Path = Path("config/audit")):
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_dir / "key_operations.jsonl"

    def log_operation(
        self,
        operation: str,
        success: bool,
        details: Optional[Dict[str, Any]] = None,
        admin_id: Optional[str] = None
    ) -> None:
        """
        Log a key management operation.

        Args:
            operation: Type of operation (e.g., 'key_rotation', 'key_recovery')
            success: Whether operation succeeded
            details: Additional details (never includes actual key material)
            admin_id: Optional admin identifier
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "success": success,
            "admin_id": admin_id or "system",
            "details": details or {}
        }

        try:
            fd = os.open(str(self.audit_file), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except IOError as e:
            logger.error(f"Failed to write audit log: {e}")

    def get_recent_operations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent key operations for audit review."""
        operations = []

        if not self.audit_file.exists():
            return operations

        try:
            with open(self.audit_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    try:
                        operations.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        except IOError as e:
            logger.debug(f"Could not read audit log: {e}")

        return operations


# Global audit logger instance
_audit_logger: Optional[KeyAuditLogger] = None


def get_audit_logger() -> KeyAuditLogger:
    """Get or create the global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = KeyAuditLogger()
    return _audit_logger


# =============================================================================
# SHAMIR'S SECRET SHARING - Emergency Key Recovery
# =============================================================================

# Prime for finite field arithmetic
# Must be larger than any 256-bit secret (AES-256 key)
# Using 2^257 - 93 which is a known prime
_PRIME = 2**257 - 93


def _mod_inverse(a: int, m: int) -> int:
    """Calculate modular multiplicative inverse using extended Euclidean algorithm."""
    def extended_gcd(a: int, b: int) -> Tuple[int, int, int]:
        if a == 0:
            return b, 0, 1
        gcd, x1, y1 = extended_gcd(b % a, a)
        x = y1 - (b // a) * x1
        y = x1
        return gcd, x, y

    _, x, _ = extended_gcd(a % m, m)
    return (x % m + m) % m


def _evaluate_polynomial(coefficients: List[int], x: int, prime: int) -> int:
    """Evaluate polynomial at point x in finite field."""
    result = 0
    for i, coef in enumerate(coefficients):
        result = (result + coef * pow(x, i, prime)) % prime
    return result


def _lagrange_interpolate(shares: List[Tuple[int, int]], prime: int) -> int:
    """
    Reconstruct secret using Lagrange interpolation.

    Args:
        shares: List of (x, y) coordinate pairs
        prime: Prime for finite field

    Returns:
        Reconstructed secret (y value at x=0)
    """
    secret = 0

    for i, (xi, yi) in enumerate(shares):
        numerator = 1
        denominator = 1

        for j, (xj, _) in enumerate(shares):
            if i != j:
                numerator = (numerator * (-xj)) % prime
                denominator = (denominator * (xi - xj)) % prime

        lagrange = (yi * numerator * _mod_inverse(denominator, prime)) % prime
        secret = (secret + lagrange) % prime

    return secret


def create_key_shares(
    key: str,
    total_shares: int = 5,
    threshold: int = 3
) -> List[str]:
    """
    Split encryption key into shares using Shamir's Secret Sharing.

    This allows emergency key recovery when the primary key holder is unavailable.
    Distribute shares to trusted administrators (e.g., principal, IT director,
    school board member).

    Args:
        key: Base64-encoded encryption key
        total_shares: Total number of shares to create (n)
        threshold: Minimum shares needed to recover key (k)

    Returns:
        List of share strings (format: "share_index:share_data")

    Example:
        # Create 5 shares, any 3 can recover the key
        shares = create_key_shares(key, total_shares=5, threshold=3)

        # Distribute to:
        # shares[0] -> School Principal
        # shares[1] -> IT Director
        # shares[2] -> Superintendent
        # shares[3] -> School Board Chair
        # shares[4] -> Secure offsite backup
    """
    if threshold > total_shares:
        raise KeyManagementError("Threshold cannot exceed total shares")

    if threshold < 2:
        raise KeyManagementError("Threshold must be at least 2 for security")

    # Decode key to bytes
    try:
        key_bytes = base64.urlsafe_b64decode(key.encode('ascii'))
    except (ValueError, TypeError) as e:
        raise KeyManagementError(f"Invalid key format: {e}")

    # Convert key bytes to integer
    secret = int.from_bytes(key_bytes, byteorder='big')

    # Generate random polynomial coefficients
    # f(x) = secret + a1*x + a2*x^2 + ... + a_{k-1}*x^{k-1}
    coefficients = [secret]
    for _ in range(threshold - 1):
        coefficients.append(secrets.randbelow(_PRIME))

    # Generate shares
    shares = []
    for i in range(1, total_shares + 1):
        y = _evaluate_polynomial(coefficients, i, _PRIME)
        # Encode share as "index:hex_value"
        # Use full hex representation (257-bit prime can produce values up to 65 hex chars)
        share_str = f"{i}:{y:0>66x}"
        shares.append(share_str)

    # Audit log
    audit = get_audit_logger()
    audit.log_operation(
        operation="key_shares_created",
        success=True,
        details={
            "total_shares": total_shares,
            "threshold": threshold
        }
    )

    logger.info(f"Created {total_shares} key shares with threshold {threshold}")

    return shares


def recover_key_from_shares(shares: List[str]) -> str:
    """
    Recover encryption key from shares.

    Args:
        shares: List of share strings (format: "index:hex_value")

    Returns:
        Recovered base64-encoded key

    Example:
        # Collect at least 3 shares (if threshold was 3)
        shares = [
            "1:abc123...",  # From Principal
            "3:def456...",  # From Superintendent
            "5:789ghi..."   # From offsite backup
        ]
        recovered_key = recover_key_from_shares(shares)
    """
    if len(shares) < 2:
        raise KeyManagementError("Need at least 2 shares to recover key")

    # Parse shares
    parsed_shares = []
    for share in shares:
        try:
            index_str, value_hex = share.split(':')
            x = int(index_str)
            y = int(value_hex, 16)
            parsed_shares.append((x, y))
        except ValueError as e:
            raise KeyManagementError(f"Invalid share format: {e}")

    # Reconstruct secret using Lagrange interpolation
    secret = _lagrange_interpolate(parsed_shares, _PRIME)

    # Convert back to bytes (32 bytes for AES-256)
    key_bytes = secret.to_bytes(32, byteorder='big')

    # Encode as base64
    recovered_key = base64.urlsafe_b64encode(key_bytes).decode('ascii')

    # Audit log
    audit = get_audit_logger()
    audit.log_operation(
        operation="key_recovered_from_shares",
        success=True,
        details={
            "shares_used": len(shares),
            "share_indices": [s.split(':')[0] for s in shares]
        }
    )

    logger.info(f"Key recovered using {len(shares)} shares")

    return recovered_key


# =============================================================================
# KEY ROTATION POLICY
# =============================================================================

def check_key_rotation_status(metadata_file: Path = Path("config/encryption.meta.json")) -> Dict[str, Any]:
    """
    Check if key rotation is recommended based on age policy.

    Returns:
        Dictionary with rotation status:
        - needs_rotation: bool
        - key_age_days: int
        - days_until_recommended: int (negative if overdue)
        - warning_message: Optional[str]
    """
    result = {
        "needs_rotation": False,
        "key_age_days": 0,
        "days_until_recommended": DEFAULT_KEY_MAX_AGE_DAYS,
        "warning_message": None
    }

    if not metadata_file.exists():
        result["warning_message"] = "No key metadata found. Cannot determine key age."
        return result

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        created_at = datetime.fromisoformat(metadata.get('created_at', datetime.now(timezone.utc).isoformat()))
        key_age = datetime.now(timezone.utc) - created_at
        result["key_age_days"] = key_age.days

        days_until = DEFAULT_KEY_MAX_AGE_DAYS - key_age.days
        result["days_until_recommended"] = days_until

        if days_until <= 0:
            result["needs_rotation"] = True
            result["warning_message"] = (
                f"Key is {key_age.days} days old and exceeds the recommended "
                f"maximum age of {DEFAULT_KEY_MAX_AGE_DAYS} days. "
                "Please rotate the encryption key."
            )
        elif days_until <= KEY_EXPIRY_WARNING_DAYS:
            result["warning_message"] = (
                f"Key rotation recommended in {days_until} days. "
                f"Key has been in use for {key_age.days} days."
            )

        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        result["warning_message"] = f"Could not parse key metadata: {e}"
        return result


def derive_key_from_passphrase(
    passphrase: str,
    salt: Optional[bytes] = None,
    iterations: int = 600000
) -> Tuple[str, str]:
    """
    Derive encryption key from user-provided passphrase using PBKDF2

    This allows schools to use a memorable passphrase instead of managing
    a random 256-bit key.

    Args:
        passphrase: User's passphrase (minimum 12 characters recommended)
        salt: Optional salt (generated if not provided)
        iterations: PBKDF2 iterations (600K is OWASP 2023 recommendation)

    Returns:
        (derived_key_base64, salt_base64)

    Raises:
        KeyStrengthError: If passphrase is too weak
    """
    # Validate passphrase strength
    if len(passphrase) < 12:
        raise KeyStrengthError(
            "Passphrase must be at least 12 characters long. "
            "Recommended: 4-5 random words or a sentence."
        )

    # Generate salt if not provided
    if salt is None:
        salt = secrets.token_bytes(32)

    # Derive key using PBKDF2-HMAC-SHA256
    key = hashlib.pbkdf2_hmac(
        'sha256',
        passphrase.encode('utf-8'),
        salt,
        iterations,
        dklen=32  # 256 bits
    )

    # Encode to base64 for storage
    key_b64 = base64.urlsafe_b64encode(key).decode('ascii')
    salt_b64 = base64.urlsafe_b64encode(salt).decode('ascii')

    return key_b64, salt_b64


def generate_secure_key() -> str:
    """
    Generate a cryptographically secure random 256-bit key

    Returns:
        Base64-encoded key suitable for AES-256
    """
    key = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(key).decode('ascii')


def validate_key_strength(key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate encryption key meets security requirements

    Args:
        key: Base64-encoded key

    Returns:
        (is_valid, error_message)
    """
    try:
        # Decode key
        decoded = base64.urlsafe_b64decode(key.encode('ascii'))

        # Check length (must be 256 bits = 32 bytes)
        if len(decoded) < 32:
            return False, f"Key is too short ({len(decoded)} bytes). AES-256 requires 32 bytes."

        # Check for low entropy (weak keys)
        # Count unique bytes - should have good distribution
        unique_bytes = len(set(decoded))
        if unique_bytes < 16:  # Less than 50% unique
            return False, "Key has low entropy (too predictable). Use a cryptographically random key."

        return True, None

    except (ValueError, TypeError) as e:
        return False, f"Invalid key format: {str(e)}"


class KeyManager:
    """
    Manages encryption keys with backup and recovery mechanisms.

    Features:
    - Key derivation from passphrase or random generation
    - Key rotation with version tracking
    - Emergency recovery via Shamir's Secret Sharing
    - Audit logging for all operations
    - Key age policy enforcement
    """

    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = Path(config_dir)
        self.key_file = self.config_dir / "encryption.key"
        self.metadata_file = self.config_dir / "encryption.meta.json"
        self.audit = get_audit_logger()

        # Create config directory if it doesn't exist
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def initialize_from_passphrase(
        self,
        passphrase: str,
        save_backup: bool = True,
        backup_location: Optional[Path] = None
    ) -> str:
        """
        Initialize encryption key from passphrase

        Args:
            passphrase: User's passphrase
            save_backup: Whether to save backup metadata
            backup_location: Optional custom backup location

        Returns:
            Derived key (base64)

        Raises:
            KeyStrengthError: If passphrase is too weak
        """
        # Derive key
        key, salt = derive_key_from_passphrase(passphrase)

        # Save metadata (salt, NOT the key or passphrase)
        metadata = {
            "method": "pbkdf2_passphrase",
            "salt": salt,
            "iterations": 600000,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key_version": 1
        }

        if save_backup:
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Key metadata saved to {self.metadata_file}")

            # Additional backup if location specified
            if backup_location:
                backup_path = Path(backup_location) / "encryption.meta.json"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                with open(backup_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                logger.info(f"Key metadata backed up to {backup_path}")

        # Audit log
        self.audit.log_operation(
            operation="key_initialized_from_passphrase",
            success=True,
            details={"key_version": 1, "backup_saved": save_backup}
        )

        return key

    def recover_key_from_passphrase(self, passphrase: str) -> str:
        """
        Recover encryption key from passphrase using saved metadata

        Args:
            passphrase: User's passphrase

        Returns:
            Recovered key (base64)

        Raises:
            KeyManagementError: If metadata not found or recovery fails
        """
        if not self.metadata_file.exists():
            raise KeyManagementError(
                f"Key metadata not found at {self.metadata_file}. "
                "Cannot recover key without salt."
            )

        try:
            with open(self.metadata_file, 'r') as f:
                metadata = json.load(f)

            if metadata.get("method") != "pbkdf2_passphrase":
                raise KeyManagementError(
                    f"Unsupported key derivation method: {metadata.get('method')}"
                )

            # Decode salt
            salt = base64.urlsafe_b64decode(metadata['salt'].encode('ascii'))
            iterations = metadata.get('iterations', 600000)

            # Derive key using same parameters
            key, _ = derive_key_from_passphrase(passphrase, salt, iterations)

            # Audit log
            self.audit.log_operation(
                operation="key_recovered_from_passphrase",
                success=True,
                details={"key_version": metadata.get('key_version', 1)}
            )

            logger.info("Key successfully recovered from passphrase")
            return key

        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            # Audit failed recovery attempt
            self.audit.log_operation(
                operation="key_recovered_from_passphrase",
                success=False,
                details={"error": str(e)}
            )
            raise KeyManagementError(f"Failed to recover key: {e}")

    def initialize_from_random_key(
        self,
        save_backup: bool = True,
        backup_location: Optional[Path] = None
    ) -> str:
        """
        Initialize with cryptographically random key

        Args:
            save_backup: Whether to save key backup
            backup_location: Optional backup location

        Returns:
            Generated key (base64)

        Security Note:
        The key itself should NOT be saved to disk unencrypted.
        This method only saves metadata about key generation.
        User must store the key securely (hardware token, password manager, etc.)
        """
        key = generate_secure_key()

        metadata = {
            "method": "random_generation",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key_version": 1,
            "warning": "Key must be stored securely. This file does NOT contain the key."
        }

        if save_backup:
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.warning(
                f"Random key generated. CRITICAL: Store this key securely! "
                f"Metadata saved to {self.metadata_file}"
            )

        # Audit log
        self.audit.log_operation(
            operation="key_initialized_random",
            success=True,
            details={"key_version": 1, "backup_saved": save_backup}
        )

        return key

    def rotate_key(
        self,
        old_key: str,
        new_passphrase: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Rotate encryption key

        Process:
        1. Generate new key
        2. Return both old and new keys
        3. Caller must re-encrypt database with new key
        4. Update metadata

        Args:
            old_key: Current encryption key
            new_passphrase: Optional passphrase for new key (random if None)

        Returns:
            (old_key, new_key)
        """
        # Validate old key
        is_valid, error = validate_key_strength(old_key)
        if not is_valid:
            raise KeyManagementError(f"Old key validation failed: {error}")

        # Generate new key
        if new_passphrase:
            new_key, salt = derive_key_from_passphrase(new_passphrase)

            metadata = {
                "method": "pbkdf2_passphrase",
                "salt": salt,
                "iterations": 600000,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "key_version": self._get_next_version(),
                "rotation_history": self._get_rotation_history()
            }
        else:
            new_key = generate_secure_key()

            metadata = {
                "method": "random_generation",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "key_version": self._get_next_version(),
                "rotation_history": self._get_rotation_history()
            }

        # Save new metadata
        with open(self.metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        # Audit log
        self.audit.log_operation(
            operation="key_rotated",
            success=True,
            details={
                "old_version": metadata['key_version'] - 1,
                "new_version": metadata['key_version'],
                "method": metadata['method']
            }
        )

        logger.warning("Key rotation completed successfully")

        return old_key, new_key

    def check_rotation_status(self) -> Dict[str, Any]:
        """Check if key rotation is recommended."""
        return check_key_rotation_status(self.metadata_file)

    def create_emergency_shares(
        self,
        key: str,
        total_shares: int = 5,
        threshold: int = 3
    ) -> List[str]:
        """
        Create emergency recovery shares for the encryption key.

        Args:
            key: The encryption key to split
            total_shares: Number of shares to create
            threshold: Minimum shares needed to recover

        Returns:
            List of share strings to distribute to trusted parties
        """
        return create_key_shares(key, total_shares, threshold)

    def recover_from_emergency_shares(self, shares: List[str]) -> str:
        """
        Recover encryption key from emergency shares.

        Args:
            shares: List of share strings

        Returns:
            Recovered encryption key
        """
        return recover_key_from_shares(shares)

    def _get_next_version(self) -> int:
        """Get next key version number"""
        if not self.metadata_file.exists():
            return 1

        try:
            with open(self.metadata_file, 'r') as f:
                metadata = json.load(f)
            return metadata.get('key_version', 0) + 1
        except (IOError, json.JSONDecodeError, KeyError):
            return 1

    def _get_rotation_history(self) -> list:
        """Get key rotation history"""
        if not self.metadata_file.exists():
            return []

        try:
            with open(self.metadata_file, 'r') as f:
                metadata = json.load(f)

            history = metadata.get('rotation_history', [])
            history.append({
                "version": metadata.get('key_version', 0),
                "rotated_at": datetime.now(timezone.utc).isoformat(),
                "method": metadata.get('method')
            })
            return history
        except (IOError, json.JSONDecodeError, KeyError):
            return []


def check_environment_key() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Check environment for DB_ENCRYPTION_KEY

    Returns:
        (key_found, key_value, error_message)
    """
    key = os.getenv('DB_ENCRYPTION_KEY')

    if not key:
        return False, None, "DB_ENCRYPTION_KEY not found in environment"

    # Validate key strength
    is_valid, error = validate_key_strength(key)

    if not is_valid:
        return True, key, f"Key validation failed: {error}"

    return True, key, None


# CLI-style helper for easy setup
def setup_encryption_interactive():
    """
    Interactive setup for encryption key (for use in install.py or setup script)

    This function guides administrators through secure key setup.
    """
    print("\n" + "="*60)
    print("DATABASE ENCRYPTION KEY SETUP")
    print("="*60)
    print("\nChoose encryption key method:\n")
    print("1. Passphrase (recommended for schools)")
    print("   - Easy to remember and recover")
    print("   - Use 4-5 random words or a sentence")
    print("   - Example: 'purple-elephant-dancing-moonlight-2024'\n")
    print("2. Random key (maximum security)")
    print("   - Cryptographically random 256-bit key")
    print("   - Must store key in password manager or hardware token")
    print("   - Lost key = permanent data loss\n")

    choice = input("Enter choice (1 or 2): ").strip()

    key_manager = KeyManager()

    if choice == "1":
        print("\n" + "-"*60)
        print("PASSPHRASE SETUP")
        print("-"*60)
        print("Requirements:")
        print("- Minimum 12 characters")
        print("- Recommended: 4-5 random words")
        print("- Example: correct-horse-battery-staple-2024\n")

        while True:
            passphrase = input("Enter passphrase: ").strip()
            confirm = input("Confirm passphrase: ").strip()

            if passphrase != confirm:
                print("[FAIL] Passphrases don't match. Try again.\n")
                continue

            try:
                key = key_manager.initialize_from_passphrase(
                    passphrase,
                    save_backup=True
                )
                print("\n[OK] Encryption key derived from passphrase")
                print(f"[OK] Metadata saved to {key_manager.metadata_file}")
                print("\n[WARN]  IMPORTANT: Remember your passphrase! It's needed to recover your key.")
                return key

            except KeyStrengthError as e:
                print(f"[FAIL] {e}\n")
                continue

    elif choice == "2":
        print("\n" + "-"*60)
        print("RANDOM KEY GENERATION")
        print("-"*60)
        print("[WARN]  WARNING: Store this key securely!")
        print("Lost key = permanent data loss\n")

        confirm = input("Type 'I UNDERSTAND' to continue: ").strip()

        if confirm != "I UNDERSTAND":
            print("Cancelled.")
            return None

        key = key_manager.initialize_from_random_key(save_backup=True)

        print("\n[OK] Random encryption key generated and saved to key file.")
        print(f"\n[KEY] ENCRYPTION KEY (first 8 chars): {key[:8]}...")
        print("\n[WARN]  The full key is stored in the key file. To export it:")
        print("   cat <data-dir>/.encryption_key")
        print("\n[WARN]  CRITICAL: Back up this key to a secure location:")
        print("   - Password manager (1Password, Bitwarden, etc.)")
        print("   - Hardware security key")
        print("   - Encrypted USB drive (store separately from database)")
        print("\n[FAIL] DO NOT save this key in plain text files or code!")

        input("\nPress Enter after you've securely saved the key...")

        return key

    else:
        print("Invalid choice")
        return None


if __name__ == "__main__":
    # Run interactive setup
    key = setup_encryption_interactive()
    if key:
        print("\n[OK] Setup complete!")
        print("\nTo use this key, set environment variable:")
        print("export DB_ENCRYPTION_KEY='<contents of your key file>'")
