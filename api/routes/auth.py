"""
Authentication API Routes
Parent/admin authentication
"""

from fastapi import APIRouter, HTTPException, Depends, Response, Request
from pydantic import BaseModel

from core.authentication import (
    auth_manager,
    AuthSession,
    AuthenticationError,
    InvalidCredentialsError,
    AccountLockedError,
)
from core.email_service import email_service
from core.email_crypto import get_email_crypto
from storage.db_adapters import DB_ERRORS
from api.middleware.auth import get_current_session, audit_log
from api.middleware.csrf_protection import set_csrf_cookie
from utils.rate_limiter import RateLimiter
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Initialize rate limiter
rate_limiter = RateLimiter()


def check_auth_rate_limit(request: Request):
    """
    Rate limiting dependency for auth endpoints

    Limits:
    - 5 requests per minute per IP for login/register
    - 3 requests per minute for password reset
    """
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"

    # Check rate limit (5 requests per minute)
    allowed, info = rate_limiter.check_rate_limit(
        identifier=client_ip, max_requests=5, window_seconds=60, limit_type="auth"
    )

    if not allowed:
        logger.warning(f"Rate limit exceeded for IP {client_ip}: {info}")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Retry after {info.get('retry_after', 60)} seconds.",
            headers={"Retry-After": str(info.get("retry_after", 60))},
        )

    return info


class LoginRequest(BaseModel):
    """Login request"""

    email: str
    password: str


class RegisterRequest(BaseModel):
    """Registration request"""

    email: str
    password: str
    verify_password: str


class VerifyEmailRequest(BaseModel):
    """Email verification request"""

    token: str


class ForgotPasswordRequest(BaseModel):
    """Forgot password request"""

    email: str


class ResetPasswordRequest(BaseModel):
    """Reset password request"""

    token: str
    new_password: str
    verify_password: str


@router.post("/login")
def login(
    request: LoginRequest,
    response: Response,
    rate_limit_info: dict = Depends(check_auth_rate_limit),
):
    """Parent/admin login"""
    try:
        success, result = auth_manager.authenticate_parent(
            request.email, request.password
        )

        if not success:
            raise HTTPException(status_code=401, detail=result)

        # result is a session dict with parent_id, session_token, expires_at
        session_data = result

        # Set CSRF token cookie for subsequent requests
        csrf_token = set_csrf_cookie(response)

        return {
            "session": session_data,
            "token": session_data["session_token"],
            "csrf_token": csrf_token,  # Return token for AJAX requests
        }

    except HTTPException:
        raise
    except AccountLockedError as e:
        logger.warning(f"Account locked during login: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error during login: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during login: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/register")
def register(
    request: RegisterRequest,
    response: Response,
    rate_limit_info: dict = Depends(check_auth_rate_limit),
):
    """Register new parent account"""
    try:
        # Verify passwords match (auth_manager doesn't handle this)
        if request.password != request.verify_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")

        success, result = auth_manager.create_parent_account(
            username=request.email,
            password=request.password,
            email=request.email,
        )

        if not success:
            raise HTTPException(status_code=400, detail=result)

        user_id = result

        # Set CSRF token cookie for new account
        csrf_token = set_csrf_cookie(response)

        return {
            "status": "success",
            "user_id": user_id,
            "csrf_token": csrf_token,
            "message": "Account created successfully! Please check your email to verify your account.",
        }

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error during registration: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during registration: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logout")
def logout(session: AuthSession = Depends(get_current_session)):
    """
    Logout current session

    [LOCKED] SECURED: Requires authentication - users can only logout their own session
    """
    try:
        # Logout the authenticated session
        success = auth_manager.logout(session.session_token)
        if not success:
            raise HTTPException(status_code=400, detail="Logout failed")

        # Audit log
        audit_log("logout", "session", session.session_token, session)

        return {"status": "success"}

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error during logout: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during logout: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/validate/{session_id}")
def validate_session(
    session_id: str, current_session: AuthSession = Depends(get_current_session)
):
    """
    Validate session token.

    Requires authentication to prevent unauthenticated enumeration of session IDs
    and leaking of user data. Only the session owner or an admin can validate a session.
    """
    try:
        is_valid, session = auth_manager.validate_session(session_id)

        if not is_valid:
            raise HTTPException(status_code=401, detail="Invalid or expired session")

        # Only allow validating your own session (or admin)
        if (
            current_session.role != "admin"
            and current_session.user_id != session.user_id
        ):
            raise HTTPException(
                status_code=403, detail="Not authorized to validate this session"
            )

        # Return limited session info — don't expose internal fields
        return {
            "valid": True,
            "session": {
                "session_id": session.session_token,
                "role": session.role,
                "expires_at": getattr(session, "expires_at", None),
            },
        }

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error during session validation: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during session validation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/verify-email")
def verify_email(request: VerifyEmailRequest):
    """
    Verify email address with token

    This endpoint is called when a user clicks the verification link in their email.
    It marks the user's email as verified in the database.
    """
    try:
        success, user_id, error = auth_manager.verify_email_token(request.token)

        if not success:
            raise HTTPException(
                status_code=400, detail=error or "Invalid or expired verification token"
            )

        logger.info(f"Email verified successfully for user: {user_id}")

        return {
            "status": "success",
            "message": "Email verified successfully! You can now log in.",
        }

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error during email verification: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during email verification: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/forgot-password")
def forgot_password(
    request: ForgotPasswordRequest,
    rate_limit_info: dict = Depends(check_auth_rate_limit),
):
    """
    Request password reset

    Generates a password reset token and sends it to the user's email.
    For security, always returns success even if the email doesn't exist.
    """
    try:
        # Generate reset token
        success, token, error = auth_manager.generate_password_reset_token(
            request.email
        )

        if not success and error:
            # Log error but don't expose it to user
            logger.error(f"Password reset token generation failed: {error}")

        # If token was generated successfully, send email
        if success and token:
            # Look up user info from the accounts table (which has email_hash)
            email_crypto = get_email_crypto()
            email_hash = email_crypto.hash_email(request.email)

            from storage.database import db_manager

            user_result = db_manager.execute_read(
                "SELECT parent_id, name FROM accounts WHERE email_hash = ?",
                (email_hash,),
            )

            if user_result and len(user_result) > 0:
                row = user_result[0]
                # Safely extract values whether row is dict or tuple
                if isinstance(row, dict):
                    user_id = row.get("parent_id")
                    user_name = row.get("name")
                elif row and len(row) >= 2:
                    user_id = row[0]
                    user_name = row[1]
                else:
                    user_id = None
                    user_name = None

                if not user_id:
                    # User not found, but don't reveal this for security
                    return {
                        "message": "If your email is registered, you will receive a password reset link"
                    }

                # Send password reset email
                email_success, email_error = email_service.send_password_reset_email(
                    user_id=user_id,
                    user_email=request.email,
                    user_name=user_name or "User",
                    reset_token=token,
                )

                if not email_success:
                    logger.error(f"Failed to send password reset email: {email_error}")

        # Always return success for security (don't reveal if email exists)
        return {
            "status": "success",
            "message": "If an account exists with that email, a password reset link has been sent.",
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error during password reset request: {e}")
        # Don't expose error details for security
        return {
            "status": "success",
            "message": "If an account exists with that email, a password reset link has been sent.",
        }
    except Exception as e:
        logger.exception(f"Unexpected error during password reset request: {e}")
        # Don't expose error details for security
        return {
            "status": "success",
            "message": "If an account exists with that email, a password reset link has been sent.",
        }


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    rate_limit_info: dict = Depends(check_auth_rate_limit),
):
    """
    Reset password with token

    Validates the reset token and updates the user's password.
    Logs the user out of all sessions for security.
    """
    try:
        # Validate passwords match
        if request.new_password != request.verify_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")

        # Reset password using token
        success, error = auth_manager.reset_password_with_token(
            token=request.token, new_password=request.new_password
        )

        if not success:
            raise HTTPException(
                status_code=400, detail=error or "Invalid or expired reset token"
            )

        logger.info("Password reset successfully")

        return {
            "status": "success",
            "message": "Password reset successfully. Please log in with your new password.",
        }

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DB_ERRORS as e:
        logger.error(f"Database error during password reset: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during password reset: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
