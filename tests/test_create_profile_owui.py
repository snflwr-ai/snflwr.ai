# tests/test_create_profile_owui.py
"""Unit tests for create_profile accepting owui_user_id + birthdate.

Runs on plain SQLite (DB_TYPE=sqlite DB_ENCRYPTION_ENABLED=false).
Uses a per-test isolated DB so: (a) data never bleeds across runs,
(b) migration 0003's partial UNIQUE index on owui_user_id is always present.
"""
import pytest
from pathlib import Path

from core.profile_manager import ProfileManager
from core.profile_manager.models import ProfileError


@pytest.fixture
def fresh_db(tmp_path):
    """A fully-migrated, isolated SQLite database per test."""
    db_path = tmp_path / "test_cp_owui.db"
    from storage.database import DatabaseManager
    from database.migrations import runner

    mgr = DatabaseManager(db_path=db_path, db_type="sqlite")
    runner.upgrade("head", manager=mgr)
    return mgr


@pytest.fixture
def pm(fresh_db):
    return ProfileManager(fresh_db)


def _mk_parent(db, pid):
    """Insert a minimal-but-valid accounts row for the given parent_id."""
    db.execute_update(
        "INSERT OR IGNORE INTO accounts "
        "(parent_id, username, password_hash, device_id, role, created_at, is_active) "
        "VALUES (?, ?, 'test-hash', ?, 'parent', CURRENT_TIMESTAMP, 1)",
        (pid, f"user-{pid}", f"device-{pid}"),
    )
    return pid


def test_owui_user_id_and_birthdate_persisted(pm, fresh_db):
    parent_id = _mk_parent(fresh_db, "par-cp-1")
    prof = pm.create_profile(
        parent_id=parent_id,
        name="Rey",
        age=12,
        grade="6",
        owui_user_id="owui-rey",
        birthdate="2013-05-01",
    )
    rows = fresh_db.execute_query(
        "SELECT owui_user_id, birthdate, encrypted_birthdate, name, encrypted_name, name_hash "
        "FROM child_profiles WHERE profile_id = ?",
        (prof.profile_id,),
    )
    row = rows[0]

    def col(k, i):
        return row[k] if isinstance(row, dict) else row[i]

    from core.profile_manager import field_crypto

    assert col("owui_user_id", 0) == "owui-rey"
    # M5: name + birthdate PII columns are redacted; the real values live encrypted
    # (protected at rest even in Postgres mode, where SQLCipher isn't used).
    assert col("birthdate", 1) is None
    assert (
        field_crypto.decrypt_birthdate(col("encrypted_birthdate", 2), None)
        == "2013-05-01"
    )
    assert col("name", 3) == field_crypto.NAME_PLACEHOLDER
    assert field_crypto.decrypt_name(col("encrypted_name", 4), None) == "Rey"
    assert col("name_hash", 5) == field_crypto.hash_name("Rey")
    # And the read path decrypts transparently back to the real name.
    assert prof.name == "Rey"
    assert pm.get_profile(prof.profile_id).name == "Rey"


def test_owui_user_id_none_allows_multiple_profiles(pm, fresh_db):
    """NULL owui_user_id must NOT be constrained by the partial unique index."""
    parent_id = _mk_parent(fresh_db, "par-cp-null")
    pm.create_profile(parent_id=parent_id, name="Alex", age=10, grade="5")
    pm.create_profile(parent_id=parent_id, name="Bree", age=12, grade="7")
    # Both created without owui_user_id — no uniqueness error expected.


def test_duplicate_owui_user_id_rejected(pm, fresh_db):
    """Partial UNIQUE index on owui_user_id (WHERE NOT NULL) must reject duplicates."""
    parent_id = _mk_parent(fresh_db, "par-cp-2")
    pm.create_profile(
        parent_id=parent_id,
        name="Alice",
        age=14,
        grade="8",
        owui_user_id="owui-dup",
    )
    with pytest.raises(Exception):
        pm.create_profile(
            parent_id=parent_id,
            name="Blake",
            age=14,
            grade="8",
            owui_user_id="owui-dup",
        )
