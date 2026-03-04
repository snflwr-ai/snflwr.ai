"""
Analytics API Routes
Usage analytics and reporting for parent dashboard

🔒 SECURED: All routes require authentication
- Parents can only access their own children's analytics
- Admins can access all analytics
"""

from fastapi import APIRouter, HTTPException, Depends

from core.session_manager import session_manager
from core.authentication import auth_manager, AuthSession
from storage.db_adapters import DB_ERRORS
from api.middleware.auth import (
    get_current_session,
    VerifyProfileAccess,
    VerifySessionAccess,
    audit_log
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/usage/{profile_id}")
async def get_usage_stats(
    profile_id: str,
    days: int = 7,
    session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Get usage statistics for a profile

    🔒 SECURED: Parents can only view their own children's usage, admins can view all
    """
    try:
        stats = session_manager.get_usage_stats(profile_id, days)

        # Audit log
        audit_log('read', 'usage_stats', profile_id, session)

        return stats
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving usage stats: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving usage stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity/{profile_id}")
async def get_activity_log(
    profile_id: str,
    limit: int = 50,
    session: AuthSession = Depends(VerifyProfileAccess)
):
    """
    Get activity log for a profile

    🔒 SECURED: Parents can only view their own children's activity, admins can view all
    """
    try:
        sessions = session_manager.get_session_history(profile_id, limit)

        # Audit log
        audit_log('read', 'activity_log', profile_id, session)

        return {
            "sessions": [s.to_dict() for s in sessions],
            "count": len(sessions)
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving activity log: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving activity log: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/messages/{session_id}")
async def get_session_messages(
    session_id: str,
    auth_session: AuthSession = Depends(VerifySessionAccess)
):
    """
    Get all messages in a session

    🔒 SECURED: Parents can only view their own children's messages, admins can view all
    """
    try:
        # Session ownership verified by VerifySessionAccess dependency
        messages = session_manager.get_messages(session_id)

        # Audit log
        audit_log('read', 'session_messages', session_id, auth_session)

        return {
            "messages": messages,
            "count": len(messages)
        }
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error retrieving messages: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error retrieving messages: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
