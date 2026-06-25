"""Admin dashboard routes for child/student profiles and bulk roster import."""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.auth import require_admin
from config import system_config
from core.authentication import AuthSession
from storage.db_adapters import DB_ERRORS
from utils.logger import sanitize_log_value

from ._common import (
    _PROFILE_UPDATE_COLUMNS,
    BulkImportRequest,
    CreateProfileRequest,
    UpdateProfileAdminRequest,
    _pkg,
    _to_dict,
    logger,
)

router = APIRouter()


@router.post("/profiles")
async def create_profile(
    request: CreateProfileRequest, session: AuthSession = Depends(require_admin)
):
    """Create a new student profile with Open WebUI login"""

    try:
        db = _pkg().DatabaseManager()
        open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")

        # Verify parent exists
        parent = db.execute_query(
            "SELECT parent_id, name FROM accounts WHERE parent_id = ?",
            (request.parent_id,),
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Parent account not found")

        # Create Open WebUI account for the student (so they can log in)
        owui_user_id = None
        if request.email and request.password:
            owui_token = _pkg()._get_owui_token(session)
            owui_user_id, error = _pkg()._owui_create_user(
                open_webui_url,
                owui_token,
                request.name,
                request.email,
                request.password,
            )
            if error:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to create Open WebUI account: {error}",
                )
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
                profile_id,
                request.parent_id,
                request.name,
                request.age,
                request.grade_level,
                request.grade_level,
                now,
                request.daily_time_limit_minutes or 120,
                owui_user_id,
            ),
        )

        _pkg().audit_log("create", "profile", profile_id, session)

        msg = "Student profile created"
        if owui_user_id:
            msg += " with Open WebUI login"

        return {
            "success": True,
            "profile_id": profile_id,
            "owui_user_id": owui_user_id,
            "message": msg,
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
    request: BulkImportRequest, session: AuthSession = Depends(require_admin)
):
    """Bulk provision students from a school roster.

    Creates an Open WebUI account and a linked snflwr profile for each student.
    Fail-forward: one student failing does not abort the rest.

    For students under 13, ``accept_institutional_coppa`` must be True — the
    admin accepts COPPA responsibility on behalf of the institution.
    """
    db = _pkg().DatabaseManager()
    open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
    age_manager = _pkg().AgeVerificationManager(db)

    created = []
    failed = []
    owui_token = _pkg()._get_owui_token(session)

    for s in request.students:
        # COPPA gate: under-13 requires institutional consent flag
        if s.age < 13 and not request.accept_institutional_coppa:
            failed.append(
                {
                    "email": s.email,
                    "error": "Student is under 13 — set accept_institutional_coppa=true to proceed",
                }
            )
            continue

        # Create Open WebUI account
        owui_user_id, error = _pkg()._owui_create_user(
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
                    profile_id,
                    session.user_id,
                    s.name,
                    s.age,
                    s.grade_level,
                    s.grade_level,
                    now,
                    owui_user_id,
                ),
            )
        except DB_ERRORS as e:
            logger.error(f"DB error creating profile for {s.email!r}: {e}")
            failed.append(
                {"email": s.email, "error": "Database error creating profile"}
            )
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

        logger.info(
            f"Imported student {s.email!r} → profile {profile_id!r}, owui {owui_user_id!r}"
        )
        created.append(s.email)

    _pkg().audit_log(
        "create", "student_bulk_import", f"imported={len(created)}", session
    )

    return {"imported": len(created), "failed": failed}


@router.get("/students")
async def list_students(
    session: AuthSession = Depends(require_admin),
    limit: int = 200,
    offset: int = 0,
):
    """List child profiles owned by this admin with Open WebUI link status."""
    try:
        db = _pkg().DatabaseManager()
        rows = db.execute_query(
            "SELECT profile_id, name, age, grade_level, owui_user_id, "
            "parental_consent_given, coppa_verified, is_active, created_at "
            "FROM child_profiles "
            "WHERE parent_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (session.user_id, limit, offset),
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
    session: AuthSession = Depends(require_admin), limit: int = 100, offset: int = 0
):
    """List all child profiles with parent info"""
    try:
        db = _pkg().DatabaseManager()
        email_crypto = _pkg().get_email_crypto()

        profiles = db.execute_query(
            "SELECT cp.*, a.name as parent_name, "
            "a.encrypted_email as parent_encrypted_email "
            "FROM child_profiles cp "
            "LEFT JOIN accounts a ON cp.parent_id = a.parent_id "
            "ORDER BY cp.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

        result = []
        for row in profiles:
            p = _to_dict(row)
            parent_email = ""
            try:
                if p.get("parent_encrypted_email"):
                    parent_email = email_crypto.decrypt_email(
                        p["parent_encrypted_email"]
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt parent email for profile {p.get('profile_id', '?')}: {type(e).__name__}"
                )
                parent_email = "[encrypted]"

            result.append(
                {
                    "profile_id": p["profile_id"],
                    "parent_id": p["parent_id"],
                    "parent_name": p.get("parent_name") or "",
                    "parent_email": parent_email,
                    "name": p["name"],
                    "age": p.get("age"),
                    "grade_level": p.get("grade_level") or p.get("grade") or "",
                    "is_active": bool(p.get("is_active", 0)),
                    "created_at": p.get("created_at"),
                    "last_active": p.get("last_active"),
                    "total_sessions": p.get("total_sessions", 0),
                    "total_questions": p.get("total_questions", 0),
                    "daily_time_limit_minutes": p.get("daily_time_limit_minutes", 0),
                    "tier": p.get("tier", "standard"),
                }
            )

        total = db.execute_query("SELECT COUNT(*) as c FROM child_profiles")

        _pkg().audit_log("list", "profiles", "all", session)

        return {"profiles": result, "total": total[0]["c"] if total else 0}
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
    session: AuthSession = Depends(require_admin),
):
    """Admin-level update of a child profile"""
    try:
        db = _pkg().DatabaseManager()

        existing = db.execute_query(
            "SELECT * FROM child_profiles WHERE profile_id = ?", (profile_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Profile not found")

        updates = []
        params: list = []

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
        used_columns = {u.split(" = ")[0] for u in updates}
        if not used_columns <= _PROFILE_UPDATE_COLUMNS:
            raise ValueError(
                f"Unexpected columns: {used_columns - _PROFILE_UPDATE_COLUMNS}"
            )

        params.append(profile_id)
        db.execute_write(
            f"UPDATE child_profiles SET {', '.join(updates)} " f"WHERE profile_id = ?",
            tuple(params),
        )

        _pkg().audit_log("update", "profile", profile_id, session)

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
    profile_id: str, session: AuthSession = Depends(require_admin)
):
    """Hard-delete a student profile and all its associated data (cascade)."""
    try:
        db = _pkg().DatabaseManager()
        existing = db.execute_query(
            "SELECT profile_id, owui_user_id FROM child_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Profile not found")
        owui_user_id = (
            existing[0]["owui_user_id"] if existing[0]["owui_user_id"] else None
        )
        db.execute_write(
            "DELETE FROM child_profiles WHERE profile_id = ?", (profile_id,)
        )
        # Best-effort: remove the corresponding Open WebUI account too.
        if owui_user_id:
            open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
            owui_token = _pkg()._get_owui_token(session)
            _pkg()._owui_delete_user(open_webui_url, owui_token, owui_user_id)
        _pkg().audit_log("delete", "profile", profile_id, session)
        return {"success": True, "deleted": profile_id}
    except HTTPException:
        raise
    except DB_ERRORS as e:
        logger.error(
            f"Database error deleting profile {sanitize_log_value(profile_id)!r}: {e}"
        )
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(
            f"Unexpected error deleting profile {sanitize_log_value(profile_id)!r}: {e}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/profiles")
async def batch_delete_profiles(
    ids: List[str], session: AuthSession = Depends(require_admin)
):
    """Hard-delete multiple student profiles by ID."""
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    try:
        db = _pkg().DatabaseManager()
        placeholders = ",".join("?" * len(ids))
        # Collect OWU user IDs before deleting.
        rows = db.execute_query(
            f"SELECT owui_user_id FROM child_profiles WHERE profile_id IN ({placeholders})",
            tuple(ids),
        )
        owui_ids = [r["owui_user_id"] for r in rows if r["owui_user_id"]]
        db.execute_write(
            f"DELETE FROM child_profiles WHERE profile_id IN ({placeholders})",
            tuple(ids),
        )
        # Best-effort: remove corresponding Open WebUI accounts.
        if owui_ids:
            open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
            owui_token = _pkg()._get_owui_token(session)
            for oid in owui_ids:
                _pkg()._owui_delete_user(open_webui_url, owui_token, oid)
        _pkg().audit_log("delete", "profiles_batch", f"count={len(ids)}", session)
        return {"success": True, "deleted": len(ids)}
    except DB_ERRORS as e:
        logger.error(f"Database error batch-deleting profiles: {e}")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    except Exception as e:
        logger.exception(f"Unexpected error batch-deleting profiles: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
