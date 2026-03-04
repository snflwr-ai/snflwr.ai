"""
Tests for admin hard-delete endpoints in api/routes/admin.py

Covers:
    - DELETE /api/admin/accounts/{parent_id}   — single account
    - DELETE /api/admin/accounts               — batch accounts
    - DELETE /api/admin/profiles/{profile_id}  — single profile
    - DELETE /api/admin/profiles               — batch profiles
    - DELETE /api/admin/alerts                 — batch alerts
    - DELETE /api/admin/activity               — batch sessions
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.authentication import AuthSession
from api.routes.admin import (
    delete_account,
    batch_delete_accounts,
    delete_profile,
    batch_delete_profiles,
    batch_delete_alerts,
    batch_delete_activity,
)


@pytest.fixture
def admin_session():
    return AuthSession(
        user_id="a" * 32,
        role="admin",
        session_token="tok_admin",
        email="admin@test.com",
    )


@pytest.fixture
def mock_db():
    return MagicMock()


# ---------------------------------------------------------------------------
# Single account delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_account_success(admin_session, mock_db):
    mock_db.execute_query.return_value = [{"parent_id": "pid1"}]
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await delete_account("pid1", admin_session)
    assert result["success"] is True
    assert result["deleted"] == "pid1"
    mock_db.execute_write.assert_called_once()


@pytest.mark.asyncio
async def test_delete_account_not_found(admin_session, mock_db):
    mock_db.execute_query.return_value = []
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await delete_account("missing", admin_session)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Batch account delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_delete_accounts_success(admin_session, mock_db):
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await batch_delete_accounts(["pid1", "pid2"], admin_session)
    assert result["deleted"] == 2
    mock_db.execute_write.assert_called_once()


@pytest.mark.asyncio
async def test_batch_delete_accounts_empty(admin_session):
    with pytest.raises(HTTPException) as exc_info:
        await batch_delete_accounts([], admin_session)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Single profile delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_profile_success(admin_session, mock_db):
    mock_db.execute_query.return_value = [{"profile_id": "prof1", "owui_user_id": None}]
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await delete_profile("prof1", admin_session)
    assert result["success"] is True
    assert result["deleted"] == "prof1"


@pytest.mark.asyncio
async def test_delete_profile_not_found(admin_session, mock_db):
    mock_db.execute_query.return_value = []
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await delete_profile("missing", admin_session)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Batch profile delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_delete_profiles_success(admin_session, mock_db):
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await batch_delete_profiles(["p1", "p2", "p3"], admin_session)
    assert result["deleted"] == 3


@pytest.mark.asyncio
async def test_batch_delete_profiles_empty(admin_session):
    with pytest.raises(HTTPException) as exc_info:
        await batch_delete_profiles([], admin_session)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Batch alert delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_delete_alerts_success(admin_session, mock_db):
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await batch_delete_alerts([1, 2], admin_session)
    assert result["deleted"] == 2


@pytest.mark.asyncio
async def test_batch_delete_alerts_empty(admin_session):
    with pytest.raises(HTTPException) as exc_info:
        await batch_delete_alerts([], admin_session)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Batch activity (session) delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_delete_activity_success(admin_session, mock_db):
    with patch("api.routes.admin.DatabaseManager", return_value=mock_db), \
         patch("api.routes.admin.audit_log"):
        result = await batch_delete_activity(["s1", "s2"], admin_session)
    assert result["deleted"] == 2


@pytest.mark.asyncio
async def test_batch_delete_activity_empty(admin_session):
    with pytest.raises(HTTPException) as exc_info:
        await batch_delete_activity([], admin_session)
    assert exc_info.value.status_code == 400
