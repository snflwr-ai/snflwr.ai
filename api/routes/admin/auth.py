"""Admin authentication routes: login (OWUI bridge + Snflwr fallback) and sync."""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.middleware.auth import require_admin
from config import system_config
from core.authentication import AuthSession, hash_session_token
from storage.db_adapters import DB_ERRORS
from utils.logger import sanitize_log_value

from ._common import (
    AdminLoginRequest,
    AdminSyncRequest,
    _pkg,
    check_auth_rate_limit,
    logger,
)

router = APIRouter()


@router.post("/login")
async def admin_login(
    request: AdminLoginRequest,
    response: Response,
    req: Request,
    rate_limit_info: dict = Depends(check_auth_rate_limit),
):
    """
    Admin login endpoint that bridges Open WebUI and Snflwr auth.

    Flow:
    1. Try authenticating via Open WebUI's signin endpoint
    2. If successful and user has admin role -> sync to Snflwr, create session
    3. Fall back to Snflwr's own auth for bootstrapped admin accounts
    """
    import requests as http_client  # type: ignore[import-untyped]

    open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")

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
                    detail="Admin access required. Your Open WebUI account is not an admin.",
                )

            owui_user_id = owui_data.get("id", "")
            owui_name = owui_data.get("name", request.email.split("@")[0])
            owui_email = owui_data.get("email", request.email)

            # Sync admin into Snflwr's accounts table (upsert)
            db = _pkg().DatabaseManager()
            email_crypto = _pkg().get_email_crypto()
            email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
                owui_email
            )

            existing = db.execute_query(
                "SELECT parent_id FROM accounts WHERE parent_id = ?", (owui_user_id,)
            )

            if existing:
                db.execute_write(
                    "UPDATE accounts SET email_hash = ?, encrypted_email = ?, "
                    "name = ?, last_login = ?, role = 'admin' WHERE parent_id = ?",
                    (
                        email_hash,
                        encrypted_email,
                        owui_name,
                        datetime.now(timezone.utc).isoformat(),
                        owui_user_id,
                    ),
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
                        owui_user_id,
                        username,
                        device_id,
                        email_hash,
                        encrypted_email,
                        datetime.now(timezone.utc).isoformat(),
                        owui_name,
                    ),
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
                        token_id,
                        owui_user_id,
                        owui_user_id,
                        hashed_token,
                        datetime.now(timezone.utc).isoformat(),
                        expires_at,
                    ),
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
            _pkg().auth_manager._set_session_in_cache(session_token, session_data)

            csrf_token = _pkg().set_csrf_cookie(response)

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
        email_crypto = _pkg().get_email_crypto()
        email_hash = email_crypto.hash_email(request.email)
        db = _pkg().DatabaseManager()
        acct_lookup = db.execute_query(
            "SELECT username FROM accounts WHERE email_hash = ?", (email_hash,)
        )
        if not acct_lookup:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        username = acct_lookup[0]["username"]
        success, result = _pkg().auth_manager.authenticate_parent(
            username, request.password
        )

        if not success:
            raise HTTPException(status_code=401, detail=result or "Invalid credentials")

        session_data = result

        # Verify the user is actually an admin
        acct = db.execute_query(
            "SELECT role FROM accounts WHERE parent_id = ?",
            (session_data["parent_id"],),
        )
        if not acct or acct[0]["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

        csrf_token = _pkg().set_csrf_cookie(response)

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
    request: AdminSyncRequest, session: AuthSession = Depends(require_admin)
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
        db = _pkg().DatabaseManager()
        email_crypto = _pkg().get_email_crypto()

        # Prepare email for storage
        email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
            request.email
        )

        # Check if admin already exists
        existing = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?", (request.admin_id,)
        )

        if existing:
            # Admin exists - update their email if changed
            db.execute_write(
                """
                UPDATE accounts
                SET email_hash = ?, encrypted_email = ?, last_login = CURRENT_TIMESTAMP
                WHERE parent_id = ?
                """,
                (email_hash, encrypted_email, request.admin_id),
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
                    request.email.split("@")[0],  # Use email prefix as default name
                ),
            )

            logger.info(f"Created new admin {sanitize_log_value(request.admin_id)!r}")

        # Fetch and return the admin record
        admin = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?", (request.admin_id,)
        )

        if not admin:
            raise HTTPException(status_code=500, detail="Failed to create/update admin")

        admin_data = admin[0]

        # Decrypt email for response
        decrypted_email = email_crypto.decrypt_email(admin_data["encrypted_email"])

        # Audit log
        _pkg().audit_log("sync", "admin", request.admin_id, session)

        return {
            "success": True,
            "admin": {
                "admin_id": admin_data["parent_id"],
                "email": decrypted_email,
                "role": admin_data["role"],
                "created_at": admin_data["created_at"],
                "is_active": bool(admin_data["is_active"]),
            },
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error syncing admin: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error syncing admin: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
