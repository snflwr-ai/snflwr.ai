"""
Safety Monitoring API Routes
Safety alerts and incident management

[LOCKED] SECURED: All routes require authentication
- Parents can only access their own children's safety data
- Admins can access all safety data
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from safety.safety_monitor import safety_monitor
from safety.incident_logger import incident_logger
from core.authentication import auth_manager, AuthSession
from storage.db_adapters import DB_ERRORS
from api.middleware.auth import (
    get_current_session,
    VerifyParentAccess,
    VerifyProfileAccess,
    VerifyAlertAccess,
    audit_log
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/alerts/{parent_id}")
async def get_parent_alerts(
    parent_id: str,
    session: AuthSession = Depends(VerifyParentAccess)
):
    """
    Get safety alerts for a parent

    [LOCKED] SECURED: Parents can only view their own alerts, admins can view all
    """
    try:
        alerts = safety_monitor.get_pending_alerts(parent_id)

        # Audit log
        audit_log('read', 'safety_alerts', parent_id, session)

        return {
            "alerts": [a.to_dict() for a in alerts],
            "count": len(alerts)
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving alerts: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    session: AuthSession = Depends(VerifyAlertAccess)
):
    """
    Acknowledge a safety alert

    [LOCKED] SECURED: Parents can only acknowledge their own alerts, admins can acknowledge all
    """
    try:
        # Alert ownership verified by VerifyAlertAccess dependency
        success = safety_monitor.acknowledge_alert(alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")

        # Audit log
        audit_log('update', 'safety_alert', alert_id, session)

        return {"status": "success"}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error acknowledging alert: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error acknowledging alert: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/incidents/{profile_id}")
async def get_profile_incidents(
    profile_id: str,
    days: int = 30,
    session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Get safety incidents for a profile

    [LOCKED] SECURED: Parents can only view their own children's incidents, admins can view all
    """
    try:
        incidents = incident_logger.get_profile_incidents(profile_id, days=days)

        # Audit log
        audit_log('read', 'safety_incidents', profile_id, session)

        return {
            "incidents": incidents,
            "count": len(incidents)
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving incidents: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving incidents: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stats/{profile_id}")
async def get_safety_stats(
    profile_id: str,
    session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Get safety statistics for a profile

    [LOCKED] SECURED: Parents can only view their own children's stats, admins can view all
    """
    try:
        stats = safety_monitor.get_profile_statistics(profile_id)

        # Audit log
        audit_log('read', 'safety_stats', profile_id, session)

        return stats
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving safety stats: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving safety stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class FalsePositiveSubmission(BaseModel):
    profile_id: str
    message_text: str
    block_reason: str
    triggered_keywords: list[str] = []
    educator_note: str = ""


@router.post("/false-positive")
async def submit_false_positive(
    body: FalsePositiveSubmission,
    session: AuthSession = Depends(get_current_session),
):
    """
    Submit a false positive report for a blocked message.

    [LOCKED] SECURED: Must be authenticated as parent or admin.
    """
    import json
    try:
        # Verify caller is parent or admin (not a student session)
        if session.role not in ("parent", "admin"):
            raise HTTPException(status_code=403, detail="Parent or admin access required")

        fp_id = auth_manager.db.insert_false_positive(
            profile_id=body.profile_id,
            message_text=body.message_text,
            block_reason=body.block_reason,
            triggered_keywords=json.dumps(body.triggered_keywords),
            educator_note=body.educator_note or None,
        )

        audit_log('create', 'false_positive_report', body.profile_id, session)

        return {"success": True, "id": fp_id}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error submitting false positive: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error submitting false positive: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
