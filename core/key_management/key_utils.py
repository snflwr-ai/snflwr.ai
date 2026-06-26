"""Key generation, rotation-status, derivation and strength checks.

Extracted verbatim from core/key_management.py.
"""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.key_management import (
    DEFAULT_KEY_MAX_AGE_DAYS,
    KEY_EXPIRY_WARNING_DAYS,
    KeyStrengthError,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def check_key_rotation_status(
    metadata_file: Path = Path("config/encryption.meta.json"),
) -> Dict[str, Any]:
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
        "warning_message": None,
    }  # type: Dict[str, Any]

    if not metadata_file.exists():
        result["warning_message"] = "No key metadata found. Cannot determine key age."
        return result

    try:
        with open(metadata_file, "r") as f:
            metadata = json.load(f)

        created_at = datetime.fromisoformat(
            metadata.get("created_at", datetime.now(timezone.utc).isoformat())
        )
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
    passphrase: str, salt: Optional[bytes] = None, iterations: int = 600000
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
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32  # 256 bits
    )

    # Encode to base64 for storage
    key_b64 = base64.urlsafe_b64encode(key).decode("ascii")
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")

    return key_b64, salt_b64


def generate_secure_key() -> str:
    """
    Generate a cryptographically secure random 256-bit key

    Returns:
        Base64-encoded key suitable for AES-256
    """
    key = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(key).decode("ascii")


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
        decoded = base64.urlsafe_b64decode(key.encode("ascii"))

        # Check length (must be 256 bits = 32 bytes)
        if len(decoded) < 32:
            return (
                False,
                f"Key is too short ({len(decoded)} bytes). AES-256 requires 32 bytes.",
            )

        # Check for low entropy (weak keys)
        # Count unique bytes - should have good distribution
        unique_bytes = len(set(decoded))
        if unique_bytes < 16:  # Less than 50% unique
            return (
                False,
                "Key has low entropy (too predictable). Use a cryptographically random key.",
            )

        return True, None

    except (ValueError, TypeError) as e:
        return False, f"Invalid key format: {str(e)}"
