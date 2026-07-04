"""Tests for core.family_provisioning — atomic family provisioning service.

All tests run on the shared auth_manager.db (plain SQLite via conftest).
A cleanup fixture deletes any pre-existing accounts/profiles for the test
emails before each test so the suite is idempotent on re-runs.
"""
import pytest
from core.authentication import auth_manager
from core.family_provisioning import (
    ChildInput,
    FamilyProvisioningError,
    ParentInput,
    provision_family,
)

DB = auth_manager.db

# Emails used across the test module — cleaned up before every test.
_TEST_EMAILS = [
    "sarah-happy@example.com",
    "bad-rollback@example.com",
    "dup@example.com",
]


@pytest.fixture(autouse=True)
def _clean_test_accounts():
    """Delete any pre-existing accounts/profiles for the test emails so that
    re-runs don't fail with 'Username already exists'."""
    for email in _TEST_EMAILS:
        try:
            DB.execute_update(
                "DELETE FROM child_profiles WHERE parent_id IN "
                "(SELECT parent_id FROM accounts WHERE username = ?)",
                (email,),
            )
            DB.execute_update(
                "DELETE FROM accounts WHERE username = ?",
                (email,),
            )
        except Exception:
            pass
    yield


def _fake_owui_create_ok(created):
    def _c(url, token, name, email, password):
        uid = f"owui-{len(created)}"
        created.append(uid)
        return uid, None
    return _c


def _fake_owui_delete(deleted):
    def _d(url, token, owui_user_id):
        deleted.append(owui_user_id)
    return _d


def test_happy_path_creates_parent_children_and_links():
    created, deleted = [], []
    res = provision_family(
        DB, "http://owui", "tok",
        ParentInput(name="Sarah Lee", email="sarah-happy@example.com"),
        [ChildInput(name="Mia", birthdate="2014-03-02", grade="5"),
         ChildInput(name="Leo", birthdate="2010-09-09", grade="8")],
        owui_create=_fake_owui_create_ok(created),
        owui_delete=_fake_owui_delete(deleted),
    )
    assert res.parent_id
    assert len(res.children) == 2
    assert deleted == []  # no rollback on success
    # each child profile is linked by owui_user_id and grouped under the parent
    for ch in res.children:
        rows = DB.execute_query(
            "SELECT parent_id, owui_user_id FROM child_profiles WHERE profile_id = ?",
            (ch.profile_id,),
        )
        row = rows[0]
        assert (row["parent_id"] if isinstance(row, dict) else row[0]) == res.parent_id
        assert (row["owui_user_id"] if isinstance(row, dict) else row[1]) == ch.owui_user_id


def test_rollback_when_second_child_owui_fails():
    created, deleted = [], []

    def _create_fail_second(url, token, name, email, password):
        if len(created) >= 1:
            return None, "Open WebUI unreachable"
        uid = f"owui-{len(created)}"
        created.append(uid)
        return uid, None

    before = DB.execute_query("SELECT COUNT(*) AS c FROM child_profiles")[0]
    before_c = before["c"] if isinstance(before, dict) else before[0]

    with pytest.raises(FamilyProvisioningError):
        provision_family(
            DB, "http://owui", "tok",
            ParentInput(name="Bad Family", email="bad-rollback@example.com"),
            [ChildInput(name="One", birthdate="2015-01-01", grade="4"),
             ChildInput(name="Two", birthdate="2015-01-01", grade="4")],
            owui_create=_create_fail_second,
            owui_delete=_fake_owui_delete(deleted),
        )

    # the one created OWUI login was deleted, and no profiles/parent persisted
    assert deleted == ["owui-0"]
    after = DB.execute_query("SELECT COUNT(*) AS c FROM child_profiles")[0]
    after_c = after["c"] if isinstance(after, dict) else after[0]
    assert after_c == before_c
    assert DB.execute_query(
        "SELECT parent_id FROM accounts WHERE email = ?", ("bad-rollback@example.com",)
    ) == []


def test_duplicate_parent_email_raises():
    provision_family(
        DB, "http://owui", "tok",
        ParentInput(name="Dup One", email="dup@example.com"),
        [], owui_create=_fake_owui_create_ok([]), owui_delete=_fake_owui_delete([]),
    )
    with pytest.raises(FamilyProvisioningError):
        provision_family(
            DB, "http://owui", "tok",
            ParentInput(name="Dup Two", email="dup@example.com"),
            [], owui_create=_fake_owui_create_ok([]), owui_delete=_fake_owui_delete([]),
        )
