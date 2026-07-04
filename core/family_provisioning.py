"""Atomic family provisioning: parent dashboard account + per-child OWUI chat
login + linked child profile, with compensating rollback.

Parents have NO OWUI account (they don't chat). Each child gets one OWUI login
whose user id is stored on child_profiles.owui_user_id; the safety proxy
resolves the child's profile from that.  child_profiles.parent_id groups
children under the parent's accounts.parent_id.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.age_verification import calculate_age_from_birthdate
from core.authentication import auth_manager
from core.profile_manager import ProfileManager
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)


class FamilyProvisioningError(Exception):
    """Raised when a family could not be fully provisioned (all work rolled back)."""


@dataclass
class ParentInput:
    name: str
    email: str
    phone: Optional[str] = None


@dataclass
class ChildInput:
    name: str
    birthdate: str  # ISO YYYY-MM-DD
    grade: str


@dataclass
class ProvisionedChild:
    name: str
    owui_username: str
    owui_email: str
    owui_password: str
    owui_user_id: str
    profile_id: str


@dataclass
class FamilyResult:
    parent_id: str
    parent_username: str
    parent_temp_password: str
    children: List[ProvisionedChild] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "family"


def _gen_password() -> str:
    # Strong enough for auth_manager password-strength check; never logged.
    return secrets.token_urlsafe(12) + "aA1!"


def _default_owui_create(*args, **kwargs):
    from api.routes.admin import _common

    return _common._owui_create_user(*args, **kwargs)


def _default_owui_delete(*args, **kwargs):
    from api.routes.admin import _common

    return _common._owui_delete_user(*args, **kwargs)


def provision_family(
    db,
    open_webui_url: str,
    owui_token: str,
    parent: ParentInput,
    children: List[ChildInput],
    *,
    owui_create: Callable = _default_owui_create,
    owui_delete: Callable = _default_owui_delete,
) -> FamilyResult:
    """Atomically provision a family: parent account + OWUI logins + profiles.

    On any failure every created resource is rolled back (OWUI logins deleted,
    DB rows removed) before raising FamilyProvisioningError.
    """
    pm = ProfileManager(db)
    created_owui_ids: List[str] = []
    parent_created = False
    parent_username = parent.email

    def _rollback():
        for uid in created_owui_ids:
            try:
                owui_delete(open_webui_url, owui_token, uid)
            except Exception as e:  # best-effort cleanup
                logger.warning(
                    "rollback: OWUI delete %r failed: %s",
                    sanitize_log_value(uid),
                    e,
                )
        try:
            # Delete any child profiles created under this parent.
            db.execute_update(
                "DELETE FROM child_profiles WHERE parent_id IN "
                "(SELECT parent_id FROM accounts WHERE username = ?)",
                (parent_username,),
            )
        except Exception as e:
            logger.error("rollback: DB child_profiles cleanup failed: %s", e)
        if parent_created:
            try:
                db.execute_update(
                    "DELETE FROM accounts WHERE username = ?",
                    (parent_username,),
                )
            except Exception as e:
                logger.error("rollback: DB accounts cleanup failed: %s", e)

    try:
        # 1. Parent dashboard account (temp password; admin hands it off).
        #    Parents get NO OWUI account — they use the dashboard, not chat.
        temp_password = _gen_password()
        ok, err = auth_manager.create_parent_account(
            username=parent_username,
            password=temp_password,
            email=parent.email,
            role="parent",
        )
        if not ok:
            raise FamilyProvisioningError(f"parent account: {err}")
        parent_created = True

        rows = db.execute_query(
            "SELECT parent_id FROM accounts WHERE username = ?",
            (parent_username,),
        )
        if not rows:
            raise FamilyProvisioningError("parent account not found after creation")
        parent_id = rows[0]["parent_id"] if isinstance(rows[0], dict) else rows[0][0]

        if parent.phone:
            db.execute_update(
                "UPDATE accounts SET phone = ? WHERE parent_id = ?",
                (parent.phone, parent_id),
            )

        # 2 + 3. Each child: OWUI chat login then linked profile.
        slug = _slug(parent.name)
        result = FamilyResult(
            parent_id=parent_id,
            parent_username=parent_username,
            parent_temp_password=temp_password,
        )
        used_usernames: set = set()
        for child in children:
            base = f"{slug}-{_slug(child.name)}"
            uname = base
            i = 1
            while uname in used_usernames:
                i += 1
                uname = f"{base}-{i}"
            used_usernames.add(uname)
            cemail = f"{uname}@snflwr.local"
            cpw = _gen_password()

            uid, cerr = owui_create(open_webui_url, owui_token, child.name, cemail, cpw)
            if not uid:
                raise FamilyProvisioningError(
                    f"OWUI login for {sanitize_log_value(child.name)!r}: {cerr}"
                )
            created_owui_ids.append(uid)

            # calculate_age_from_birthdate raises ValueError on bad birthdate —
            # caught by the outer except and converted to FamilyProvisioningError.
            age = calculate_age_from_birthdate(child.birthdate)
            profile = pm.create_profile(
                parent_id=parent_id,
                name=child.name,
                age=age,
                grade=child.grade,
                owui_user_id=uid,
                birthdate=child.birthdate,
            )
            result.children.append(
                ProvisionedChild(
                    name=child.name,
                    owui_username=uname,
                    owui_email=cemail,
                    owui_password=cpw,
                    owui_user_id=uid,
                    profile_id=profile.profile_id,
                )
            )

        logger.info(
            "Provisioned family for parent %s with %d child(ren)",
            sanitize_log_value(parent_username),
            len(result.children),
        )
        return result

    except FamilyProvisioningError:
        _rollback()
        raise
    except Exception as e:
        _rollback()
        raise FamilyProvisioningError(f"unexpected error: {e}") from e
