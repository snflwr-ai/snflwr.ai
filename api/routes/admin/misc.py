"""Catch-all admin route — kept in its own submodule so it can be included LAST.

The ``/{admin_id}`` path must be registered after all specific routes so that
``/stats``, ``/accounts``, etc. match first.
"""

from fastapi import APIRouter, HTTPException, Depends

from storage.db_adapters import DB_ERRORS
from core.authentication import AuthSession
from api.middleware.auth import require_admin

from ._common import logger, _pkg, _to_dict

router = APIRouter()


@router.get("/{admin_id}")
async def get_admin(admin_id: str, session: AuthSession = Depends(require_admin)):
    """
    Get admin information by ID

    [LOCKED] SECURED: Admin-only access
    NOTE: This route MUST be defined last so /stats, /accounts etc. match first.
    """
    try:
        db = _pkg().DatabaseManager()

        admin = db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ? AND role = 'admin'", (admin_id,)
        )

        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")

        admin_data = _to_dict(admin[0])

        email_crypto = _pkg().get_email_crypto()
        decrypted_email = email_crypto.decrypt_email(admin_data["encrypted_email"])

        _pkg().audit_log("read", "admin", admin_id, session)

        return {
            "admin_id": admin_data["parent_id"],
            "email": decrypted_email,
            "name": admin_data.get("name"),
            "role": admin_data["role"],
            "created_at": admin_data["created_at"],
            "is_active": bool(admin_data.get("is_active", 0)),
        }

    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(f"Database error fetching admin: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error fetching admin: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
