"""Shamir's Secret Sharing for emergency key recovery (extracted verbatim)."""

import base64
import secrets
from typing import List, Tuple

from core.key_management import (
    KeyManagementError,
    get_audit_logger,
)
from utils.logger import get_logger

logger = get_logger(__name__)

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


def create_key_shares(key: str, total_shares: int = 5, threshold: int = 3) -> List[str]:
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
        key_bytes = base64.urlsafe_b64decode(key.encode("ascii"))
    except (ValueError, TypeError) as e:
        raise KeyManagementError(f"Invalid key format: {e}")

    # Convert key bytes to integer
    secret = int.from_bytes(key_bytes, byteorder="big")

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
        details={"total_shares": total_shares, "threshold": threshold},
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
            index_str, value_hex = share.split(":")
            x = int(index_str)
            y = int(value_hex, 16)
            parsed_shares.append((x, y))
        except ValueError as e:
            raise KeyManagementError(f"Invalid share format: {e}")

    # Reconstruct secret using Lagrange interpolation
    secret = _lagrange_interpolate(parsed_shares, _PRIME)

    # Convert back to bytes (32 bytes for AES-256)
    key_bytes = secret.to_bytes(32, byteorder="big")

    # Encode as base64
    recovered_key = base64.urlsafe_b64encode(key_bytes).decode("ascii")

    # Audit log
    audit = get_audit_logger()
    audit.log_operation(
        operation="key_recovered_from_shares",
        success=True,
        details={
            "shares_used": len(shares),
            "share_indices": [s.split(":")[0] for s in shares],
        },
    )

    logger.info(f"Key recovered using {len(shares)} shares")

    return recovered_key


# =============================================================================
# KEY ROTATION POLICY
# =============================================================================
