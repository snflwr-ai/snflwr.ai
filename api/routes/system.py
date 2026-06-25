"""Root, favicon, internal profile lookup and first-run setup endpoints.

Extracted verbatim from ``api/server.py`` (behavior-preserving refactor).
Registered on the app with no prefix, so the paths are unchanged:

    GET  /
    GET  /favicon.ico
    GET  /api/internal/profile-for-user/{user_id}
    GET  /api/system/setup-status
    POST /api/system/setup

The setup endpoints are rate limited by ``api.server.check_setup_rate_limit``.
That function (and its ``_setup_rate_limiter``) must stay in ``api.server`` so
existing tests can ``patch("api.server._setup_rate_limiter")`` and have it take
effect. To avoid an import cycle at module load (``api.server`` imports this
module to register the router), the dependency is resolved lazily at request
time via :func:`_check_setup_rate_limit`, which calls through to the function
living in ``api.server`` — so the patch contract is preserved.
"""

from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api import __version__
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()


def _check_setup_rate_limit(request: Request):
    """Lazy proxy to ``api.server.check_setup_rate_limit``.

    Deferring the import to call-time avoids a circular import (api.server
    imports this module) while still invoking the rate limiter that lives in
    ``api.server`` — so ``patch("api.server._setup_rate_limiter")`` continues
    to take effect against the setup endpoints.
    """
    from api.server import check_setup_rate_limit

    return check_setup_rate_limit(request)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(
        str(_Path(__file__).parent.parent / "static" / "admin" / "icon.png"),
        media_type="image/png",
    )


@router.get("/api/internal/profile-for-user/{user_id}")
async def get_profile_for_user(user_id: str, authorization: str = Header(None)):
    """
    Internal endpoint: look up the active child profile for an Open WebUI user.

    Called by the Snflwr middleware running inside the Open WebUI container.
    Requires the internal API key for authentication.

    Returns {"profile_id": "..."} or {"profile_id": "no_profile_<user_id>"}.
    """
    import hmac as _hmac

    from config import INTERNAL_API_KEY

    token = (
        authorization.split(" ", 1)[1]
        if authorization and " " in authorization
        else authorization
    )
    if not token or not _hmac.compare_digest(token, INTERNAL_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
    import re

    if not re.match(r"^[a-zA-Z0-9_-]{1,128}$", user_id):
        return {"profile_id": "default_profile"}

    try:
        from storage.database import db_manager

        # First check if this Open WebUI user has a direct student profile
        profiles = db_manager.execute_query(
            """
            SELECT profile_id FROM child_profiles
            WHERE owui_user_id = ? AND is_active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )

        if not profiles:
            # Fallback: check by parent_id (for parents whose child profiles apply)
            profiles = db_manager.execute_query(
                """
                SELECT profile_id FROM child_profiles
                WHERE parent_id = ? AND is_active = 1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )

        if profiles:
            pid = (
                profiles[0]["profile_id"]
                if isinstance(profiles[0], dict)
                else profiles[0][0]
            )
            return {"profile_id": str(pid)}

        return {"profile_id": f"no_profile_{user_id}"}

    except DB_ERRORS as e:
        logger.error(
            f"Database error looking up profile for user {sanitize_log_value(user_id)!r}: {e}"
        )
        return {"profile_id": f"no_profile_{user_id}"}
    except Exception as e:
        logger.exception(
            f"Unexpected error looking up profile for user {sanitize_log_value(user_id)!r}: {e}"
        )
        return {"profile_id": f"no_profile_{user_id}"}


@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "snflwr.ai API",
        "version": __version__,
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/system/setup-status")
async def setup_status(_rate=Depends(_check_setup_rate_limit)):
    """
    Check if the system has been set up (has at least one admin account).
    Used by the frontend to redirect to the setup wizard on first run.
    This endpoint requires no authentication.
    """
    try:
        from storage.database import db_manager

        parents = db_manager.execute_query("SELECT COUNT(*) as count FROM accounts")
        has_accounts = parents and parents[0]["count"] > 0
    except DB_ERRORS as e:
        logger.error(f"Database error checking setup status: {e}")
        has_accounts = False
    except Exception as e:
        logger.exception(f"Unexpected error checking setup status: {e}")
        has_accounts = False

    return {
        "initialized": has_accounts,
        "needs_setup": not has_accounts,
    }


class SetupRequest(BaseModel):
    """Web setup wizard request — creates the first parent account and optional child profile."""

    email: str
    password: str
    verify_password: str
    child_name: Optional[str] = None
    child_age: Optional[int] = None
    child_grade_level: Optional[str] = None
    child_tier: str = "standard"
    child_model_role: str = "student"


@router.post("/api/system/setup")
async def run_setup(request: SetupRequest, _rate=Depends(_check_setup_rate_limit)):
    """
    First-time web setup wizard.

    Creates the initial parent account and (optionally) the first child profile.
    This endpoint only works when the system has zero accounts — it refuses to
    run once any account exists, preventing abuse.
    """
    # ---- Guard: only allow when system is not yet initialized ----
    try:
        from storage.database import db_manager

        parents = db_manager.execute_query("SELECT COUNT(*) as count FROM accounts")
        if parents and parents[0]["count"] > 0:
            raise HTTPException(
                status_code=403,
                detail="Setup has already been completed. Please log in instead.",
            )
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error in setup guard: {e}")
        raise HTTPException(
            status_code=503,
            detail="Cannot verify system state. Please try again in a moment.",
        )
    except Exception as e:
        logger.exception(f"Unexpected error in setup guard: {e}")
        raise HTTPException(
            status_code=503,
            detail="Cannot verify system state. Please try again in a moment.",
        )

    # ---- Step 1: Create parent account ----
    try:
        from core.authentication import auth_manager

        if request.password != request.verify_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")

        success, result = auth_manager.create_parent_account(
            username=request.email,
            password=request.password,
            email=request.email,
            role="admin",
        )

        if not success:
            raise HTTPException(status_code=400, detail=result)

        user_id = result
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error during setup account creation: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during setup account creation: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to create account. Please try again."
        )

    # ---- Step 2: Optionally create child profile ----
    # COPPA: If child is under 13, do NOT create the profile during setup.
    # The parent must go through the parental consent workflow first.
    child_profile = None
    coppa_consent_required = False
    if request.child_name and request.child_age is not None:
        if request.child_age < 13:
            coppa_consent_required = True
            logger.info(
                f"Setup: child age {sanitize_log_value(request.child_age)!r} requires parental consent workflow. "
                f"Profile creation deferred."
            )
        else:
            try:
                from core.profile_manager import ProfileManager

                profile_manager = ProfileManager(auth_manager.db)
                profile = profile_manager.create_profile(
                    parent_id=user_id,
                    name=request.child_name,
                    age=request.child_age,
                    grade=request.child_grade_level or "5",
                )
                if profile:
                    child_profile = profile.to_dict()
            except DB_ERRORS as e:
                logger.warning(
                    f"Setup: child profile creation DB error (non-fatal): {e}"
                )
            except Exception as e:
                logger.warning(f"Setup: child profile creation failed (non-fatal): {e}")

    # ---- Step 3: Auto-login ----
    try:
        login_success, login_result = auth_manager.authenticate_parent(
            request.email, request.password
        )
        session_data = login_result if login_success else None
        token = (
            login_result.get("session_token")
            if login_success and isinstance(login_result, dict)
            else None
        )
    except DB_ERRORS as e:
        logger.warning(f"Setup: auto-login DB error (non-fatal): {e}")
        session_data = None
        token = None
    except Exception as e:
        logger.warning(f"Setup: auto-login failed (non-fatal): {e}")
        session_data = None
        token = None

    message = "Welcome to snflwr.ai! Your account is ready."
    if coppa_consent_required:
        message += (
            " Your child is under 13, so a parental consent verification is required "
            "before their profile can be created (COPPA compliance). "
            "Please complete the consent workflow from your dashboard."
        )

    return {
        "status": "success",
        "user_id": user_id,
        "session": session_data,
        "token": token,
        "child_profile": child_profile,
        "coppa_consent_required": coppa_consent_required,
        "message": message,
    }
