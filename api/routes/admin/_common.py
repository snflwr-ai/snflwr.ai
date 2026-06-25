"""
Shared building blocks for the admin routes package.

This module holds the imports, Pydantic request/response models, module-level
rate limiter, shared OWUI helpers and the ``_to_dict`` utility used across the
admin route submodules.

IMPORTANT — patchability contract:
The test suite patches names on the ``api.routes.admin`` package namespace
(e.g. ``patch("api.routes.admin.DatabaseManager")``). For those patches to take
effect inside running handlers and helpers, runtime symbol lookups go through
the package module object (``_pkg``) rather than capturing the name at import
time. ``api/routes/admin/__init__.py`` re-exports every symbol the tests patch,
so ``_pkg.DatabaseManager`` etc. resolve to the (possibly patched) attribute.
"""

import importlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field

from api.middleware.auth import audit_log, require_admin
from api.middleware.csrf_protection import set_csrf_cookie
from config import system_config
from core.age_verification import AgeVerificationManager
from core.authentication import AuthSession, auth_manager, hash_session_token
from core.email_crypto import get_email_crypto
from storage.database import DatabaseManager
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager
from utils.logger import get_logger, sanitize_log_value
from utils.rate_limiter import RateLimiter

logger = get_logger(__name__)

# Initialize rate limiter for admin auth endpoints
rate_limiter = RateLimiter()


def _pkg():
    """Return the ``api.routes.admin`` package module for call-time symbol lookup.

    Using the package namespace (rather than the locally-imported name) means
    ``unittest.mock.patch("api.routes.admin.X")`` is honored inside handlers.
    """
    return importlib.import_module("api.routes.admin")


def _get_owui_token(session: "AuthSession") -> str:
    """Retrieve the Open WebUI JWT for this admin session.

    Checks the in-memory/Redis session cache first (fast path), then falls back
    to the DB-persisted token (survives server restarts / uvicorn --reload).
    """
    cached = _pkg().auth_manager._get_session_from_cache(session.session_token)
    token = (cached or {}).get("owui_token", "")
    if token:
        return token

    # Cache miss (e.g., server restarted) — read from the DB.
    try:
        db = _pkg().DatabaseManager()
        rows = db.execute_query(
            "SELECT owui_token FROM accounts WHERE parent_id = ?",
            (session.user_id,),
        )
        if rows:
            val = rows[0]["owui_token"]
            return val or ""
        return ""
    except Exception as e:
        logger.warning(f"Failed to retrieve owui_token from DB: {e}")
        return ""


def _owui_find_user_by_email(open_webui_url: str, owui_token: str, email: str):
    """Look up an existing OWU user by email. Returns (user_dict, error) tuple."""
    import requests as http_client  # type: ignore[import-untyped]

    from utils.logger import get_logger as _get_logger

    _log = _get_logger(__name__)
    headers = {"Authorization": f"Bearer {owui_token}"}
    try:
        resp = http_client.get(
            f"{open_webui_url}/api/v1/users/all",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            users = resp.json()
            for u in users if isinstance(users, list) else users.get("users", []):
                if u.get("email", "").lower() == email.lower():
                    return u, None
            return None, "User not found"
        return None, f"OWU users list error ({resp.status_code})"
    except Exception:
        _log.exception("Unexpected error looking up OWU user by email")
        return None, "An internal error occurred"


def _owui_activate_user(open_webui_url: str, owui_token: str, user: dict):
    """Set an existing OWU user's role to 'user' (activates pending accounts)."""
    import requests as http_client  # type: ignore[import-untyped]

    headers = {"Authorization": f"Bearer {owui_token}"}
    user_id = user.get("id", "")
    try:
        resp = http_client.post(
            f"{open_webui_url}/api/v1/users/{user_id}/update",
            json={
                "role": "user",
                "name": user.get("name", ""),
                "email": user.get("email", ""),
                "profile_image_url": user.get("profile_image_url", "/user.png"),
            },
            headers=headers,
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _owui_delete_user(open_webui_url: str, owui_token: str, owui_user_id: str):
    """Delete an OWU user account. Best-effort — errors are logged, not raised."""
    import requests as http_client  # type: ignore[import-untyped]

    from utils.logger import get_logger as _get_logger

    _log = _get_logger(__name__)
    if not owui_user_id or not owui_token:
        return
    headers = {"Authorization": f"Bearer {owui_token}"}
    try:
        resp = http_client.delete(
            f"{open_webui_url}/api/v1/users/{owui_user_id}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            _log.warning(
                f"OWU delete user {owui_user_id!r} returned {resp.status_code}"
            )
    except Exception as e:
        _log.warning(f"OWU delete user {owui_user_id!r} failed: {e}")


def _owui_create_user(
    open_webui_url: str, owui_token: str, name: str, email: str, password: str
):
    """Create an Open WebUI user via the admin endpoint (works even when signup is disabled).

    If the email is already registered (e.g. leftover pending account from a previous
    failed attempt), this activates the existing account instead of erroring.
    Returns (owui_user_id, error_detail) tuple.
    """
    import requests as http_client  # type: ignore[import-untyped]

    from utils.logger import get_logger as _get_logger

    _log = _get_logger(__name__)

    headers = {"Authorization": f"Bearer {owui_token}"} if owui_token else {}
    endpoint = "/api/v1/auths/add" if owui_token else "/api/v1/auths/signup"
    _log.info(f"Creating OWU user via {endpoint} (token present: {bool(owui_token)})")

    try:
        resp = http_client.post(
            f"{open_webui_url}{endpoint}",
            json={"name": name, "email": email, "password": password, "role": "user"},
            headers=headers,
            timeout=10,
        )
        _log.info(f"OWU create user response: {resp.status_code}")
        if resp.status_code == 200:
            return resp.json().get("id"), None

        # If email already exists, find and activate the existing account.
        if resp.status_code == 400 and owui_token:
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = ""
            if (
                "already" in detail.lower()
                or "registered" in detail.lower()
                or "taken" in detail.lower()
            ):
                _log.info(
                    f"OWU email {sanitize_log_value(email)!r} already exists — activating existing account"
                )
                existing, err = _pkg()._owui_find_user_by_email(
                    open_webui_url, owui_token, email
                )
                if existing:
                    _pkg()._owui_activate_user(open_webui_url, owui_token, existing)
                    return existing.get("id"), None
                return (
                    None,
                    f"Email already registered in Open WebUI and could not retrieve user: {err}",
                )

        detail = "Unknown error"
        try:
            detail = resp.json().get("detail", detail)
        except Exception:
            pass
        _log.warning(f"OWU create user failed: {resp.status_code} {detail}")
        return None, f"Open WebUI error ({resp.status_code}): {detail}"
    except http_client.exceptions.ConnectionError:
        return None, "Open WebUI unreachable"
    except http_client.exceptions.Timeout:
        return None, "Open WebUI signup timed out"


def check_auth_rate_limit(request: Request):
    """
    Rate limiting dependency for admin auth endpoints.

    Limits: 5 requests per minute per IP for admin login.
    """
    client_ip = request.client.host if request.client else "unknown"

    allowed, info = _pkg().rate_limiter.check_rate_limit(
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


# Allowed columns for dynamic UPDATE queries (defense in depth).
# Only these column names may appear in SET clauses built at runtime.
_ACCOUNT_UPDATE_COLUMNS = frozenset(
    {
        "name",
        "email_hash",
        "encrypted_email",
        "is_active",
    }
)
_PROFILE_UPDATE_COLUMNS = frozenset(
    {
        "name",
        "age",
        "grade_level",
        "grade",
        "daily_time_limit_minutes",
        "is_active",
    }
)


def _to_dict(row):
    """Convert sqlite3.Row or dict-like row to a plain dict for safe .get() access"""
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {k: row[k] for k in row.keys()}


# ============================================================================
# Pydantic request / response models
# ============================================================================


class AdminSyncRequest(BaseModel):
    """Request to sync admin from Open WebUI"""

    admin_id: str  # Open WebUI user ID
    email: str  # Email from Open WebUI (already validated)


class AdminResponse(BaseModel):
    """Admin information response"""

    admin_id: str
    email: str
    role: str
    created_at: str
    is_active: bool


class AdminLoginRequest(BaseModel):
    """Admin login request — proxied through Open WebUI auth"""

    email: str
    password: str


class UpdateAccountRequest(BaseModel):
    """Request to update a parent account"""

    name: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None


class UpdateProfileAdminRequest(BaseModel):
    """Admin-level request to update a child profile"""

    name: Optional[str] = None
    age: Optional[int] = None
    grade_level: Optional[str] = None
    daily_time_limit_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class CreateAccountRequest(BaseModel):
    """Request to create a parent account from admin dashboard"""

    name: str
    email: str
    password: str


class CreateProfileRequest(BaseModel):
    """Request to create a child profile from admin dashboard"""

    parent_id: str
    name: str
    age: int
    grade_level: str
    daily_time_limit_minutes: Optional[int] = 120
    email: Optional[str] = None  # For Open WebUI login
    password: Optional[str] = None  # For Open WebUI login


class StudentImportRecord(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    age: int = Field(..., ge=5, le=18)
    grade_level: str = Field(..., min_length=1, max_length=20)


class BulkImportRequest(BaseModel):
    students: List[StudentImportRecord] = Field(..., min_length=1, max_length=500)
    password: str = Field(..., min_length=8)
    accept_institutional_coppa: bool = False


class FalsePositiveReview(BaseModel):
    reviewed_by: str
