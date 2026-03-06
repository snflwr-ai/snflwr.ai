"""
Age Verification Module - COPPA Compliance

Implements age verification and parental consent requirements per COPPA regulations.

COPPA Requirements:
- Children under 13 require verifiable parental consent
- Age must be accurately verified (birthdate preferred over self-reported age)
- Consent must be documented and auditable
- Parents must be able to review and revoke consent

References:
- 15 U.S.C. §§ 6501–6506 (COPPA statute)
- 16 CFR Part 312 (FTC regulations)
"""

from datetime import datetime, date, timezone
from typing import Optional, Tuple
from dataclasses import dataclass
import hashlib
import hmac
import secrets

from utils.logger import get_logger, sanitize_log_value
from storage.db_adapters import DB_ERRORS

logger = get_logger(__name__)

COPPA_AGE_THRESHOLD = 13  # Age below which parental consent is required


@dataclass
class AgeVerificationResult:
    """Result of age verification check"""
    age: int
    is_under_13: bool
    requires_parental_consent: bool
    has_parental_consent: bool
    is_compliant: bool
    verification_date: str
    error_message: Optional[str] = None


class AgeVerificationError(Exception):
    """Raised when age verification fails"""
    pass


class ParentalConsentRequired(Exception):
    """Raised when parental consent is required but not provided"""
    pass


def calculate_age_from_birthdate(birthdate: str) -> int:
    """
    Calculate current age from birthdate

    Args:
        birthdate: ISO 8601 date string (YYYY-MM-DD)

    Returns:
        Age in years

    Raises:
        ValueError: If birthdate format is invalid
    """
    try:
        birth_date = datetime.fromisoformat(birthdate).date()
    except ValueError:
        raise ValueError(f"Invalid birthdate format: {birthdate}. Expected YYYY-MM-DD")

    today = date.today()
    age = today.year - birth_date.year

    # Adjust if birthday hasn't occurred this year yet
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1

    return age


def validate_birthdate(birthdate: str, min_age: int = 5, max_age: int = 18) -> Tuple[bool, Optional[str]]:
    """
    Validate birthdate for K-12 student profile

    Args:
        birthdate: ISO 8601 date string
        min_age: Minimum age (default 5 for kindergarten)
        max_age: Maximum age (default 18 for K-12)

    Returns:
        (is_valid, error_message)
    """
    try:
        age = calculate_age_from_birthdate(birthdate)
    except ValueError as e:
        return False, str(e)

    # Check if age is within K-12 range
    if age < min_age:
        return False, f"Student must be at least {min_age} years old (currently {age})"

    if age > max_age:
        return False, f"Student must be {max_age} or younger (currently {age})"

    # Check if birthdate is not in the future
    try:
        birth_date = datetime.fromisoformat(birthdate).date()
        if birth_date > date.today():
            return False, "Birthdate cannot be in the future"
    except ValueError:
        return False, "Invalid birthdate format"

    return True, None


def check_coppa_compliance(
    age: int,
    has_parental_consent: bool,
    parental_consent_date: Optional[str] = None
) -> AgeVerificationResult:
    """
    Check if profile meets COPPA compliance requirements

    Args:
        age: Student's age in years
        has_parental_consent: Whether parental consent was obtained
        parental_consent_date: When consent was obtained (ISO 8601 timestamp)

    Returns:
        AgeVerificationResult with compliance status
    """
    is_under_13 = age < COPPA_AGE_THRESHOLD
    requires_consent = is_under_13

    # Determine compliance
    if not is_under_13:
        # Age 13+ doesn't require parental consent under COPPA
        is_compliant = True
    else:
        # Under 13 requires verifiable parental consent
        is_compliant = has_parental_consent

    error_msg = None
    if not is_compliant:
        error_msg = (
            f"Student is {age} years old and requires verifiable parental consent per COPPA regulations. "
            "Please complete the parental consent process before creating this profile."
        )

    return AgeVerificationResult(
        age=age,
        is_under_13=is_under_13,
        requires_parental_consent=requires_consent,
        has_parental_consent=has_parental_consent,
        is_compliant=is_compliant,
        verification_date=datetime.now(timezone.utc).isoformat(),
        error_message=error_msg
    )


def generate_consent_verification_token(parent_id: str, profile_id: str) -> Tuple[str, str]:
    """
    Generate a verification token for parental consent via email

    Args:
        parent_id: Parent user ID
        profile_id: Child profile ID

    Returns:
        (token, token_hash) - Token to send to parent, hash to store in DB
    """
    # Generate secure random token
    token = secrets.token_urlsafe(32)

    # Hash token for database storage (no salt needed - token is already
    # cryptographically random; matches verification in parental_consent.py
    # and the pattern used in core/authentication.py for email tokens)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    return token, token_hash


def verify_consent_token(token: str, token_hash: str, parent_id: str, profile_id: str) -> bool:
    """
    Verify a parental consent token

    Args:
        token: Token provided by parent (from email link)
        token_hash: Stored hash from database
        parent_id: Parent user ID
        profile_id: Child profile ID

    Returns:
        True if token is valid
    """
    # Hash the token the same way generate_consent_verification_token does:
    # plain SHA-256 of the token (the token itself is cryptographically random,
    # so no salt is needed). Use constant-time comparison to prevent timing attacks.
    computed_hash = hashlib.sha256(token.encode()).hexdigest()

    return hmac.compare_digest(computed_hash, token_hash)


class AgeVerificationManager:
    """
    Manages age verification and COPPA compliance for child profiles
    """

    def __init__(self, db_manager):
        self.db = db_manager

    def verify_age_from_birthdate(
        self,
        birthdate: str,
        has_parental_consent: bool = False,
        parental_consent_date: Optional[str] = None
    ) -> AgeVerificationResult:
        """
        Verify age and COPPA compliance from birthdate

        Args:
            birthdate: ISO 8601 date string (YYYY-MM-DD)
            has_parental_consent: Whether parental consent was obtained
            parental_consent_date: When consent was obtained

        Returns:
            AgeVerificationResult

        Raises:
            AgeVerificationError: If birthdate is invalid or age is out of range
        """
        # Validate birthdate
        is_valid, error_msg = validate_birthdate(birthdate)
        if not is_valid:
            raise AgeVerificationError(error_msg)

        # Calculate age
        age = calculate_age_from_birthdate(birthdate)

        # Check COPPA compliance
        result = check_coppa_compliance(age, has_parental_consent, parental_consent_date)

        if not result.is_compliant:
            logger.warning(f"COPPA compliance check failed: {result.error_message}")

        return result

    def log_parental_consent(
        self,
        profile_id: str,
        parent_id: str,
        consent_method: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        electronic_signature: Optional[str] = None,
        verification_token: Optional[str] = None
    ) -> str:
        """
        Log parental consent to audit trail

        Args:
            profile_id: Child profile ID
            parent_id: Parent user ID
            consent_method: Method of consent verification
            ip_address: IP address of consent action
            user_agent: User agent of consent action
            electronic_signature: Parent's typed name or signature
            verification_token: Email verification token if applicable

        Returns:
            consent_id: ID of consent log entry
        """
        import uuid

        consent_id = uuid.uuid4().hex
        consent_date = datetime.now(timezone.utc).isoformat()

        try:
            self.db.execute_write(
                """
                INSERT INTO parental_consent_log
                (consent_id, profile_id, parent_id, consent_type, consent_method,
                 consent_date, ip_address, user_agent, electronic_signature,
                 verification_token, verified_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consent_id,
                    profile_id,
                    parent_id,
                    'initial',
                    consent_method,
                    consent_date,
                    ip_address,
                    user_agent,
                    electronic_signature,
                    verification_token,
                    consent_date,  # verified_at = consent_date for immediate verification
                    1  # is_active
                )
            )

            logger.info(f"Parental consent logged: {sanitize_log_value(consent_id)!r} for profile {sanitize_log_value(profile_id)!r}")
            return consent_id

        except DB_ERRORS as e:
            logger.error(f"Failed to log parental consent: {e}")
            raise

    def update_profile_consent_status(
        self,
        profile_id: str,
        consent_given: bool,
        consent_date: str,
        consent_method: str
    ) -> bool:
        """
        Update child profile with parental consent status

        Args:
            profile_id: Child profile ID
            consent_given: Whether consent was given
            consent_date: When consent was obtained
            consent_method: Method of consent verification

        Returns:
            True if update successful
        """
        try:
            self.db.execute_write(
                """
                UPDATE child_profiles
                SET parental_consent_given = ?,
                    parental_consent_date = ?,
                    parental_consent_method = ?,
                    coppa_verified = ?,
                    age_verified_at = ?
                WHERE profile_id = ?
                """,
                (
                    1 if consent_given else 0,
                    consent_date,
                    consent_method,
                    1 if consent_given else 0,
                    datetime.now(timezone.utc).isoformat(),
                    profile_id
                )
            )

            logger.info(f"Profile {profile_id} consent status updated: {consent_given}")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to update profile consent status: {e}")
            return False

    def revoke_parental_consent(
        self,
        profile_id: str,
        parent_id: str,
        reason: Optional[str] = None
    ) -> bool:
        """
        Revoke parental consent for a profile

        This will:
        1. Mark consent as inactive in consent log
        2. Update profile to require new consent
        3. Deactivate profile until new consent is obtained

        Args:
            profile_id: Child profile ID
            parent_id: Parent user ID
            reason: Optional reason for revocation

        Returns:
            True if revocation successful
        """
        import uuid

        try:
            # Log revocation
            consent_id = uuid.uuid4().hex
            revocation_date = datetime.now(timezone.utc).isoformat()

            # Step 1: Deactivate all prior active consent records for this profile
            self.db.execute_write(
                """
                UPDATE parental_consent_log
                SET is_active = 0
                WHERE profile_id = ? AND parent_id = ? AND is_active = 1
                """,
                (profile_id, parent_id)
            )

            # Step 2: Insert revocation record (is_active=0 since consent is revoked)
            self.db.execute_write(
                """
                INSERT INTO parental_consent_log
                (consent_id, profile_id, parent_id, consent_type, consent_method,
                 consent_date, notes, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consent_id,
                    profile_id,
                    parent_id,
                    'revoked',
                    'manual_revocation',
                    revocation_date,
                    reason or "Parental consent revoked",
                    0
                )
            )

            # Update profile
            self.db.execute_write(
                """
                UPDATE child_profiles
                SET parental_consent_given = 0,
                    coppa_verified = 0,
                    is_active = 0
                WHERE profile_id = ?
                """,
                (profile_id,)
            )

            logger.warning(f"Parental consent revoked for profile {sanitize_log_value(profile_id)!r}: {sanitize_log_value(reason)!r}")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to revoke parental consent: {e}")
            return False

    def get_consent_status(self, profile_id: str) -> dict:
        """
        Get current parental consent status for a profile

        Args:
            profile_id: Child profile ID

        Returns:
            Dict with consent status information
        """
        try:
            rows = self.db.execute_query(
                """
                SELECT parental_consent_given, parental_consent_date,
                       parental_consent_method, coppa_verified, age, birthdate
                FROM child_profiles
                WHERE profile_id = ?
                """,
                (profile_id,)
            )

            if not rows:
                return {"error": "Profile not found"}

            row = rows[0]
            # Support both dict and tuple row types
            if isinstance(row, dict):
                consent_given = row.get('parental_consent_given')
                consent_date = row.get('parental_consent_date')
                consent_method = row.get('parental_consent_method')
                coppa_verified = row.get('coppa_verified')
                age = row.get('age')
                birthdate = row.get('birthdate')
            else:
                consent_given = row[0]
                consent_date = row[1]
                consent_method = row[2]
                coppa_verified = row[3]
                age = row[4]
                birthdate = row[5]

            return {
                "profile_id": profile_id,
                "consent_given": bool(consent_given),
                "consent_date": consent_date,
                "consent_method": consent_method,
                "coppa_verified": bool(coppa_verified),
                "age": age,
                "birthdate": birthdate,
                "requires_consent": age < COPPA_AGE_THRESHOLD if age else False
            }

        except DB_ERRORS as e:
            logger.error(f"Failed to get consent status: {e}")
            return {"error": "Failed to retrieve consent status"}
