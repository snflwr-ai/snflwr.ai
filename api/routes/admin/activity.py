"""Admin dashboard routes for alerts, activity, audit log and false positives."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from api.middleware.auth import require_admin
from core.authentication import AuthSession
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager

from ._common import (
    FalsePositiveReview,
    _pkg,
    _to_dict,
    logger,
)

router = APIRouter()


@router.delete("/alerts")
async def batch_delete_alerts(
    ids: List[int], session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple parent alerts by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = _pkg().DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM parent_alerts WHERE alert_id IN ({placeholders})", tuple(ids)
        )
        _pkg().audit_log("delete", "alerts_batch", f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting alerts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/activity")
async def batch_delete_activity(
    ids: List[str], session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple session records (and their conversations/messages) by session_id."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = _pkg().DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        db.execute_write(
            f"DELETE FROM sessions WHERE session_id IN ({placeholders})", tuple(ids)
        )
        _pkg().audit_log("delete", "activity_batch", f"count={len(ids)}", session)
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
    limit: int = Query(100, le=1000),
):
    """List all safety alerts across all parents"""
    try:
        db = _pkg().DatabaseManager()
        email_crypto = _pkg().get_email_crypto()

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
                (limit,),
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
                (limit,),
            )

        result = []
        for row in alerts:
            al = _to_dict(row)
            parent_email = ""
            try:
                if al.get("parent_encrypted_email"):
                    parent_email = email_crypto.decrypt_email(
                        al["parent_encrypted_email"]
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt parent email for alert {al.get('alert_id', '?')}: {type(e).__name__}"
                )
                parent_email = "[encrypted]"

            # Decrypt content_snippet (stored encrypted in safety_incidents)
            snippet = ""
            try:
                raw_snippet = al.get("content_snippet", "")
                if raw_snippet:
                    decrypted = encryption_manager.decrypt_string(raw_snippet)
                    snippet = decrypted if decrypted else ""
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt content snippet for alert {al.get('alert_id', '?')}: {type(e).__name__}"
                )
                snippet = "[encrypted]"

            result.append(
                {
                    "alert_id": al["alert_id"],
                    "parent_id": al["parent_id"],
                    "profile_id": al.get("profile_id", ""),
                    "child_name": al.get("child_name", ""),
                    "parent_name": al.get("parent_name", ""),
                    "parent_email": parent_email,
                    "severity": al.get("severity", "medium"),
                    "alert_type": al.get("alert_type", ""),
                    "message": al.get("message", ""),
                    "content_snippet": snippet,
                    "timestamp": al.get("timestamp"),
                    "acknowledged": bool(al.get("acknowledged", 0)),
                }
            )

        _pkg().audit_log("list", "alerts", "all", session)

        return {"alerts": result, "total": len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error listing alerts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity")
async def list_activity(session: AuthSession = Depends(require_admin), limit: int = 50):
    """List recent activity across all profiles"""
    try:
        db = _pkg().DatabaseManager()

        sessions = db.execute_query(
            "SELECT s.*, cp.name as child_name "
            "FROM sessions s "
            "LEFT JOIN child_profiles cp ON s.profile_id = cp.profile_id "
            "ORDER BY s.started_at DESC LIMIT ?",
            (limit,),
        )

        result = []
        for row in sessions:
            s = _to_dict(row)
            result.append(
                {
                    "session_id": s["session_id"],
                    "profile_id": s["profile_id"],
                    "child_name": s.get("child_name", ""),
                    "session_type": s.get("session_type", ""),
                    "started_at": s.get("started_at"),
                    "ended_at": s.get("ended_at"),
                    "duration_minutes": s.get("duration_minutes", 0),
                    "questions_asked": s.get("questions_asked", 0),
                    "platform": s.get("platform", ""),
                    "is_active": s.get("ended_at") is None,
                }
            )

        _pkg().audit_log("list", "activity", "all", session)

        return {"sessions": result, "total": len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error listing activity: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error listing activity: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/audit-log")
async def get_audit_log_entries(
    session: AuthSession = Depends(require_admin), limit: int = 50
):
    """Get recent audit log entries"""
    try:
        db = _pkg().DatabaseManager()

        entries = db.execute_query(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

        result = []
        for row in entries:
            entry = _to_dict(row)
            result.append(
                {
                    "log_id": entry.get("log_id"),
                    "timestamp": entry.get("timestamp"),
                    "event_type": entry.get("event_type", ""),
                    "user_id": entry.get("user_id", ""),
                    "user_type": entry.get("user_type", ""),
                    "action": entry.get("action", ""),
                    "details": entry.get("details", ""),
                    "success": bool(entry.get("success", 0)),
                }
            )

        return {"entries": result, "total": len(result)}
    except DB_ERRORS as e:
        logger.error(f"Database error getting audit log: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error getting audit log: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/false-positives")
async def list_false_positives(
    session: AuthSession = Depends(require_admin),
):
    """
    List unreviewed false positive reports from educators/parents.

    [LOCKED] SECURED: Admin only.
    """
    try:
        db = _pkg().DatabaseManager()
        rows = db.get_false_positives(reviewed=False)
        _pkg().audit_log("read", "false_positives", "all", session)
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
        db = _pkg().DatabaseManager()
        db.mark_false_positive_reviewed(fp_id, body.reviewed_by)
        _pkg().audit_log("update", "false_positive", str(fp_id), session)
        return {"success": True}
    except DB_ERRORS as e:
        logger.error(f"Database error marking false positive reviewed: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error marking false positive reviewed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
