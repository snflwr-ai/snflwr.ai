# tests/test_profile_owui_lookup.py
"""Unit tests for ProfileManager.get_profile_by_owui_user_id.

Runs on plain SQLite (DB_TYPE=sqlite DB_ENCRYPTION_ENABLED=false).
Profile rows are seeded via direct SQL inserts because create_profile()
does not yet accept an owui_user_id kwarg (that lands in Task 3).
"""

import pytest
from core.authentication import auth_manager
from core.profile_manager import ProfileManager


@pytest.fixture
def pm():
    return ProfileManager(auth_manager.db)


def _mk_parent(pid):
    """Insert a minimal-but-valid accounts row for the given parent_id."""
    auth_manager.db.execute_update(
        "INSERT OR IGNORE INTO accounts "
        "(parent_id, username, password_hash, device_id, role, created_at, is_active) "
        "VALUES (?, ?, 'test-hash', ?, 'parent', CURRENT_TIMESTAMP, 1)",
        (pid, f"user-{pid}", f"device-{pid}"),
    )
    return pid


def test_lookup_returns_profile_by_owui_id(pm):
    parent_id = _mk_parent("par-lk-1")
    auth_manager.db.execute_update(
        "INSERT OR IGNORE INTO child_profiles "
        "(profile_id, parent_id, name, age, grade, owui_user_id, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)",
        ("prof-owui-ada", parent_id, "Ada", 14, "8", "owui-ada"),
    )
    found = pm.get_profile_by_owui_user_id("owui-ada")
    assert found is not None
    assert found.profile_id == "prof-owui-ada"


def test_lookup_miss_returns_none(pm):
    assert pm.get_profile_by_owui_user_id("owui-does-not-exist") is None


def test_lookup_inactive_profile_returns_none(pm):
    """is_active = 0 rows must be invisible to the resolver."""
    parent_id = _mk_parent("par-lk-3")
    auth_manager.db.execute_update(
        "INSERT OR IGNORE INTO child_profiles "
        "(profile_id, parent_id, name, age, grade, owui_user_id, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)",
        ("prof-owui-inactive", parent_id, "Zed", 10, "5", "owui-inactive-zed"),
    )
    assert pm.get_profile_by_owui_user_id("owui-inactive-zed") is None
