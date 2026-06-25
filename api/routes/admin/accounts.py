"""Admin dashboard routes for parent accounts and overview stats."""

import secrets
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.auth import require_admin
from core.authentication import AuthSession
from storage.db_adapters import DB_ERRORS
from utils.logger import sanitize_log_value

from ._common import (
    _ACCOUNT_UPDATE_COLUMNS,
    CreateAccountRequest,
    UpdateAccountRequest,
    _pkg,
    _to_dict,
    logger,
)

router = APIRouter()


@router.get("/stats")
async def get_admin_stats(session: AuthSession = Depends(require_admin)):
    """Get overview statistics for the admin dashboard"""
    try:
        db = _pkg().DatabaseManager()

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM accounts WHERE role = 'parent'"
        )
        total_parents = result[0]["c"] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM child_profiles WHERE is_active = 1"
        )
        active_children = result[0]["c"] if result else 0

        result = db.execute_query("SELECT COUNT(*) as c FROM child_profiles")
        total_children = result[0]["c"] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM parent_alerts WHERE acknowledged = 0"
        )
        pending_alerts = result[0]["c"] if result else 0

        result = db.execute_query(
            "SELECT COUNT(*) as c FROM sessions "
            "WHERE started_at > datetime('now', '-7 days')"
        )
        recent_sessions = result[0]["c"] if result else 0

        result = db.execute_query("SELECT COUNT(*) as c FROM safety_incidents")
        total_incidents = result[0]["c"] if result else 0

        _pkg().audit_log("read", "admin_stats", "overview", session)

        return {
            "total_parents": total_parents,
            "active_children": active_children,
            "total_children": total_children,
            "pending_alerts": pending_alerts,
            "recent_sessions": recent_sessions,
            "total_incidents": total_incidents,
        }
    except DB_ERRORS as e:
        logger.error(f"Database error getting admin stats: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error getting admin stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/accounts")
async def list_accounts(
    session: AuthSession = Depends(require_admin), limit: int = 100, offset: int = 0
):
    """List all parent accounts with decrypted emails"""
    try:
        db = _pkg().DatabaseManager()
        email_crypto = _pkg().get_email_crypto()

        accounts = db.execute_query(
            "SELECT parent_id, name, role, created_at, last_login, is_active, "
            "encrypted_email, email_verified, failed_login_attempts "
            "FROM accounts WHERE role = 'parent' "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

        result = []
        for row in accounts:
            acct = _to_dict(row)
            email = "[encrypted]"
            try:
                if acct.get("encrypted_email"):
                    email = email_crypto.decrypt_email(acct["encrypted_email"])
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt email for account {acct.get('parent_id', '?')}: {type(e).__name__}"
                )

            children = db.execute_query(
                "SELECT COUNT(*) as c FROM child_profiles WHERE parent_id = ?",
                (acct["parent_id"],),
            )

            result.append(
                {
                    "parent_id": acct["parent_id"],
                    "name": acct.get("name") or "",
                    "email": email,
                    "created_at": acct.get("created_at"),
                    "last_login": acct.get("last_login"),
                    "is_active": bool(acct.get("is_active", 0)),
                    "email_verified": bool(acct.get("email_verified", 0)),
                    "child_count": children[0]["c"] if children else 0,
                }
            )

        total = db.execute_query(
            "SELECT COUNT(*) as c FROM accounts WHERE role = 'parent'"
        )

        _pkg().audit_log("list", "accounts", "all", session)

        return {"accounts": result, "total": total[0]["c"] if total else 0}
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
    session: AuthSession = Depends(require_admin),
):
    """Update a parent account (admin only)"""
    try:
        db = _pkg().DatabaseManager()

        existing = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?", (parent_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")

        updates = []
        params: list = []

        if request.name is not None:
            updates.append("name = ?")
            params.append(request.name)

        if request.email is not None:
            email_crypto = _pkg().get_email_crypto()
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
        used_columns = {u.split(" = ")[0] for u in updates}
        if not used_columns <= _ACCOUNT_UPDATE_COLUMNS:
            raise ValueError(
                f"Unexpected columns: {used_columns - _ACCOUNT_UPDATE_COLUMNS}"
            )

        params.append(parent_id)
        db.execute_write(
            f"UPDATE accounts SET {', '.join(updates)} WHERE parent_id = ?",
            tuple(params),
        )

        _pkg().audit_log("update", "account", parent_id, session)

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
async def delete_account(parent_id: str, session: AuthSession = Depends(require_admin)):
    """Hard-delete a parent account and all its child profiles (cascade)."""
    try:
        db = _pkg().DatabaseManager()
        existing = db.execute_query(
            "SELECT parent_id FROM accounts WHERE parent_id = ?", (parent_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")
        db.execute_write("DELETE FROM accounts WHERE parent_id = ?", (parent_id,))
        _pkg().audit_log("delete", "account", parent_id, session)
        return {"success": True, "deleted": parent_id}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(
            f"Database error deleting account {sanitize_log_value(parent_id)!r}: {e}"
        )
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(
            f"Unexpected error deleting account {sanitize_log_value(parent_id)!r}: {e}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/accounts")
async def batch_delete_accounts(
    ids: List[str], session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple parent accounts by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = _pkg().DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM accounts WHERE parent_id IN ({placeholders})", tuple(ids)
        )
        _pkg().audit_log("delete", "accounts_batch", f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting accounts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting accounts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts")
async def create_account(
    request: CreateAccountRequest, session: AuthSession = Depends(require_admin)
):
    """Create a new parent account (Snflwr dashboard only — no Open WebUI user)"""
    try:
        db = _pkg().DatabaseManager()
        email_crypto = _pkg().get_email_crypto()

        parent_id = uuid.uuid4().hex
        email_hash, encrypted_email = email_crypto.prepare_email_for_storage(
            request.email
        )

        # Hash the password for Snflwr auth
        password_hash = _pkg().auth_manager.ph.hash(request.password)

        username = f"{request.email.split('@')[0]}_{secrets.token_hex(4)}"
        device_id = f"parent_{secrets.token_hex(8)}"

        db.execute_write(
            "INSERT INTO accounts "
            "(parent_id, username, device_id, email_hash, encrypted_email, "
            "password_hash, role, created_at, is_active, "
            "email_notifications_enabled, name) "
            "VALUES (?, ?, ?, ?, ?, ?, 'parent', ?, 1, 1, ?)",
            (
                parent_id,
                username,
                device_id,
                email_hash,
                encrypted_email,
                password_hash,
                datetime.now(timezone.utc).isoformat(),
                request.name,
            ),
        )

        _pkg().audit_log("create", "account", parent_id, session)

        return {
            "success": True,
            "parent_id": parent_id,
            "message": "Parent account created",
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error creating account: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error creating account: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
