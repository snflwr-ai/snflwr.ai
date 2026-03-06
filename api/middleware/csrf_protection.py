"""
CSRF Protection Middleware for FastAPI

Implements double-submit cookie pattern for CSRF protection.
Protects all state-changing operations (POST, PUT, DELETE, PATCH).
"""

import secrets
import hashlib
import hmac
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.responses import Response

from utils.logger import get_logger
from config import SECURITY_CONFIG

logger = get_logger(__name__)

# CSRF configuration
CSRF_TOKEN_LENGTH = 32
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"

# Secret key for HMAC (should be from config in production)
CSRF_SECRET = SECURITY_CONFIG.get("csrf_secret", secrets.token_hex(32))


def generate_csrf_token() -> str:
    """
    Generate a cryptographically secure CSRF token

    Returns:
        32-byte hex token
    """
    return secrets.token_hex(CSRF_TOKEN_LENGTH)


def sign_csrf_token(token: str) -> str:
    """
    Sign CSRF token with HMAC for additional security

    Args:
        token: Raw CSRF token

    Returns:
        Signed token (token.signature)
    """
    signature = hmac.new(
        CSRF_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return f"{token}.{signature}"


def verify_csrf_token(signed_token: str) -> bool:
    """
    Verify CSRF token signature

    Args:
        signed_token: Token with signature (token.signature)

    Returns:
        True if valid, False otherwise
    """
    try:
        if "." not in signed_token:
            return False

        token, signature = signed_token.rsplit(".", 1)

        expected_signature = hmac.new(
            CSRF_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(signature, expected_signature)

    except Exception as e:  # Intentional catch-all: CSRF validation must fail safely
        logger.warning(f"CSRF token verification failed: {e}")
        return False


async def extract_csrf_token_from_request(request: Request) -> Optional[str]:
    """
    Extract CSRF token from request (header or form field)

    Args:
        request: FastAPI request object

    Returns:
        CSRF token if found, None otherwise
    """
    # Check header first (preferred for AJAX requests)
    token = request.headers.get(CSRF_HEADER_NAME)

    # Check form data (for traditional form submissions)
    # Note: request.form() is async in FastAPI
    if not token:
        try:
            form_data = await request.form()
            token = form_data.get(CSRF_FORM_FIELD)
        except (
            Exception
        ) as e:  # Intentional catch-all: CSRF validation must fail safely
            # Form parsing may fail for non-form content types - this is expected
            logger.debug(f"Could not parse form data for CSRF token: {e}")

    return token


async def validate_csrf_token(request: Request) -> bool:
    """
    Validate CSRF token for state-changing requests

    Args:
        request: FastAPI request object

    Returns:
        True if valid or not required, False if invalid

    Raises:
        HTTPException: If CSRF validation fails
    """
    # Only validate state-changing methods
    if request.method not in ["POST", "PUT", "DELETE", "PATCH"]:
        return True

    # Exempt certain paths (login, public API endpoints, internal calls)
    exempt_paths = [
        "/api/auth/login",  # Login creates token
        "/api/auth/register",  # Registration creates token
        "/api/auth/verify-email",  # Email verification link (no CSRF cookie)
        "/api/auth/forgot-password",  # Password reset request
        "/api/auth/reset-password",  # Password reset from email link
        "/api/admin/login",  # Admin login creates token (Open WebUI auth bridge)
        "/api/system/setup",  # First-time setup wizard (no session exists yet)
        "/api/parental-consent/verify",  # Parental consent from email link
        # /api/chat/send removed — protected by Bearer token auth, no CSRF exemption needed
        "/api/chat/send",  # Server-to-server from Open WebUI middleware (Bearer token auth, no CSRF cookie)
        "/api/internal/",  # Internal server-to-server endpoints
        "/api/thin-client/",  # Thin client API (non-browser clients, no CSRF cookies)
        "/docs",  # Swagger docs
        "/openapi.json",  # OpenAPI spec
        "/health",  # Health check
    ]

    if any(request.url.path.startswith(path) for path in exempt_paths):
        return True

    # Get token from cookie
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)

    if not cookie_token:
        logger.warning(
            f"CSRF validation failed: No cookie token for {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing in cookie"
        )

    # Get token from request (header or form)
    request_token = await extract_csrf_token_from_request(request)

    if not request_token:
        logger.warning(
            f"CSRF validation failed: No request token for {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing in request",
        )

    # Verify cookie token signature
    if not verify_csrf_token(cookie_token):
        logger.warning(f"CSRF validation failed: Invalid cookie token signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token signature"
        )

    # Verify request token signature
    if not verify_csrf_token(request_token):
        logger.warning(f"CSRF validation failed: Invalid request token signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token signature"
        )

    # Double-submit cookie validation: tokens must match
    if not hmac.compare_digest(cookie_token, request_token):
        logger.warning(f"CSRF validation failed: Token mismatch for {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch"
        )

    logger.debug(f"CSRF validation successful for {request.url.path}")
    return True


def set_csrf_cookie(response: Response, token: Optional[str] = None) -> str:
    """
    Set CSRF token in response cookie

    Args:
        response: FastAPI response object
        token: Optional existing token (generates new if None)

    Returns:
        The CSRF token (signed)
    """
    if not token:
        token = generate_csrf_token()

    signed_token = sign_csrf_token(token)

    # Set secure cookie with SameSite=Strict
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=signed_token,
        httponly=False,  # Must be accessible to JavaScript
        secure=SECURITY_CONFIG.get(
            "csrf_cookie_secure", True
        ),  # False for http://localhost dev
        samesite=SECURITY_CONFIG.get("csrf_cookie_samesite", "strict"),
        max_age=86400,  # 24 hours
        path="/",
    )

    return signed_token


def get_csrf_token_for_template(request: Request) -> str:
    """
    Get CSRF token for template rendering

    Args:
        request: FastAPI request object

    Returns:
        CSRF token for embedding in forms/AJAX
    """
    token = request.cookies.get(CSRF_COOKIE_NAME)

    if not token or not verify_csrf_token(token):
        # Generate new token if missing or invalid
        raw_token = generate_csrf_token()
        token = sign_csrf_token(raw_token)

    return token
