"""
Profile Management API Routes
Child profile CRUD operations

[LOCKED] SECURED: All routes require authentication
- Parents can only access their own children's profiles
- Admins can access all profiles
"""

import re
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from core.profile_manager import (
    ProfileManager,
    ProfileError,
    ProfileValidationError,
    ProfileNotFoundError,
    PermissionDeniedError,
)
from utils.input_validation import (
    validate_parent_id,
    validate_name,
    validate_age,
    validate_grade_level,
    validate_model_role,
    UUID_HEX_PATTERN,
    MIN_NAME_LENGTH,
    MAX_NAME_LENGTH,
    VALID_GRADE_LEVELS,
    VALID_MODEL_ROLES,
    MIN_AGE,
    MAX_AGE,
)
from core.authentication import auth_manager, AuthSession
from core.age_verification import (
    AgeVerificationManager,
    calculate_age_from_birthdate,
    validate_birthdate,
    AgeVerificationError,
    ParentalConsentRequired,
)
from api.middleware.auth import (
    get_current_session,
    VerifyParentAccess,
    VerifyProfileAccess,
    audit_log,
)
from storage.conversation_store import conversation_store
from storage.db_adapters import DB_ERRORS
from safety.incident_logger import incident_logger
from utils.rate_limiter import RateLimiter
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()

# Initialize rate limiter
rate_limiter = RateLimiter()


def check_profile_rate_limit(request: Request):
    """Rate limit profile operations: 20 requests per 60 seconds per IP"""
    client_ip = request.client.host if request.client else "unknown"
    allowed, info = rate_limiter.check_rate_limit(
        identifier=client_ip, max_requests=20, window_seconds=60, limit_type="profile"
    )
    if not allowed:
        retry_after = info.get("retry_after", 60) if isinstance(info, dict) else 60
        raise HTTPException(
            status_code=429,
            detail="Too many profile requests. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )
    return info


class CreateProfileRequest(BaseModel):
    """Request to create child profile with validated fields"""

    parent_id: str = Field(..., min_length=32, max_length=36)
    name: str = Field(..., min_length=MIN_NAME_LENGTH, max_length=MAX_NAME_LENGTH)
    age: Optional[int] = Field(
        None, ge=MIN_AGE, le=MAX_AGE
    )  # Optional if birthdate provided
    birthdate: Optional[str] = (
        None  # ISO 8601 date (YYYY-MM-DD) - recommended for COPPA
    )
    grade_level: str = Field(..., min_length=1, max_length=20)
    model_role: str = Field("student", min_length=1, max_length=20)
    # Note: parental consent is verified server-side from the database,
    # not from client input. This field is accepted but ignored.
    parental_consent_verified: bool = False

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id_format(cls, v: str) -> str:
        is_valid, error = validate_parent_id(v)
        if not is_valid:
            raise ValueError(error)
        return v

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        is_valid, error = validate_name(v, "Name")
        if not is_valid:
            raise ValueError(error)
        return v.strip()

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level_value(cls, v: str) -> str:
        is_valid, error = validate_grade_level(v)
        if not is_valid:
            raise ValueError(error)
        return v.lower().strip()

    @field_validator("model_role")
    @classmethod
    def validate_model_role_value(cls, v: str) -> str:
        is_valid, error = validate_model_role(v)
        if not is_valid:
            raise ValueError(error)
        return v.lower().strip()


class UpdateProfileRequest(BaseModel):
    """Request to update child profile with validated fields"""

    name: Optional[str] = Field(
        None, min_length=MIN_NAME_LENGTH, max_length=MAX_NAME_LENGTH
    )
    age: Optional[int] = Field(None, ge=MIN_AGE, le=MAX_AGE)
    grade_level: Optional[str] = Field(None, min_length=1, max_length=20)
    model_role: Optional[str] = Field(None, min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        is_valid, error = validate_name(v, "Name")
        if not is_valid:
            raise ValueError(error)
        return v.strip()

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level_value(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        is_valid, error = validate_grade_level(v)
        if not is_valid:
            raise ValueError(error)
        return v.lower().strip()

    @field_validator("model_role")
    @classmethod
    def validate_model_role_value(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        is_valid, error = validate_model_role(v)
        if not is_valid:
            raise ValueError(error)
        return v.lower().strip()


@router.post("/", status_code=201)
def create_profile(
    request: CreateProfileRequest,
    session: AuthSession = Depends(get_current_session),
    rate_limit_info: dict = Depends(check_profile_rate_limit),
):
    """
    Create new child profile with age verification and COPPA compliance

    COPPA Requirements:
    - If child is under 13, parental consent is required
    - Birthdate preferred over age for accurate age calculation
    - Consent must be verified before profile activation

    [LOCKED] SECURED: Parent can only create profiles for themselves, admins can create for anyone
    """
    try:
        # Verify authorization: Parents can only create for themselves
        if session.role != "admin" and session.user_id != request.parent_id:
            logger.warning(
                f"Access denied: {sanitize_log_value(session.user_id)!r} tried to create profile for {sanitize_log_value(request.parent_id)!r}"
            )
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only create profiles for yourself",
            )

        # AGE VERIFICATION LOGIC
        age_manager = AgeVerificationManager(auth_manager.db)
        calculated_age = None
        birthdate_to_store = None

        # Determine age (from birthdate if provided, otherwise use age field)
        if request.birthdate:
            # Validate birthdate format and range
            is_valid, error_msg = validate_birthdate(request.birthdate)
            if not is_valid:
                raise HTTPException(
                    status_code=400, detail=f"Invalid birthdate: {error_msg}"
                )

            # Calculate age from birthdate
            calculated_age = calculate_age_from_birthdate(request.birthdate)
            birthdate_to_store = request.birthdate

            logger.info("Age calculated from birthdate")

        elif request.age:
            # Use provided age (less accurate, but allowed)
            calculated_age = request.age
            logger.warning(
                f"Profile created with age only (no birthdate) - less accurate for COPPA"
            )

        else:
            raise HTTPException(
                status_code=400, detail="Either 'age' or 'birthdate' must be provided"
            )

        # COPPA COMPLIANCE CHECK
        # Server-side verification: check parental consent from the database,
        # NEVER trust the client-supplied parental_consent_verified field.
        has_consent = False
        if calculated_age < 13:
            consent_rows = auth_manager.db.execute_query(
                """
                SELECT is_active FROM parental_consent_log
                WHERE parent_id = ? AND is_active = 1
                AND consent_type != 'revoked'
                ORDER BY consent_date DESC LIMIT 1
                """,
                (session.user_id,),
            )
            if consent_rows:
                row = consent_rows[0]
                has_consent = bool(
                    row["is_active"] if isinstance(row, dict) else row[0]
                )

        age_verification_result = age_manager.verify_age_from_birthdate(
            birthdate=request.birthdate
            or f"{datetime.now(timezone.utc).year - calculated_age}-01-01",
            has_parental_consent=has_consent,
        )

        if not age_verification_result.is_compliant:
            # Under-13 without parental consent
            logger.warning(
                f"COPPA compliance failure: Child is {age_verification_result.age}, "
                f"requires parental consent"
            )

            raise HTTPException(
                status_code=403,
                detail={
                    "error": "parental_consent_required",
                    "message": age_verification_result.error_message,
                    "age": age_verification_result.age,
                    "requires_consent": age_verification_result.requires_parental_consent,
                    "action_required": "complete_parental_consent_workflow",
                },
            )

        # CREATE PROFILE (COPPA-compliant)
        profile_manager = ProfileManager(auth_manager.db)
        profile = profile_manager.create_profile(
            parent_id=request.parent_id,
            name=request.name,
            age=calculated_age,
            grade=request.grade_level,
        )

        if not profile:
            raise HTTPException(status_code=400, detail="Failed to create profile")

        # UPDATE PROFILE WITH AGE VERIFICATION DATA
        if birthdate_to_store:
            auth_manager.db.execute_write(
                """
                UPDATE child_profiles
                SET birthdate = ?,
                    parental_consent_given = ?,
                    parental_consent_date = ?,
                    parental_consent_method = ?,
                    coppa_verified = ?,
                    age_verified_at = ?
                WHERE profile_id = ?
                """,
                (
                    birthdate_to_store,
                    1 if has_consent else 0,
                    datetime.now(timezone.utc).isoformat() if has_consent else None,
                    "db_verified" if has_consent else None,
                    1 if age_verification_result.is_compliant else 0,
                    datetime.now(timezone.utc).isoformat(),
                    profile.profile_id,
                ),
            )

        # Audit log
        audit_log("create", "profile", profile.profile_id, session)

        logger.info(
            f"Profile created: {profile.profile_id!r} by {session.user_id!r}, "
            f"coppa_compliant={age_verification_result.is_compliant!r}"
        )

        return {
            **profile.to_dict(),
            "age_verification": {
                "age": age_verification_result.age,
                "is_under_13": age_verification_result.is_under_13,
                "coppa_compliant": age_verification_result.is_compliant,
                "has_parental_consent": age_verification_result.has_parental_consent,
            },
        }

    except HTTPException:
        raise
    except AgeVerificationError as e:
        logger.error(f"Age verification failed: {e}")
        raise HTTPException(status_code=400, detail="Age verification failed")
    except ProfileValidationError as e:
        logger.warning(f"Profile validation failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid profile data")
    except PermissionDeniedError as e:
        logger.warning(f"Permission denied creating profile: {e}")
        raise HTTPException(status_code=403, detail="Access denied")
    except ProfileError as e:
        logger.warning(f"Profile error during creation: {e}")
        raise HTTPException(status_code=400, detail="Profile operation failed")
    except DB_ERRORS as e:
        logger.error(f"Database error creating profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error creating profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{profile_id}")
def get_profile(profile_id: str, session: AuthSession = Depends(VerifyProfileAccess)):
    """
    Get child profile by ID

    [LOCKED] SECURED: Parents can only view their own children's profiles, admins can view all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)
        profile = profile_manager.get_profile(profile_id)

        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Audit log
        audit_log("read", "profile", profile_id, session)

        return profile.to_dict()

    except HTTPException:
        raise
    except ProfileNotFoundError as e:
        logger.warning(f"Profile not found: {e}")
        raise HTTPException(status_code=404, detail="Profile not found")
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/parent/{parent_id}")
def get_profiles_for_parent(
    parent_id: str,
    include_inactive: bool = False,
    session: AuthSession = Depends(VerifyParentAccess),
):
    """
    Get all profiles for a parent

    [LOCKED] SECURED: Parents can only view their own children, admins can view all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)
        profiles = profile_manager.get_profiles_by_parent(parent_id)

        # Filter by active status if requested
        if not include_inactive:
            profiles = [p for p in profiles if p.is_active]

        # Audit log
        audit_log("read", "parent_profiles", parent_id, session)

        return {"profiles": [p.to_dict() for p in profiles], "count": len(profiles)}

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving profiles: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving profiles: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/{profile_id}")
def update_profile(
    profile_id: str,
    request: UpdateProfileRequest,
    session: AuthSession = Depends(VerifyProfileAccess),
    rate_limit_info: dict = Depends(check_profile_rate_limit),
):
    """
    Update child profile

    [LOCKED] SECURED: Parents can only update their own children's profiles, admins can update all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)

        # Build kwargs, filtering out None values
        update_fields: Dict[str, Any] = {}
        if request.name is not None:
            update_fields["name"] = request.name
        if request.age is not None:
            update_fields["age"] = request.age
        if request.grade_level is not None:
            update_fields["grade_level"] = request.grade_level
        if request.model_role is not None:
            update_fields["model_role"] = request.model_role

        profile_manager.update_profile(profile_id=profile_id, **update_fields)

        # Audit log
        audit_log("update", "profile", profile_id, session)

        # Return updated profile
        profile = profile_manager.get_profile(profile_id)
        return profile.to_dict()

    except HTTPException:
        raise
    except ProfileValidationError as e:
        logger.warning(f"Profile validation failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid profile data")
    except ProfileNotFoundError as e:
        logger.warning(f"Profile not found: {e}")
        raise HTTPException(status_code=404, detail="Profile not found")
    except PermissionDeniedError as e:
        logger.warning(f"Permission denied updating profile: {e}")
        raise HTTPException(status_code=403, detail="Access denied")
    except ProfileError as e:
        logger.warning(f"Profile error during update: {e}")
        raise HTTPException(status_code=400, detail="Profile operation failed")
    except DB_ERRORS as e:
        logger.error(f"Database error updating profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error updating profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{profile_id}")
def deactivate_profile(
    profile_id: str, session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Deactivate child profile (soft delete)

    [LOCKED] SECURED: Parents can only deactivate their own children's profiles, admins can deactivate all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)
        profile_manager.deactivate_profile(profile_id)

        # Audit log
        audit_log("delete", "profile", profile_id, session)

        return {"status": "success", "message": "Profile deactivated"}

    except HTTPException:
        raise
    except ProfileNotFoundError as e:
        logger.warning(f"Profile not found for deactivation: {e}")
        raise HTTPException(status_code=404, detail="Profile not found")
    except PermissionDeniedError as e:
        logger.warning(f"Permission denied deactivating profile: {e}")
        raise HTTPException(status_code=403, detail="Access denied")
    except ProfileError as e:
        logger.warning(f"Profile error during deactivation: {e}")
        raise HTTPException(status_code=400, detail="Profile operation failed")
    except DB_ERRORS as e:
        logger.error(f"Database error deactivating profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error deactivating profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{profile_id}/stats")
def get_profile_statistics(
    profile_id: str, session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Get usage statistics for a profile

    [LOCKED] SECURED: Parents can only view their own children's stats, admins can view all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)
        profile = profile_manager.get_profile(profile_id)

        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Build stats from profile data (total_sessions, total_questions are on the profile)
        stats = {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "total_sessions": profile.total_sessions,
            "total_questions": profile.total_questions,
            "last_active": profile.last_active,
            "is_active": profile.is_active,
        }

        # Audit log
        audit_log("read", "profile_stats", profile_id, session)

        return stats

    except HTTPException:
        raise
    except ProfileNotFoundError as e:
        logger.warning(f"Profile not found for stats: {e}")
        raise HTTPException(status_code=404, detail="Profile not found")
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving profile stats: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving profile stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{profile_id}/export")
def export_profile_data(
    profile_id: str, session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Export all child data in machine-readable JSON format

    **COPPA/FERPA Compliance: Right to Data Portability**

    Returns comprehensive export including:
    - Profile information
    - All conversation history with messages
    - Safety incidents
    - Usage statistics
    - Export metadata (date, format version)

    [LOCKED] SECURED: Parents can only export their own children's data, admins can export all
    """
    try:
        profile_manager = ProfileManager(auth_manager.db)

        # Get profile
        profile = profile_manager.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Get all conversations and messages
        conversations = conversation_store.get_profile_conversations(profile_id)
        conversations_data = []
        for conv in conversations:
            messages = conversation_store.get_conversation_messages(
                conv.conversation_id
            )
            conversations_data.append(
                {
                    "conversation_id": conv.conversation_id,
                    "subject_area": conv.subject_area,
                    "created_at": conv.created_at.isoformat(),
                    "updated_at": (
                        conv.updated_at.isoformat() if conv.updated_at else None
                    ),
                    "message_count": conv.message_count,
                    "messages": [msg.to_dict() for msg in messages],
                }
            )

        # Get safety incidents (use large days window to get all history)
        incidents_list = incident_logger.get_profile_incidents(profile_id, days=3650)
        incidents = [
            i.to_dict() if hasattr(i, "to_dict") else i for i in incidents_list
        ]

        # Get usage statistics from profile data
        stats = {
            "total_sessions": profile.total_sessions,
            "total_questions": profile.total_questions,
            "last_active": profile.last_active,
        }

        # Build comprehensive export
        export_data = {
            # Profile information
            "profile": profile.to_dict(),
            # Conversation history
            "conversations": conversations_data,
            "total_conversations": len(conversations_data),
            # Safety incidents
            "safety_incidents": incidents,
            "total_incidents": len(incidents),
            # Usage statistics
            "usage_statistics": stats,
            # Export metadata
            "export_metadata": {
                "export_date": datetime.now(timezone.utc).isoformat(),
                "export_format_version": "1.0",
                "exported_by": session.user_id,
                "data_types_included": [
                    "profile",
                    "conversations",
                    "safety_incidents",
                    "usage_statistics",
                ],
                "compliance": {
                    "coppa_compliant": True,
                    "ferpa_compliant": True,
                    "right_to_portability": True,
                },
            },
        }

        # Audit log
        audit_log("export", "profile_data", profile_id, session)

        logger.info(
            f"Data export completed for profile {sanitize_log_value(profile_id)!r} by {sanitize_log_value(session.user_id)!r}"
        )

        # Return as downloadable JSON file
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f"attachment; filename=child_data_{re.sub(r'[^a-zA-Z0-9_-]', '_', profile.name)}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
            },
        )

    except HTTPException:
        raise
    except ProfileNotFoundError as e:
        logger.warning(f"Profile not found for export: {e}")
        raise HTTPException(status_code=404, detail="Profile not found")
    except DB_ERRORS as e:
        logger.error(f"Database error exporting profile data: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error exporting profile data: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
