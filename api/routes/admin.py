"""
Admin Management Routes
Handles syncing Open WebUI admins to Snflwr database and
provides admin dashboard API endpoints for managing accounts,
profiles, alerts, activity, and audit logs.

[LOCKED] SECURED: All routes require admin authentication
- Only admins can access these endpoints
- Prevents unauthorized admin account creation
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Response, Request, Query
from pydantic import BaseModel, EmailStr, Field
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from storage.database import DatabaseManager
from storage.db_adapters import DB_ERRORS
from core.authentication import auth_manager, AuthSession, hash_session_token
from core.email_crypto import get_email_crypto
from storage.encryption import encryption_manager
from api.middleware.auth import (
    require_admin,
    audit_log
)
from api.middleware.csrf_protection import set_csrf_cookie
from utils.rate_limiter import RateLimiter
from config import system_config

from core.age_verification import AgeVerificationManager
from utils.logger import get_logger, sanitize_log_value


def _get_owui_token(session: "AuthSession") -> str:
    """Retrieve the Open WebUI JWT for this admin session.

    Checks the in-memory/Redis session cache first (fast path), then falls back
    to the DB-persisted token (survives server restarts / uvicorn --reload).
    """
    cached = auth_manager._get_session_from_cache(session.session_token)
    token = (cached or {}).get("owui_token", "")
    if token:
        return token

    # Cache miss (e.g., server restarted) — read from the DB.
    try:
        db = DatabaseManager()
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
    import requests as http_client
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
            for u in (users if isinstance(users, list) else users.get("users", [])):
                if u.get("email", "").lower() == email.lower():
                    return u, None
            return None, "User not found"
        return None, f"OWU users list error ({resp.status_code})"
    except Exception as e:
        _log.exception("Unexpected error looking up OWU user by email")
        return None, "An internal error occurred"


def _owui_activate_user(open_webui_url: str, owui_token: str, user: dict):
    """Set an existing OWU user's role to 'user' (activates pending accounts)."""
    import requests as http_client
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
    import requests as http_client
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
            _log.warning(f"OWU delete user {owui_user_id!r} returned {resp.status_code}")
    except Exception as e:
        _log.warning(f"OWU delete user {owui_user_id!r} failed: {e}")


def _owui_create_user(open_webui_url: str, owui_token: str, name: str, email: str, password: str):
    """Create an Open WebUI user via the admin endpoint (works even when signup is disabled).

    If the email is already registered (e.g. leftover pending account from a previous
    failed attempt), this activates the existing account instead of erroring.
    Returns (owui_user_id, error_detail) tuple.
    """
    import requests as http_client
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
            if "already" in detail.lower() or "registered" in detail.lower() or "taken" in detail.lower():
                _log.info(f"OWU email {sanitize_log_value(email)!r} already exists — activating existing account")
                existing, err = _owui_find_user_by_email(open_webui_url, owui_token, email)
                if existing:
                    _owui_activate_user(open_webui_url, owui_token, existing)
                    return existing.get("id"), None
                return None, f"Email already registered in Open WebUI and could not retrieve user: {err}"

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

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = get_logger(__name__)

# Initialize rate limiter for admin auth endpoints
rate_limiter = RateLimiter()


def check_auth_rate_limit(request: Request):
    """
    Rate limiting dependency for admin auth endpoints.

    Limits: 5 requests per minute per IP for admin login.
    """
    client_ip = request.client.host if request.client else "unknown"

    allowed, info = rate_limiter.check_rate_limit(
        identifier=client_ip,
        max_requests=5,
        window_seconds=60,
        limit_type="auth"
    )

    if not allowed:
        logger.warning(f"Rate limit exceeded for IP {client_ip}: {info}")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Retry after {info.get('retry_after', 60)} seconds.",
            headers={"Retry-After": str(info.get('retry_after', 60))}
        )

    return info

# Allowed columns for dynamic UPDATE queries (defense in depth).
# Only these column names may appear in SET clauses built at runtime.
_ACCOUNT_UPDATE_COLUMNS = frozenset({
    'name', 'email_hash', 'encrypted_email', 'is_active',
})
_PROFILE_UPDATE_COLUMNS = frozenset({
    'name', 'age', 'grade_level', 'grade', 'daily_time_limit_minutes', 'is_active',
})

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


@router.post("/login")
async def admin_login(request: AdminLoginRequest, response: Response, req: Request, rate_limit_info: dict = Depends(check_auth_rate_limit)):
    """
    Admin login endpoint that bridges Open WebUI and Snflwr auth.

    Flow:
    1. Try authenticating via Open WebUI's signin endpoint
    2. If successful and user has admin role -> sync to Snflwr, create session
    3. Fall back to Snflwr's own auth for bootstrapped admin accounts
    """
    import requests as http_client

    open_webui_url = system_config.OPEN_WEBUI_URL.rstrip('/')

    # --- Try Open WebUI auth first ---
    try:
        owui_resp = http_client.post(
            f"{open_webui_url}/api/v1/auths/signin",
            json={"email": request.email, "password": request.password},
            timeout=10,
        )

        if owui_resp.status_code == 200:
            owui_data = owui_resp.json()
            owui_role = owui_data.get("role", "")

            if owui_role != "admin":
                raise HTTPException(
                    status_code=403,
                    detail="Admin access required. Your Open WebUI account is not an admin."
                )

            owui_user_id = owui_data.get("id", "")
            owui_name = owui_data.get("name", request.email.split("@")[0])
            owui_email = owui_data.get("email", request.email)

            # Sync admin into Snflwr's accounts table (upsert)
            db = DatabaseManager()
            email_crypto = get_email_crypto()
            email_hash, encrypted_email = email_crypto.prepare_email_for_storage(owui_email)

            existing = db.execute_query(
                "SELECT parent_id FROM accounts WHERE parent_id = ?",
                (owui_user_id,)
            )

            if existing:
                db.execute_write(
                    "UPDATE accounts SET email_hash = ?, encrypted_email = ?, "
                    "name = ?, last_login = ?, role = 'admin' WHERE parent_id = ?",
                    (
                        email_hash, encrypted_email, owui_name,
                        datetime.now(timezone.utc).isoformat(), owui_user_id
                    )
                )
            else:
                username = f"{owui_email.split('@')[0]}_{secrets.token_hex(4)}"
                device_id = f"admin_{secrets.token_hex(8)}"
                db.execute_write(
                    "INSERT INTO accounts "
                    "(parent_id, username, device_id, email_hash, encrypted_email, "
                    "password_hash, role, created_at, is_active, "
                    "email_notifications_enabled, name) "
                    "VALUES (?, ?, ?, ?, ?, 'OPENWEBUI_AUTH', 'admin', ?, 1, 1, ?)",
                    (
                        owui_user_id, username, device_id,
                        email_hash, encrypted_email,
                        datetime.now(timezone.utc).isoformat(), owui_name
                    )
                )
                logger.info(f"Created new Snflwr admin from Open WebUI: {owui_user_id}")

            # Create Snflwr session token for this admin
            session_token = secrets.token_hex(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

            token_id = uuid.uuid4().hex
            hashed_token = hash_session_token(session_token)
            try:
                db.execute_write(
                    "INSERT INTO auth_tokens "
                    "(token_id, user_id, parent_id, token_type, session_token, "
                    "created_at, expires_at, is_valid) "
                    "VALUES (?, ?, ?, 'session', ?, ?, ?, 1)",
                    (
                        token_id, owui_user_id, owui_user_id,
                        hashed_token,
                        datetime.now(timezone.utc).isoformat(), expires_at
                    )
                )
            except DB_ERRORS as e:
                logger.warning(f"Failed to persist admin session token: {e}")

            owui_token_value = owui_data.get("token", "")

            # Persist the Open WebUI token in the DB so it survives server restarts.
            try:
                db.execute_write(
                    "UPDATE accounts SET owui_token = ? WHERE parent_id = ?",
                    (owui_token_value, owui_user_id),
                )
            except Exception as e:
                logger.warning(f"Failed to persist owui_token in DB: {e}")

            session_data = {
                "parent_id": owui_user_id,
                "session_token": session_token,
                "expires_at": expires_at,
                "owui_token": owui_token_value,
            }

            # Cache session for validation
            auth_manager._set_session_in_cache(session_token, session_data)

            csrf_token = set_csrf_cookie(response)

            logger.info(f"Admin login via Open WebUI: {owui_user_id}")

            return {
                "session": session_data,
                "token": session_token,
                "csrf_token": csrf_token,
            }

        # Open WebUI returned non-200 (bad creds or server error)
        # Fall through to Snflwr direct auth below
        logger.debug(
            f"Open WebUI auth returned {owui_resp.status_code}, "
            f"falling back to Snflwr auth"
        )

    except HTTPException:
        raise
    except http_client.exceptions.ConnectionError:
        logger.warning("Open WebUI unreachable, falling back to Snflwr auth")
    except http_client.exceptions.Timeout:
        logger.warning("Open WebUI auth timed out, falling back to Snflwr auth")
    except Exception as e:
        logger.warning(f"Open WebUI auth failed ({e}), falling back to Snflwr auth")

    # --- Fallback: Snflwr direct auth (for bootstrapped admins) ---
    try:
        # Look up account by email hash (authenticate_parent queries by username,
        # but admin login uses email — resolve username from email_hash first)
        email_crypto = get_email_crypto()
        email_hash = email_crypto.hash_email(request.email)
        db = DatabaseManager()
        acct_lookup = db.execute_query(
            "SELECT username FROM accounts WHERE email_hash = ?",
            (email_hash,)
        )
        if not acct_lookup:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        username = acct_lookup[0]["username"]
        success, result = auth_manager.authenticate_parent(username, request.password)

        if not success:
            raise HTTPException(status_code=401, detail=result or "Invalid credentials")

        session_data = result

        # Verify the user is actually an admin
        acct = db.execute_query(
            "SELECT role FROM accounts WHERE parent_id = ?",
            (session_data["parent_id"],)
        )
        if not acct or acct[0]["role"] != "admin":
            raise HTTPException(
                status_code=403,
                detail="Admin access required"
            )

        csrf_token = set_csrf_cookie(response)

        logger.info(f"Admin login via Snflwr auth: {session_data['parent_id']}")

        return {
            "session": session_data,
            "token": session_data["session_token"],
            "csrf_token": csrf_token,
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error during admin login: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error during admin login: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sync")
async def sync_admin(
    request: AdminSyncRequest,
    session: AuthSession = Depends(require_admin)
):
    """
    Sync Open WebUI admin to Snflwr database

    [LOCKED] SECURED: Admin-only access to prevent unauthorized admin creation

    This endpoint is called when a user logs into Open WebUI to ensure
    they exist in Snflwr's users table. If they don't exist, creates them.
    If they exist, updates their info.

    Returns the admin record.

    Note: For first admin account creation, use the bootstrap script:
    python scripts/bootstrap_admin.py
    """
    try:
        db = DatabaseManager()
        email_crypto = get_email_crypto()

        # Prepare email for storage
        email_hash, encrypted_email = email_crypto.prepare_email_for_storage(request.email)

        # Check if admin already exists
        existing = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (request.admin_id,)
        )

        if existing:
            # Admin exists - update their email if changed
            db.execute_write(
                """
                UPDATE accounts
                SET email_hash = ?, encrypted_email = ?, last_login = CURRENT_TIMESTAMP
                WHERE parent_id = ?
                """,
                (email_hash, encrypted_email, request.admin_id)
            )

            logger.info(f"Updated admin {sanitize_log_value(request.admin_id)!r}")

        else:
            # Create new admin
            # Note: password_hash is required but not used (Open WebUI handles auth)
            username = f"{request.email.split('@')[0]}_{secrets.token_hex(4)}"
            device_id = f"admin_{secrets.token_hex(8)}"
            db.execute_write(
                """
                INSERT INTO accounts (parent_id, username, device_id, email_hash, encrypted_email, password_hash, role, created_at, is_active, email_notifications_enabled, name)
                VALUES (?, ?, ?, ?, ?, 'OPENWEBUI_AUTH', 'admin', CURRENT_TIMESTAMP, 1, 1, ?)
                """,
                (
                    request.admin_id,
                    username,
                    device_id,
                    email_hash,
                    encrypted_email,
                    request.email.split('@')[0]  # Use email prefix as default name
                )
            )

            logger.info(f"Created new admin {sanitize_log_value(request.admin_id)!r}")

        # Fetch and return the admin record
        admin = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (request.admin_id,)
        )

        if not admin:
            raise HTTPException(status_code=500, detail="Failed to create/update admin")

        admin_data = admin[0]

        # Decrypt email for response
        decrypted_email = email_crypto.decrypt_email(admin_data['encrypted_email'])

        # Audit log
        audit_log('sync', 'admin', request.admin_id, session)

        return {
            "success": True,
            "admin": {
                "admin_id": admin_data['parent_id'],
                "email": decrypted_email,
                "role": admin_data['role'],
                "created_at": admin_data['created_at'],
                "is_active": bool(admin_data['is_active'])
            }
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error syncing admin: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error syncing admin: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# Admin Dashboard API Endpoints
# ============================================================================


def _to_dict(row):
    """Convert sqlite3.Row or dict-like row to a plain dict for safe .get() access"""
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {k: row[k] for k in row.keys()}


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
    email: Optional[str] = None       # For Open WebUI login
    password: Optional[str] = None    # For Open WebUI login


class StudentImportRecord(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    age: int = Field(..., ge=5, le=18)
    grade_level: str = Field(..., min_length=1, max_length=20)


class BulkImportRequest(BaseModel):
    students: List[StudentImportRecord] = Field(..., min_length=1, max_length=500)
    password: str = Field(..., min_length=8)
    accept_institutional_coppa: bool = False


@router.get("/stats")
async def get_admin_stats(session: AuthSession = Depends(require_admin)):
    """Get overview statistics for the admin dashboard"""
    try:
        db = DatabaseManager()

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM accounts WHERE role = 'parent'"
        )
        total_parents = result[0]['c'] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM child_profiles WHERE is_active = 1"
        )
        active_children = result[0]['c'] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM child_profiles"
        )
        total_children = result[0]['c'] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM parent_alerts WHERE acknowledged = 0"
        )
        pending_alerts = result[0]['c'] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM sessions "
            "WHERE started_at > datetime('now', '-7 days')"
        )
        recent_sessions = result[0]['c'] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM safety_incidents"
        )
        total_incidents = result[0]['c'] if result else 0

        audit_log('read', 'admin_stats', 'overview', session)

        return {
            'total_parents': total_parents,
            'active_children': active_children,
            'total_children': total_children,
            'pending_alerts': pending_alerts,
            'recent_sessions': recent_sessions,
            'total_incidents': total_incidents
        }
    except DB_ERRORS as e:
        logger.error(f"Database error getting admin stats: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error getting admin stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/accounts")
async def list_accounts(
    session: AuthSession = Depends(require_admin),
    limit: int = 100,
    offset: int = 0
):
    """List all parent accounts with decrypted emails"""
    try:
        db = DatabaseManager()
        email_crypto = get_email_crypto()

        accounts = db.execute_query(
            "SELECT parent_id, name, role, created_at, last_login, is_active, "
            "encrypted_email, email_verified, failed_login_attempts "
            "FROM accounts WHERE role = 'parent' "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )

        result = []
        for row in accounts:
            acct = _to_dict(row)
            email = '[encrypted]'
            try:
                if acct.get('encrypted_email'):
                    email = email_crypto.decrypt_email(acct['encrypted_email'])
            except Exception as e:
                logger.warning(f"Failed to decrypt email for account {acct.get('parent_id', '?')}: {type(e).__name__}")

            children = db.execute_query(
                "SELECT COUNT(*) as c FROM child_profiles WHERE parent_id = ?",
                (acct['parent_id'],)
            )

            result.append({
                'parent_id': acct['parent_id'],
                'name': acct.get('name') or '',
                'email': email,
                'created_at': acct.get('created_at'),
                'last_login': acct.get('last_login'),
                'is_active': bool(acct.get('is_active', 0)),
                'email_verified': bool(acct.get('email_verified', 0)),
                'child_count': children[0]['c'] if children else 0
            })

        total = db.execute_query(
            "SELECT COUNT(*) as c FROM accounts WHERE role = 'parent'"
        )

        audit_log('list', 'accounts', 'all', session)

        return {
            'accounts': result,
            'total': total[0]['c'] if total else 0
        }
    except DB_ERRORS as e:
        logger.error(f"Database error listing accounts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing accounts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/accounts/{parent_id}")
async def update_account(
    parent_id: str,
    request: UpdateAccountRequest,
    session: AuthSession = Depends(require_admin)
):
    """Update a parent account (admin only)"""
    try:
        db = DatabaseManager()

        existing = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")

        updates = []
        params = []

        if request.name is not None:
            updates.append("name = ?")
            params.append(request.name)

        if request.email is not None:
            email_crypto = get_email_crypto()
            email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
                request.email
            )
            updates.append("email_hash = ?")
            params.append(email_hash)
            updates.append("encrypted_email = ?")
            params.append(encrypted_email)

        if request.is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if request.is_active else 0)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Defense in depth: verify only allowlisted columns in SET clause
        used_columns = {u.split(' = ')[0] for u in updates}
        if not used_columns <= _ACCOUNT_UPDATE_COLUMNS:
            raise ValueError(f"Unexpected columns: {used_columns - _ACCOUNT_UPDATE_COLUMNS}")

        params.append(parent_id)
        db.execute_write(
            f"UPDATE accounts SET {', '.join(updates)} WHERE parent_id = ?",
            tuple(params)
        )

        audit_log('update', 'account', parent_id, session)

        return {"success": True, "message": "Account updated"}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error updating account: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error updating account: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/accounts/{parent_id}")
async def delete_account(
    parent_id: str,
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete a parent account and all its child profiles (cascade)."""
    try:
        db = DatabaseManager()
        existing = db.execute_query(
            "SELECT parent_id FROM accounts WHERE parent_id = ?", (parent_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")
        db.execute_write("DELETE FROM accounts WHERE parent_id = ?", (parent_id,))
        audit_log('delete', 'account', parent_id, session)
        return {"success": True, "deleted": parent_id}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error deleting account {sanitize_log_value(parent_id)!r}: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error deleting account {sanitize_log_value(parent_id)!r}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/accounts")
async def batch_delete_accounts(
    ids: List[str],
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple parent accounts by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM accounts WHERE parent_id IN ({placeholders})", tuple(ids)
        )
        audit_log('delete', 'accounts_batch', f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting accounts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting accounts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts")
async def create_account(
    request: CreateAccountRequest,
    session: AuthSession = Depends(require_admin)
):
    """Create a new parent account (Snflwr dashboard only — no Open WebUI user)"""
    try:
        db = DatabaseManager()
        email_crypto = get_email_crypto()

        parent_id = uuid.uuid4().hex
        email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
            request.email
        )

        # Hash the password for Snflwr auth
        password_hash = auth_manager.ph.hash(request.password)

        username = f"{request.email.split('@')[0]}_{secrets.token_hex(4)}"
        device_id = f"parent_{secrets.token_hex(8)}"

        db.execute_write(
            "INSERT INTO accounts "
            "(parent_id, username, device_id, email_hash, encrypted_email, "
            "password_hash, role, created_at, is_active, "
            "email_notifications_enabled, name) "
            "VALUES (?, ?, ?, ?, ?, ?, 'parent', ?, 1, 1, ?)",
            (
                parent_id, username, device_id,
                email_hash, encrypted_email, password_hash,
                datetime.now(timezone.utc).isoformat(), request.name
            )
        )

        audit_log('create', 'account', parent_id, session)

        return {
            "success": True,
            "parent_id": parent_id,
            "message": "Parent account created"
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error creating account: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error creating account: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/profiles")
async def create_profile(
    request: CreateProfileRequest,
    session: AuthSession = Depends(require_admin)
):
    """Create a new student profile with Open WebUI login"""
    import requests as http_client

    try:
        db = DatabaseManager()
        open_webui_url = system_config.OPEN_WEBUI_URL.rstrip('/')

        # Verify parent exists
        parent = db.execute_query(
            "SELECT parent_id, name FROM accounts WHERE parent_id = ?",
            (request.parent_id,)
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Parent account not found")

        # Create Open WebUI account for the student (so they can log in)
        owui_user_id = None
        if request.email and request.password:
            owui_token = _get_owui_token(session)
            owui_user_id, error = _owui_create_user(
                open_webui_url, owui_token, request.name, request.email, request.password
            )
            if error:
                raise HTTPException(status_code=400, detail=f"Failed to create Open WebUI account: {error}")
            logger.info(f"Created Open WebUI account for student: {owui_user_id}")

        # Create the child profile in Snflwr
        profile_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        db.execute_write(
            "INSERT INTO child_profiles "
            "(profile_id, parent_id, name, age, grade, grade_level, "
            "tier, model_role, created_at, is_active, "
            "daily_time_limit_minutes, owui_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'standard', 'student', ?, 1, ?, ?)",
            (
                profile_id, request.parent_id, request.name,
                request.age, request.grade_level, request.grade_level,
                now, request.daily_time_limit_minutes or 120,
                owui_user_id
            )
        )

        audit_log('create', 'profile', profile_id, session)

        msg = "Student profile created"
        if owui_user_id:
            msg += " with Open WebUI login"

        return {
            "success": True,
            "profile_id": profile_id,
            "owui_user_id": owui_user_id,
            "message": msg
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error creating profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error creating profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/students/import")
async def bulk_import_students(
    request: BulkImportRequest,
    session: AuthSession = Depends(require_admin)
):
    """Bulk provision students from a school roster.

    Creates an Open WebUI account and a linked snflwr profile for each student.
    Fail-forward: one student failing does not abort the rest.

    For students under 13, ``accept_institutional_coppa`` must be True — the
    admin accepts COPPA responsibility on behalf of the institution.
    """
    db = DatabaseManager()
    open_webui_url = system_config.OPEN_WEBUI_URL.rstrip('/')
    age_manager = AgeVerificationManager(db)

    created = []
    failed = []
    owui_token = _get_owui_token(session)

    for s in request.students:
        # COPPA gate: under-13 requires institutional consent flag
        if s.age < 13 and not request.accept_institutional_coppa:
            failed.append({
                "email": s.email,
                "error": "Student is under 13 — set accept_institutional_coppa=true to proceed"
            })
            continue

        # Create Open WebUI account
        owui_user_id, error = _owui_create_user(
            open_webui_url, owui_token, s.name, s.email, request.password
        )
        if error:
            failed.append({"email": s.email, "error": error})
            continue

        # Create snflwr profile
        profile_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        try:
            db.execute_write(
                "INSERT INTO child_profiles "
                "(profile_id, parent_id, name, age, grade, grade_level, "
                "tier, model_role, created_at, is_active, owui_user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 'standard', 'student', ?, 1, ?)",
                (
                    profile_id, session.user_id, s.name,
                    s.age, s.grade_level, s.grade_level,
                    now, owui_user_id
                )
            )
        except DB_ERRORS as e:
            logger.error(f"DB error creating profile for {s.email!r}: {e}")
            failed.append({"email": s.email, "error": "Database error creating profile"})
            continue

        # COPPA consent for under-13 via institutional exception
        if s.age < 13:
            try:
                age_manager.update_profile_consent_status(
                    profile_id=profile_id,
                    consent_given=True,
                    consent_date=now,
                    consent_method="institutional",
                )
                age_manager.log_parental_consent(
                    profile_id=profile_id,
                    parent_id=session.user_id,
                    consent_method="institutional",
                    electronic_signature=f"Bulk import by admin {session.user_id}",
                )
            except Exception as e:
                # COPPA log failure is non-fatal — profile is created, flag it
                logger.error(f"COPPA consent log failed for {profile_id}: {e}")

        logger.info(f"Imported student {s.email!r} → profile {profile_id!r}, owui {owui_user_id!r}")
        created.append(s.email)

    audit_log('create', 'student_bulk_import', f"imported={len(created)}", session)

    return {"imported": len(created), "failed": failed}


@router.get("/students")
async def list_students(
    session: AuthSession = Depends(require_admin),
    limit: int = 200,
    offset: int = 0,
):
    """List child profiles owned by this admin with Open WebUI link status."""
    try:
        db = DatabaseManager()
        rows = db.execute_query(
            "SELECT profile_id, name, age, grade_level, owui_user_id, "
            "parental_consent_given, coppa_verified, is_active, created_at "
            "FROM child_profiles "
            "WHERE parent_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (session.user_id, limit, offset)
        )
        return [
            {
                **_to_dict(r),
                "linked": bool(_to_dict(r).get("owui_user_id")),
            }
            for r in (rows or [])
        ]
    except DB_ERRORS as e:
        logger.error(f"DB error listing students: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing students: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/profiles/all")
async def list_all_profiles(
    session: AuthSession = Depends(require_admin),
    limit: int = 100,
    offset: int = 0
):
    """List all child profiles with parent info"""
    try:
        db = DatabaseManager()
        email_crypto = get_email_crypto()

        profiles = db.execute_query(
            "SELECT cp.*, a.name as parent_name, "
            "a.encrypted_email as parent_encrypted_email "
            "FROM child_profiles cp "
            "LEFT JOIN accounts a ON cp.parent_id = a.parent_id "
            "ORDER BY cp.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )

        result = []
        for row in profiles:
            p = _to_dict(row)
            parent_email = ''
            try:
                if p.get('parent_encrypted_email'):
                    parent_email = email_crypto.decrypt_email(
                        p['parent_encrypted_email']
                    )
            except Exception as e:
                logger.warning(f"Failed to decrypt parent email for profile {p.get('profile_id', '?')}: {type(e).__name__}")
                parent_email = '[encrypted]'

            result.append({
                'profile_id': p['profile_id'],
                'parent_id': p['parent_id'],
                'parent_name': p.get('parent_name') or '',
                'parent_email': parent_email,
                'name': p['name'],
                'age': p.get('age'),
                'grade_level': p.get('grade_level') or p.get('grade') or '',
                'is_active': bool(p.get('is_active', 0)),
                'created_at': p.get('created_at'),
                'last_active': p.get('last_active'),
                'total_sessions': p.get('total_sessions', 0),
                'total_questions': p.get('total_questions', 0),
                'daily_time_limit_minutes': p.get('daily_time_limit_minutes', 0),
                'tier': p.get('tier', 'standard')
            })

        total = db.execute_query("SELECT COUNT(*) as c FROM child_profiles")

        audit_log('list', 'profiles', 'all', session)

        return {
            'profiles': result,
            'total': total[0]['c'] if total else 0
        }
    except DB_ERRORS as e:
        logger.error(f"Database error listing profiles: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing profiles: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/profiles/{profile_id}")
async def admin_update_profile(
    profile_id: str,
    request: UpdateProfileAdminRequest,
    session: AuthSession = Depends(require_admin)
):
    """Admin-level update of a child profile"""
    try:
        db = DatabaseManager()

        existing = db.execute_query(
            "SELECT * FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Profile not found")

        updates = []
        params = []

        if request.name is not None:
            updates.append("name = ?")
            params.append(request.name)
        if request.age is not None:
            updates.append("age = ?")
            params.append(request.age)
        if request.grade_level is not None:
            updates.append("grade_level = ?")
            params.append(request.grade_level)
            updates.append("grade = ?")
            params.append(request.grade_level)
        if request.daily_time_limit_minutes is not None:
            updates.append("daily_time_limit_minutes = ?")
            params.append(request.daily_time_limit_minutes)
        if request.is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if request.is_active else 0)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Defense in depth: verify only allowlisted columns in SET clause
        used_columns = {u.split(' = ')[0] for u in updates}
        if not used_columns <= _PROFILE_UPDATE_COLUMNS:
            raise ValueError(f"Unexpected columns: {used_columns - _PROFILE_UPDATE_COLUMNS}")

        params.append(profile_id)
        db.execute_write(
            f"UPDATE child_profiles SET {', '.join(updates)} "
            f"WHERE profile_id = ?",
            tuple(params)
        )

        audit_log('update', 'profile', profile_id, session)

        return {"success": True, "message": "Profile updated"}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error updating profile: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error updating profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete a student profile and all its associated data (cascade)."""
    try:
        db = DatabaseManager()
        existing = db.execute_query(
            "SELECT profile_id, owui_user_id FROM child_profiles WHERE profile_id = ?", (profile_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Profile not found")
        owui_user_id = existing[0]["owui_user_id"] if existing[0]["owui_user_id"] else None
        db.execute_write("DELETE FROM child_profiles WHERE profile_id = ?", (profile_id,))
        # Best-effort: remove the corresponding Open WebUI account too.
        if owui_user_id:
            open_webui_url = system_config.OPEN_WEBUI_URL.rstrip('/')
            owui_token = _get_owui_token(session)
            _owui_delete_user(open_webui_url, owui_token, owui_user_id)
        audit_log('delete', 'profile', profile_id, session)
        return {"success": True, "deleted": profile_id}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error deleting profile {sanitize_log_value(profile_id)!r}: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error deleting profile {sanitize_log_value(profile_id)!r}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/profiles")
async def batch_delete_profiles(
    ids: List[str],
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple student profiles by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        # Collect OWU user IDs before deleting.
        rows = db.execute_query(
            f"SELECT owui_user_id FROM child_profiles WHERE profile_id IN ({placeholders})",
            tuple(ids),
        )
        owui_ids = [r["owui_user_id"] for r in rows if r["owui_user_id"]]
        db.execute_write(
            f"DELETE FROM child_profiles WHERE profile_id IN ({placeholders})", tuple(ids)
        )
        # Best-effort: remove corresponding Open WebUI accounts.
        if owui_ids:
            open_webui_url = system_config.OPEN_WEBUI_URL.rstrip('/')
            owui_token = _get_owui_token(session)
            for oid in owui_ids:
                _owui_delete_user(open_webui_url, owui_token, oid)
        audit_log('delete', 'profiles_batch', f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting profiles: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting profiles: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/alerts")
async def batch_delete_alerts(
    ids: List[int],
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple parent alerts by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM parent_alerts WHERE alert_id IN ({placeholders})", tuple(ids)
        )
        audit_log('delete', 'alerts_batch', f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting alerts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/activity")
async def batch_delete_activity(
    ids: List[str],
    session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple session records (and their conversations/messages) by session_id."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM sessions WHERE session_id IN ({placeholders})", tuple(ids)
        )
        audit_log('delete', 'activity_batch', f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting activity: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting activity: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/alerts/all")
async def list_all_alerts(
    session: AuthSession = Depends(require_admin),
    include_acknowledged: bool = False,
    limit: int = Query(100, le=1000)
):
    """List all safety alerts across all parents"""
    try:
        db = DatabaseManager()
        email_crypto = get_email_crypto()

        if include_acknowledged:
            alerts = db.execute_query(
                "SELECT pa.*, si.profile_id as profile_id, "
                "si.content_snippet as content_snippet, "
                "cp.name as child_name, a.name as parent_name, "
                "a.encrypted_email as parent_encrypted_email "
                "FROM parent_alerts pa "
                "LEFT JOIN safety_incidents si ON pa.related_incident_id = si.incident_id "
                "LEFT JOIN child_profiles cp ON si.profile_id = cp.profile_id "
                "LEFT JOIN accounts a ON pa.parent_id = a.parent_id "
                "ORDER BY pa.timestamp DESC LIMIT ?",
                (limit,)
            )
        else:
            alerts = db.execute_query(
                "SELECT pa.*, si.profile_id as profile_id, "
                "si.content_snippet as content_snippet, "
                "cp.name as child_name, a.name as parent_name, "
                "a.encrypted_email as parent_encrypted_email "
                "FROM parent_alerts pa "
                "LEFT JOIN safety_incidents si ON pa.related_incident_id = si.incident_id "
                "LEFT JOIN child_profiles cp ON si.profile_id = cp.profile_id "
                "LEFT JOIN accounts a ON pa.parent_id = a.parent_id "
                "WHERE pa.acknowledged = 0 "
                "ORDER BY pa.timestamp DESC LIMIT ?",
                (limit,)
            )

        result = []
        for row in alerts:
            al = _to_dict(row)
            parent_email = ''
            try:
                if al.get('parent_encrypted_email'):
                    parent_email = email_crypto.decrypt_email(
                        al['parent_encrypted_email']
                    )
            except Exception as e:
                logger.warning(f"Failed to decrypt parent email for alert {al.get('alert_id', '?')}: {type(e).__name__}")
                parent_email = '[encrypted]'

            # Decrypt content_snippet (stored encrypted in safety_incidents)
            snippet = ''
            try:
                raw_snippet = al.get('content_snippet', '')
                if raw_snippet:
                    decrypted = encryption_manager.decrypt_string(raw_snippet)
                    snippet = decrypted if decrypted else ''
            except Exception as e:
                logger.warning(f"Failed to decrypt content snippet for alert {al.get('alert_id', '?')}: {type(e).__name__}")
                snippet = '[encrypted]'

            result.append({
                'alert_id': al['alert_id'],
                'parent_id': al['parent_id'],
                'profile_id': al.get('profile_id', ''),
                'child_name': al.get('child_name', ''),
                'parent_name': al.get('parent_name', ''),
                'parent_email': parent_email,
                'severity': al.get('severity', 'medium'),
                'alert_type': al.get('alert_type', ''),
                'message': al.get('message', ''),
                'content_snippet': snippet,
                'timestamp': al.get('timestamp'),
                'acknowledged': bool(al.get('acknowledged', 0)),
            })

        audit_log('list', 'alerts', 'all', session)

        return {'alerts': result, 'total': len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error listing alerts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity")
async def list_activity(
    session: AuthSession = Depends(require_admin),
    limit: int = 50
):
    """List recent activity across all profiles"""
    try:
        db = DatabaseManager()

        sessions = db.execute_query(
            "SELECT s.*, cp.name as child_name "
            "FROM sessions s "
            "LEFT JOIN child_profiles cp ON s.profile_id = cp.profile_id "
            "ORDER BY s.started_at DESC LIMIT ?",
            (limit,)
        )

        result = []
        for row in sessions:
            s = _to_dict(row)
            result.append({
                'session_id': s['session_id'],
                'profile_id': s['profile_id'],
                'child_name': s.get('child_name', ''),
                'session_type': s.get('session_type', ''),
                'started_at': s.get('started_at'),
                'ended_at': s.get('ended_at'),
                'duration_minutes': s.get('duration_minutes', 0),
                'questions_asked': s.get('questions_asked', 0),
                'platform': s.get('platform', ''),
                'is_active': s.get('ended_at') is None
            })

        audit_log('list', 'activity', 'all', session)

        return {'sessions': result, 'total': len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error listing activity: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing activity: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/audit-log")
async def get_audit_log_entries(
    session: AuthSession = Depends(require_admin),
    limit: int = 50
):
    """Get recent audit log entries"""
    try:
        db = DatabaseManager()

        entries = db.execute_query(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )

        result = []
        for row in entries:
            entry = _to_dict(row)
            result.append({
                'log_id': entry.get('log_id'),
                'timestamp': entry.get('timestamp'),
                'event_type': entry.get('event_type', ''),
                'user_id': entry.get('user_id', ''),
                'user_type': entry.get('user_type', ''),
                'action': entry.get('action', ''),
                'details': entry.get('details', ''),
                'success': bool(entry.get('success', 0))
            })

        return {'entries': result, 'total': len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error getting audit log: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error getting audit log: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class FalsePositiveReview(BaseModel):
    reviewed_by: str


@router.get("/false-positives")
async def list_false_positives(
    session: AuthSession = Depends(require_admin),
):
    """
    List unreviewed false positive reports from educators/parents.

    [LOCKED] SECURED: Admin only.
    """
    try:
        db = DatabaseManager()
        rows = db.get_false_positives(reviewed=False)
        audit_log('read', 'false_positives', 'all', session)
        return {"false_positives": rows, "count": len(rows)}
    except DB_ERRORS as e:
        logger.error(f"Database error listing false positives: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing false positives: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/false-positives/{fp_id}")
async def mark_false_positive_reviewed(
    fp_id: int,
    body: FalsePositiveReview,
    session: AuthSession = Depends(require_admin),
):
    """
    Mark a false positive report as reviewed.

    [LOCKED] SECURED: Admin only.
    """
    try:
        db = DatabaseManager()
        db.mark_false_positive_reviewed(fp_id, body.reviewed_by)
        audit_log('update', 'false_positive', str(fp_id), session)
        return {"success": True}
    except DB_ERRORS as e:
        logger.error(f"Database error marking false positive reviewed: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error marking false positive reviewed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# Catch-all route — MUST be last to avoid shadowing specific routes
# ============================================================================

@router.get("/{admin_id}")
async def get_admin(
    admin_id: str,
    session: AuthSession = Depends(require_admin)
):
    """
    Get admin information by ID

    [LOCKED] SECURED: Admin-only access
    NOTE: This route MUST be defined last so /stats, /accounts etc. match first.
    """
    try:
        db = DatabaseManager()

        admin = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ? AND role = 'admin'",
            (admin_id,)
        )

        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")

        admin_data = _to_dict(admin[0])

        email_crypto = get_email_crypto()
        decrypted_email = email_crypto.decrypt_email(admin_data['encrypted_email'])

        audit_log('read', 'admin', admin_id, session)

        return {
            "admin_id": admin_data['parent_id'],
            "email": decrypted_email,
            "name": admin_data.get('name'),
            "role": admin_data['role'],
            "created_at": admin_data['created_at'],
            "is_active": bool(admin_data.get('is_active', 0))
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error fetching admin: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error fetching admin: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
