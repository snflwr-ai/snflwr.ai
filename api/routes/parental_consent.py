"""
Parental Consent API Routes - COPPA Compliance

Implements verifiable parental consent workflow per COPPA regulations.

Flow:
1. Parent creates child profile (under 13)
2. System sends verification email
3. Parent clicks link → completes consent form
4. System logs consent → activates profile

Methods supported:
- Email + electronic signature
"""

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta, timezone
import secrets
import hashlib

from config import system_config
from core.authentication import auth_manager, AuthSession
from core.age_verification import AgeVerificationManager, AgeVerificationError, generate_consent_verification_token
from core.email_service import email_service
from storage.db_adapters import DB_ERRORS
from api.middleware.auth import get_current_session, audit_log
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


class ConsentRequest(BaseModel):
    """Request to initiate parental consent process"""
    profile_id: str
    parent_email: EmailStr
    child_name: str
    child_age: int


class ConsentVerification(BaseModel):
    """Verify parental consent via token"""
    token: str
    electronic_signature: str  # Parent's full name typed
    accept_terms: bool


class ConsentRevocation(BaseModel):
    """Revoke previously given consent"""
    profile_id: str
    reason: Optional[str] = None


@router.post("/request")
async def request_parental_consent(
    request: Request,
    request_data: ConsentRequest,
    auth_session: AuthSession = Depends(get_current_session)
):
    """
    Initiate parental consent process for under-13 child

    Sends verification email to parent with consent form link

    [LOCKED] SECURED: Parents can only request consent for their own children
    """
    try:
        age_manager = AgeVerificationManager(auth_manager.db)

        # Verify this is the child's parent
        profile_rows = auth_manager.db.execute_query(
            "SELECT parent_id, age FROM child_profiles WHERE profile_id = ?",
            (request_data.profile_id,)
        )

        if not profile_rows:
            raise HTTPException(status_code=404, detail="Profile not found")

        row = profile_rows[0]
        parent_id = row['parent_id'] if isinstance(row, dict) else row[0]
        child_age = row['age'] if isinstance(row, dict) else row[1]

        if parent_id != auth_session.user_id:
            logger.warning(f"Access denied: {auth_session.user_id} tried to request consent for profile {request_data.profile_id}")
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only request consent for your own children"
            )

        # Verify child is under 13
        if child_age >= 13:
            raise HTTPException(
                status_code=400,
                detail=f"Parental consent not required for children age {child_age}"
            )

        # Generate verification token
        token, token_hash = generate_consent_verification_token(
            parent_id=auth_session.user_id,
            profile_id=request_data.profile_id
        )

        # Store token in database (expires in 7 days)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        auth_manager.db.execute_write(
            """
            INSERT INTO auth_tokens
            (token_id, user_id, token_type, token_hash, created_at, expires_at, is_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                secrets.token_urlsafe(16),
                auth_session.user_id,
                'email_verification',
                token_hash,
                datetime.now(timezone.utc).isoformat(),
                expires_at,
                1
            )
        )

        # Send verification email
        consent_url = f"{system_config.BASE_URL}/api/parental-consent/verify?token={token}&profile_id={request_data.profile_id}"

        # Look up parent's display name from the database
        parent_name = auth_session.user_id  # fallback
        try:
            from storage.database import db_manager
            rows = db_manager.execute_query(
                "SELECT username FROM accounts WHERE parent_id = ?",
                (auth_session.user_id,)
            )
            if rows:
                try:
                    username = rows[0]['username']
                    if username:
                        parent_name = username
                except (KeyError, IndexError, TypeError):
                    pass
        except DB_ERRORS as name_err:
            logger.warning(f"Database error looking up parent name: {name_err}")
        except Exception as name_err:
            logger.warning(f"Could not look up parent name: {name_err}")

        email_sent = await email_service.send_parental_consent_request(
            to_email=request_data.parent_email,
            parent_name=parent_name,
            child_name=request_data.child_name,
            child_age=request_data.child_age,
            consent_url=consent_url
        )

        if not email_sent:
            raise HTTPException(
                status_code=500,
                detail="Failed to send verification email. Please try again."
            )

        # Audit log
        audit_log('request', 'parental_consent', request_data.profile_id, auth_session)

        logger.info(f"Parental consent requested for profile {request_data.profile_id}")

        return {
            "status": "success",
            "message": "Verification email sent to the registered parent email address",
            "expires_at": expires_at
        }

    except HTTPException:
        raise
    except AgeVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error requesting parental consent: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error requesting parental consent: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/verify")
async def verify_parental_consent(
    verification: ConsentVerification,
    profile_id: str,
    request: Request
):
    """
    Verify parental consent via email token

    This endpoint is called when parent clicks the link in verification email
    and completes the consent form

    [UNLOCKED] PUBLIC: Accessible via token (no auth required)
    """
    try:
        age_manager = AgeVerificationManager(auth_manager.db)

        # Verify token exists and is valid
        # Hash token for secure database lookup
        token_hash = hashlib.sha256(verification.token.encode()).hexdigest()

        token_rows = auth_manager.db.execute_query(
            """
            SELECT user_id, expires_at, is_valid
            FROM auth_tokens
            WHERE token_hash = ? AND token_type = 'email_verification'
            """,
            (token_hash,)
        )

        if not token_rows:
            raise HTTPException(status_code=400, detail="Invalid or expired verification token")

        row = token_rows[0]
        user_id = row['user_id'] if isinstance(row, dict) else row[0]
        expires_at = row['expires_at'] if isinstance(row, dict) else row[1]
        is_valid = row['is_valid'] if isinstance(row, dict) else row[2]

        if not is_valid:
            raise HTTPException(status_code=400, detail="Token has already been used")

        if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Verification token has expired")

        # Verify terms acceptance
        if not verification.accept_terms:
            raise HTTPException(
                status_code=400,
                detail="You must accept the terms and conditions to provide consent"
            )

        # Verify electronic signature is not empty
        if not verification.electronic_signature or len(verification.electronic_signature.strip()) < 2:
            raise HTTPException(
                status_code=400,
                detail="Electronic signature (your full name) is required"
            )

        # Verify the profile belongs to the parent associated with this token
        profile_rows = auth_manager.db.execute_query(
            "SELECT parent_id FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )
        if not profile_rows:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile_parent = profile_rows[0]['parent_id'] if isinstance(profile_rows[0], dict) else profile_rows[0][0]
        if profile_parent != user_id:
            logger.warning(
                "Consent verification: token user_id %s does not own profile %s (owner: %s)",
                user_id, profile_id, profile_parent,
            )
            raise HTTPException(status_code=403, detail="Token does not match profile owner")

        # Get client IP and user agent for audit trail
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Log parental consent
        consent_id = age_manager.log_parental_consent(
            profile_id=profile_id,
            parent_id=user_id,
            consent_method='email_verification',
            ip_address=client_ip,
            user_agent=user_agent,
            electronic_signature=verification.electronic_signature,
            verification_token=hashlib.sha256(verification.token.encode()).hexdigest()
        )

        # Update profile consent status
        consent_date = datetime.now(timezone.utc).isoformat()
        age_manager.update_profile_consent_status(
            profile_id=profile_id,
            consent_given=True,
            consent_date=consent_date,
            consent_method='email_verification'
        )

        # Mark token as used
        auth_manager.db.execute_write(
            """
            UPDATE auth_tokens
            SET is_valid = 0, used_at = ?
            WHERE token_hash = ?
            """,
            (datetime.now(timezone.utc).isoformat(), token_hash)
        )

        logger.info(f"Parental consent verified for profile {profile_id}, consent_id: {consent_id}")

        return {
            "status": "success",
            "message": "Parental consent successfully verified",
            "consent_id": consent_id,
            "profile_id": profile_id,
            "verified_at": consent_date
        }

    except HTTPException:
        raise
    except AgeVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error verifying parental consent: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error verifying parental consent: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/revoke")
async def revoke_parental_consent(
    revocation: ConsentRevocation,
    auth_session: AuthSession = Depends(get_current_session)
):
    """
    Revoke previously given parental consent

    This will deactivate the child profile until new consent is obtained

    [LOCKED] SECURED: Parents can only revoke consent for their own children
    """
    try:
        age_manager = AgeVerificationManager(auth_manager.db)

        # Verify parent owns this profile
        profile_rows = auth_manager.db.execute_query(
            "SELECT parent_id FROM child_profiles WHERE profile_id = ?",
            (revocation.profile_id,)
        )

        if not profile_rows:
            raise HTTPException(status_code=404, detail="Profile not found")

        row = profile_rows[0]
        parent_id = row['parent_id'] if isinstance(row, dict) else row[0]

        if parent_id != auth_session.user_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only revoke consent for your own children"
            )

        # Revoke consent
        success = age_manager.revoke_parental_consent(
            profile_id=revocation.profile_id,
            parent_id=auth_session.user_id,
            reason=revocation.reason
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to revoke consent")

        # Audit log
        audit_log('revoke', 'parental_consent', revocation.profile_id, auth_session)

        logger.warning(f"Parental consent revoked for profile {revocation.profile_id}")

        return {
            "status": "success",
            "message": "Parental consent has been revoked. Profile has been deactivated.",
            "profile_id": revocation.profile_id
        }

    except HTTPException:
        raise
    except AgeVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error revoking parental consent: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error revoking parental consent: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/status/{profile_id}")
async def get_consent_status(
    profile_id: str,
    auth_session: AuthSession = Depends(get_current_session)
):
    """
    Get current parental consent status for a profile

    [LOCKED] SECURED: Parents can only view status for their own children
    """
    try:
        age_manager = AgeVerificationManager(auth_manager.db)

        # Verify parent owns this profile
        profile_rows = auth_manager.db.execute_query(
            "SELECT parent_id FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )

        if not profile_rows:
            raise HTTPException(status_code=404, detail="Profile not found")

        row = profile_rows[0]
        parent_id = row['parent_id'] if isinstance(row, dict) else row[0]

        if parent_id != auth_session.user_id and auth_session.role != 'admin':
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only view consent status for your own children"
            )

        # Get consent status
        consent_status = age_manager.get_consent_status(profile_id)

        if "error" in consent_status:
            raise HTTPException(status_code=404, detail=consent_status["error"])

        # Get consent log history
        consent_history = auth_manager.db.execute_query(
            """
            SELECT consent_id, consent_type, consent_method, consent_date,
                   electronic_signature, is_active
            FROM parental_consent_log
            WHERE profile_id = ?
            ORDER BY consent_date DESC
            LIMIT 10
            """,
            (profile_id,)
        )

        return {
            "status": "success",
            "consent_status": consent_status,
            "consent_history": [
                {
                    "consent_id": row['consent_id'] if isinstance(row, dict) else row[0],
                    "consent_type": row['consent_type'] if isinstance(row, dict) else row[1],
                    "consent_method": row['consent_method'] if isinstance(row, dict) else row[2],
                    "consent_date": row['consent_date'] if isinstance(row, dict) else row[3],
                    "electronic_signature": row['electronic_signature'] if isinstance(row, dict) else row[4],
                    "is_active": bool(row['is_active'] if isinstance(row, dict) else row[5])
                }
                for row in consent_history
            ]
        }

    except HTTPException:
        raise
    except AgeVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving consent status: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving consent status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
