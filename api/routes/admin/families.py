"""Admin endpoint: provision a whole family (parent + per-child OWUI logins +
linked child profiles) in one atomic call.

The parent's Snflwr dashboard account and each child's Open WebUI chat login are
created together.  On success the response hands the admin the parent's temporary
password and every child's OWUI login credentials.  On any failure
``FamilyProvisioningError`` is re-raised as HTTP 400 (all work has already been
rolled back by the provisioning layer).
"""

import importlib
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import require_admin
from api.routes.admin._common import _get_owui_token
from config import system_config
from core.authentication import AuthSession
from core.family_provisioning import (
    ChildInput,
    FamilyProvisioningError,
    ParentInput,
    provision_family,
)
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class ParentPayload(BaseModel):
    """Parent account fields for the family provisioning request."""

    name: str
    email: str
    phone: Optional[str] = None


class ChildPayload(BaseModel):
    """Per-child fields for the family provisioning request."""

    name: str
    birthdate: str  # ISO YYYY-MM-DD
    grade: str


class FamilyPayload(BaseModel):
    """Top-level request body for POST /api/admin/families."""

    parent: ParentPayload
    children: List[ChildPayload] = []


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post("/families")
async def create_family(
    payload: FamilyPayload,
    session: AuthSession = Depends(require_admin),
):
    """Atomically provision a parent account + per-child OWUI logins + profiles.

    Returns parent credentials and each child's Open WebUI login on success.
    Raises HTTP 400 with ``{detail}`` if provisioning fails (all work rolled back).
    """
    # Look up symbols through the package namespace so test patches propagate.
    _pkg = importlib.import_module("api.routes.admin")
    db = _pkg.DatabaseManager()

    open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
    owui_token = _get_owui_token(session)

    logger.info(
        "Admin %s provisioning family for parent email %s",
        sanitize_log_value(session.user_id),
        sanitize_log_value(payload.parent.email),
    )

    try:
        result = provision_family(
            db,
            open_webui_url,
            owui_token,
            ParentInput(
                name=payload.parent.name,
                email=payload.parent.email,
                phone=payload.parent.phone,
            ),
            [
                ChildInput(name=c.name, birthdate=c.birthdate, grade=c.grade)
                for c in payload.children
            ],
        )
    except FamilyProvisioningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "parent_id": result.parent_id,
        "parent_username": result.parent_username,
        "parent_temp_password": result.parent_temp_password,
        "children": [
            {
                "name": c.name,
                "owui_username": c.owui_username,
                "owui_email": c.owui_email,
                "owui_password": c.owui_password,
                "profile_id": c.profile_id,
            }
            for c in result.children
        ],
    }
