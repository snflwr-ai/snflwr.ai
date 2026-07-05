"""Tests for _get_profile_for_user resolving by owui_user_id.

Runs on plain SQLite: DB_TYPE=sqlite DB_ENCRYPTION_ENABLED=false pytest tests/test_proxy_owui_resolve.py -v
"""
import asyncio

from core.authentication import auth_manager
from api.routes.ollama_proxy import profile as prof_mod

# Fixed identifiers — delete-before-insert makes reruns safe.
_PARENT_ID = "par-pxtest-1"
_OWUI_ID = "owui-proxy-pxtest"
_PROFILE_ID = "prof-proxy-pxtest"


def _mk_parent(pid):
    """Insert a minimal-but-valid accounts row; OR IGNORE so reruns are safe."""
    auth_manager.db.execute_update(
        "INSERT OR IGNORE INTO accounts "
        "(parent_id, username, password_hash, device_id, role, created_at, is_active) "
        "VALUES (?, ?, 'test-hash', ?, 'parent', CURRENT_TIMESTAMP, 1)",
        (pid, f"user-{pid}", f"device-{pid}"),
    )
    return pid


def _seed_child(profile_id, parent_id, owui_user_id):
    """Delete any stale rows for this owui_user_id, then insert fresh."""
    auth_manager.db.execute_update(
        "DELETE FROM child_profiles WHERE owui_user_id = ?",
        (owui_user_id,),
    )
    auth_manager.db.execute_update(
        "INSERT INTO child_profiles "
        "(profile_id, parent_id, name, age, grade, owui_user_id, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)",
        (profile_id, parent_id, f"child-{profile_id}", 15, "9", owui_user_id),
    )


def test_resolves_by_owui_user_id():
    parent_id = _mk_parent(_PARENT_ID)
    _seed_child(_PROFILE_ID, parent_id, _OWUI_ID)
    got = asyncio.run(prof_mod._get_profile_for_user(_OWUI_ID))
    assert got == _PROFILE_ID


def test_unprovisioned_owui_id_fails_closed():
    got = asyncio.run(prof_mod._get_profile_for_user("owui-nobody"))
    assert got == "safety_required_owui-nobody"


def test_none_user_fails_closed():
    got = asyncio.run(prof_mod._get_profile_for_user(None))
    assert got == "safety_required_unknown"
