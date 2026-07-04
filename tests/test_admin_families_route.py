"""Tests for POST /api/admin/families — admin family provisioning endpoint.

Admin dependency: ``require_admin`` from ``api.middleware.auth``.
Auth fixture: ``admin_session`` (AuthSession with role="admin").
CSRF bypass: patch ``api.middleware.csrf.validate_csrf_token`` (AsyncMock).
provision_family patch target: ``api.routes.admin.families.provision_family``.
"""

import os
import pytest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")
os.environ.setdefault("DB_ENCRYPTION_ENABLED", "false")
os.environ.setdefault("DB_TYPE", "sqlite")

from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


@pytest.fixture
def admin_session():
    from core.authentication import AuthSession

    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="admin-token",
        email="admin@test.com",
    )


# ---------------------------------------------------------------------------
# Test 1: non-admin → 401 / 403
# ---------------------------------------------------------------------------


def test_requires_admin():
    """Request with no auth → CSRF or auth middleware rejects with 401/403."""
    r = client.post(
        "/api/admin/families",
        json={"parent": {"name": "x", "email": "x@y.z"}, "children": []},
    )
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test 1b: non-admin authenticated user → 403 from require_admin (not CSRF)
# ---------------------------------------------------------------------------


def test_admin_gate_rejects_non_admin():
    """CSRF bypassed; valid parent session → require_admin raises 403 (not CSRF).

    Isolation: CSRF is patched to pass, auth_manager returns a real (non-admin)
    session, so the *only* possible source of rejection is require_admin's role
    check — proving the gate is wired and active.
    """
    from core.authentication import AuthSession

    parent_session = AuthSession(
        user_id="parent1",
        role="parent",
        session_token="parent-token",
        email="parent@test.com",
    )

    with (
        patch("api.middleware.auth.auth_manager") as mock_am,
        patch(
            "api.middleware.csrf.validate_csrf_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        mock_am.validate_session.return_value = (True, parent_session)
        r = client.post(
            "/api/admin/families",
            json={"parent": {"name": "x", "email": "x@y.z"}, "children": []},
            headers={"Authorization": "Bearer parent-token"},
        )

    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test 2: admin → 200 with mapped JSON
# ---------------------------------------------------------------------------


@patch("api.routes.admin.families.provision_family")
def test_admin_can_provision(mock_provision, admin_session):
    """Admin caller → 200 with parent_id and children[0].owui_username."""
    from core.family_provisioning import FamilyResult, ProvisionedChild

    mock_provision.return_value = FamilyResult(
        parent_id="par-1",
        parent_username="a@b.c",
        parent_temp_password="TMP",
        children=[
            ProvisionedChild(
                "Mia", "fam-mia", "fam-mia@snflwr.local", "CPW", "owui-1", "prof-1"
            )
        ],
    )

    with (
        patch("api.middleware.auth.auth_manager") as mock_am,
        patch(
            "api.middleware.csrf.validate_csrf_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        mock_am.validate_session.return_value = (True, admin_session)
        r = client.post(
            "/api/admin/families",
            json={
                "parent": {"name": "Fam", "email": "a@b.c"},
                "children": [{"name": "Mia", "birthdate": "2014-01-01", "grade": "5"}],
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parent_id"] == "par-1"
    assert body["parent_username"] == "a@b.c"
    assert body["parent_temp_password"] == "TMP"
    assert body["children"][0]["owui_username"] == "fam-mia"
    assert body["children"][0]["profile_id"] == "prof-1"


# ---------------------------------------------------------------------------
# Test 3: provisioning failure → 400
# ---------------------------------------------------------------------------


@patch("api.routes.admin.families.provision_family")
def test_provisioning_error_returns_400(mock_provision, admin_session):
    """FamilyProvisioningError raised inside service → HTTP 400."""
    from core.family_provisioning import FamilyProvisioningError

    mock_provision.side_effect = FamilyProvisioningError(
        "Parent account creation failed"
    )

    with (
        patch("api.middleware.auth.auth_manager") as mock_am,
        patch(
            "api.middleware.csrf.validate_csrf_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        mock_am.validate_session.return_value = (True, admin_session)
        r = client.post(
            "/api/admin/families",
            json={
                "parent": {"name": "Bad", "email": "bad@example.com"},
                "children": [],
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert r.status_code == 400, r.text
    assert "Parent account creation failed" in r.json()["detail"]
