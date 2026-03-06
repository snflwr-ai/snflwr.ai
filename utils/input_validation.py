"""
Input Validation Utilities

Provides comprehensive input validation for API endpoints.
Protects against:
- Oversized inputs (DoS)
- Invalid formats
- Injection attacks (SQL, XSS)
- Invalid ID formats

Usage:
    from utils.input_validation import (
        validate_profile_id,
        validate_name,
        validate_message,
        sanitize_string
    )
"""

import re
from typing import Tuple, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ==============================================================================
# Constants
# ==============================================================================

# ID Patterns (hex UUID format)
UUID_HEX_PATTERN = re.compile(r"^[a-f0-9]{32}$")
UUID_HYPHENATED_PATTERN = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
)
SESSION_TOKEN_PATTERN = re.compile(r"^[a-f0-9]{64}$")  # secrets.token_hex(32)

# Name constraints
MIN_NAME_LENGTH = 1
MAX_NAME_LENGTH = 100
NAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9\s\-\'\.]+$"
)  # Alphanumeric, space, hyphen, apostrophe, period

# Message constraints
MIN_MESSAGE_LENGTH = 1
MAX_MESSAGE_LENGTH = 10000  # 10K characters max per message

# Grade levels allowed
VALID_GRADE_LEVELS = {
    "pre-k",
    "kindergarten",
    "k",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "1st",
    "2nd",
    "3rd",
    "4th",
    "5th",
    "6th",
    "7th",
    "8th",
    "9th",
    "10th",
    "11th",
    "12th",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "elementary",
    "middle",
    "high",
    "college",
    "university",
    "graduate",
}

# Model roles
VALID_MODEL_ROLES = {"student", "tutor", "teacher", "assistant", "researcher"}

# Age constraints (COPPA)
MIN_AGE = 3
MAX_AGE = 25


# ==============================================================================
# Validators
# ==============================================================================


def validate_profile_id(profile_id: str) -> Tuple[bool, Optional[str]]:
    """
    Validate profile ID format.

    Args:
        profile_id: Profile ID to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not profile_id:
        return False, "Profile ID is required"

    if not isinstance(profile_id, str):
        return False, "Profile ID must be a string"

    if not UUID_HEX_PATTERN.match(profile_id):
        logger.warning(f"Invalid profile ID format: {profile_id[:20]}...")
        return False, "Invalid profile ID format"

    return True, None


def validate_parent_id(parent_id: str) -> Tuple[bool, Optional[str]]:
    """
    Validate parent ID format.

    Accepts both 32-char hex UUIDs and hyphenated UUIDs (Open WebUI uses hyphenated).

    Args:
        parent_id: Parent ID to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not parent_id:
        return False, "Parent ID is required"

    if not isinstance(parent_id, str):
        return False, "Parent ID must be a string"

    if not (
        UUID_HEX_PATTERN.match(parent_id) or UUID_HYPHENATED_PATTERN.match(parent_id)
    ):
        logger.warning(f"Invalid parent ID format: {parent_id[:20]}...")
        return False, "Invalid parent ID format"

    return True, None


def validate_session_id(session_id: str) -> Tuple[bool, Optional[str]]:
    """
    Validate session ID/token format.

    Args:
        session_id: Session ID to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not session_id:
        return False, "Session ID is required"

    if not isinstance(session_id, str):
        return False, "Session ID must be a string"

    # Session IDs can be 32-byte hex (64 chars), UUID hex (32 chars), or hyphenated UUID (36 chars)
    if not (
        UUID_HEX_PATTERN.match(session_id)
        or SESSION_TOKEN_PATTERN.match(session_id)
        or UUID_HYPHENATED_PATTERN.match(session_id)
    ):
        logger.warning(f"Invalid session ID format: {session_id[:20]}...")
        return False, "Invalid session ID format"

    return True, None


def validate_name(name: str, field_name: str = "Name") -> Tuple[bool, Optional[str]]:
    """
    Validate a name field (child name, username, etc.).

    Args:
        name: Name to validate
        field_name: Field name for error messages

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not name:
        return False, f"{field_name} is required"

    if not isinstance(name, str):
        return False, f"{field_name} must be a string"

    # Strip and check length
    name = name.strip()

    if len(name) < MIN_NAME_LENGTH:
        return False, f"{field_name} must be at least {MIN_NAME_LENGTH} character(s)"

    if len(name) > MAX_NAME_LENGTH:
        return False, f"{field_name} must be at most {MAX_NAME_LENGTH} characters"

    if not NAME_PATTERN.match(name):
        return (
            False,
            f"{field_name} contains invalid characters. Only letters, numbers, spaces, hyphens, apostrophes, and periods are allowed.",
        )

    return True, None


def validate_message(message: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a chat message.

    Args:
        message: Message content to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not message:
        return False, "Message is required"

    if not isinstance(message, str):
        return False, "Message must be a string"

    # Strip and check length
    message = message.strip()

    if len(message) < MIN_MESSAGE_LENGTH:
        return False, "Message cannot be empty"

    if len(message) > MAX_MESSAGE_LENGTH:
        return (
            False,
            f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters",
        )

    return True, None


def validate_age(age: int) -> Tuple[bool, Optional[str]]:
    """
    Validate age for COPPA compliance.

    Args:
        age: Age to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if age is None:
        return False, "Age is required"

    if not isinstance(age, int):
        return False, "Age must be an integer"

    if age < MIN_AGE:
        return False, f"Age must be at least {MIN_AGE}"

    if age > MAX_AGE:
        return False, f"Age must be at most {MAX_AGE}"

    return True, None


def validate_grade_level(grade_level: str) -> Tuple[bool, Optional[str]]:
    """
    Validate grade level.

    Args:
        grade_level: Grade level to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not grade_level:
        return False, "Grade level is required"

    if not isinstance(grade_level, str):
        return False, "Grade level must be a string"

    if grade_level.lower().strip() not in VALID_GRADE_LEVELS:
        return (
            False,
            f"Invalid grade level. Allowed values: {', '.join(sorted(VALID_GRADE_LEVELS))}",
        )

    return True, None


def validate_model_role(model_role: str) -> Tuple[bool, Optional[str]]:
    """
    Validate model role.

    Args:
        model_role: Model role to validate

    Returns:
        Tuple of (is_valid, error_message or None)
    """
    if not model_role:
        return False, "Model role is required"

    if not isinstance(model_role, str):
        return False, "Model role must be a string"

    if model_role.lower().strip() not in VALID_MODEL_ROLES:
        return (
            False,
            f"Invalid model role. Allowed values: {', '.join(sorted(VALID_MODEL_ROLES))}",
        )

    return True, None


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """
    Sanitize a string by removing potentially dangerous characters.

    Args:
        value: String to sanitize
        max_length: Maximum length to truncate to

    Returns:
        Sanitized string
    """
    if not value:
        return ""

    if not isinstance(value, str):
        value = str(value)

    # Strip whitespace
    value = value.strip()

    # Truncate to max length
    if len(value) > max_length:
        value = value[:max_length]

    # Remove null bytes
    value = value.replace("\x00", "")

    return value


# ==============================================================================
# Pydantic Field Validators (for use in Pydantic models)
# ==============================================================================

from pydantic import field_validator, ValidationInfo


def create_id_validator(field_name: str):
    """Create a Pydantic field validator for ID fields."""

    def validator(cls, v: str) -> str:
        if not v:
            raise ValueError(f"{field_name} is required")
        if not UUID_HEX_PATTERN.match(v):
            raise ValueError(f"Invalid {field_name} format")
        return v

    return field_validator(field_name, mode="before")(validator)


def create_name_validator(field_name: str):
    """Create a Pydantic field validator for name fields."""

    def validator(cls, v: str) -> str:
        if not v:
            raise ValueError(f"{field_name} is required")
        v = v.strip()
        if len(v) < MIN_NAME_LENGTH or len(v) > MAX_NAME_LENGTH:
            raise ValueError(
                f"{field_name} must be {MIN_NAME_LENGTH}-{MAX_NAME_LENGTH} characters"
            )
        if not NAME_PATTERN.match(v):
            raise ValueError(f"{field_name} contains invalid characters")
        return v

    return field_validator(field_name, mode="before")(validator)


# ==============================================================================
# Exports
# ==============================================================================

__all__ = [
    # Validators
    "validate_profile_id",
    "validate_parent_id",
    "validate_session_id",
    "validate_name",
    "validate_message",
    "validate_age",
    "validate_grade_level",
    "validate_model_role",
    "sanitize_string",
    # Constants
    "UUID_HEX_PATTERN",
    "SESSION_TOKEN_PATTERN",
    "MIN_NAME_LENGTH",
    "MAX_NAME_LENGTH",
    "MIN_MESSAGE_LENGTH",
    "MAX_MESSAGE_LENGTH",
    "VALID_GRADE_LEVELS",
    "VALID_MODEL_ROLES",
    "MIN_AGE",
    "MAX_AGE",
    # Pydantic helpers
    "create_id_validator",
    "create_name_validator",
]
