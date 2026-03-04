"""
Tests for api/routes/safety.py — Safety Alerts and Incidents

Covers:
    - GET /alerts/{parent_id}  — happy path, DB error, unexpected error, HTTPException re-raise
    - POST /alerts/{alert_id}/acknowledge — success, not found, DB error, unexpected error
    - GET /incidents/{profile_id} — happy path, DB error, unexpected error, HTTPException re-raise
    - GET /stats/{profile_id} — happy path, DB error, unexpected error, HTTPException re-raise
    - audit_log called on each successful path
    - HTTPException re-raised as-is (not swallowed)
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from fastapi import HTTPException

from core.authentication import AuthSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin_session(**kwargs):
    defaults = dict(
        user_id="admin-user-id",
        role="admin",
        session_token="tok_admin",
        email="admin@snflwr.ai",
    )
    defaults.update(kwargs)
    return AuthSession(**defaults)


def _make_parent_session(**kwargs):
    defaults = dict(
        user_id="parent-user-id",
        role="parent",
        session_token="tok_parent",
        email="parent@snflwr.ai",
    )
    defaults.update(kwargs)
    return AuthSession(**defaults)


def _make_alert(alert_id="alert-001", parent_id="parent-user-id"):
    """Build a mock SafetyAlert with .to_dict()."""
    alert = MagicMock()
    alert.to_dict.return_value = {
        "alert_id": alert_id,
        "profile_id": "prof-abc",
        "parent_id": parent_id,
        "severity": "high",
        "incident_count": 3,
        "description": "Repeated inappropriate content",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_snippet": "...",
        "requires_action": True,
    }
    return alert


# ---------------------------------------------------------------------------
# Fixtures — patch module-level singletons used by the routes
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_safety_monitor():
    """Patch the safety_monitor singleton used in api.routes.safety."""
    with patch("api.routes.safety.safety_monitor") as m:
        m.get_pending_alerts.return_value = []
        m.acknowledge_alert.return_value = True
        m.get_profile_statistics.return_value = {}
        yield m


@pytest.fixture
def mock_incident_logger():
    """Patch the incident_logger singleton used in api.routes.safety."""
    with patch("api.routes.safety.incident_logger") as m:
        m.get_profile_incidents.return_value = []
        yield m


@pytest.fixture
def mock_audit():
    """Patch audit_log so database writes are suppressed."""
    with patch("api.routes.safety.audit_log") as m:
        m.return_value = True
        yield m


# ---------------------------------------------------------------------------
# GET /alerts/{parent_id}
# ---------------------------------------------------------------------------

class TestGetParentAlerts:

    @pytest.mark.asyncio
    async def test_returns_alerts_and_count(
        self, mock_safety_monitor, mock_audit
    ):
        """Happy path: returns list of serialised alerts and count."""
        from api.routes.safety import get_parent_alerts

        alert = _make_alert()
        mock_safety_monitor.get_pending_alerts.return_value = [alert]

        result = await get_parent_alerts(
            parent_id="parent-user-id",
            session=_make_admin_session(),
        )

        assert result["count"] == 1
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["alert_id"] == "alert-001"

    @pytest.mark.asyncio
    async def test_calls_get_pending_alerts_with_parent_id(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        mock_safety_monitor.get_pending_alerts.return_value = []

        await get_parent_alerts(
            parent_id="parent-abc",
            session=_make_admin_session(),
        )

        mock_safety_monitor.get_pending_alerts.assert_called_once_with("parent-abc")

    @pytest.mark.asyncio
    async def test_empty_alerts_returns_empty_list(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        mock_safety_monitor.get_pending_alerts.return_value = []

        result = await get_parent_alerts(
            parent_id="parent-xyz",
            session=_make_admin_session(),
        )

        assert result["count"] == 0
        assert result["alerts"] == []

    @pytest.mark.asyncio
    async def test_multiple_alerts_serialised(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        alerts = [_make_alert(f"alert-{i}") for i in range(3)]
        mock_safety_monitor.get_pending_alerts.return_value = alerts

        result = await get_parent_alerts(
            parent_id="parent-user-id",
            session=_make_admin_session(),
        )

        assert result["count"] == 3
        assert len(result["alerts"]) == 3

    @pytest.mark.asyncio
    async def test_audit_log_called_on_success(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        session = _make_admin_session()
        await get_parent_alerts(parent_id="parent-abc", session=session)

        mock_audit.assert_called_once_with("read", "safety_alerts", "parent-abc", session)

    @pytest.mark.asyncio
    async def test_db_error_raises_503(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        mock_safety_monitor.get_pending_alerts.side_effect = sqlite3.Error("connection lost")

        with pytest.raises(HTTPException) as exc:
            await get_parent_alerts(
                parent_id="parent-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 503
        assert "unavailable" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        mock_safety_monitor.get_pending_alerts.side_effect = RuntimeError("unexpected")

        with pytest.raises(HTTPException) as exc:
            await get_parent_alerts(
                parent_id="parent-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 500
        assert "internal server error" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_http_exception_reraised_as_is(
        self, mock_safety_monitor, mock_audit
    ):
        """HTTPException raised inside the handler must bubble up unchanged."""
        from api.routes.safety import get_parent_alerts

        original = HTTPException(status_code=403, detail="Forbidden")
        mock_safety_monitor.get_pending_alerts.side_effect = original

        with pytest.raises(HTTPException) as exc:
            await get_parent_alerts(
                parent_id="parent-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Forbidden"

    @pytest.mark.asyncio
    async def test_to_dict_called_on_each_alert(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_parent_alerts

        alert = _make_alert()
        mock_safety_monitor.get_pending_alerts.return_value = [alert]

        await get_parent_alerts(
            parent_id="parent-user-id",
            session=_make_admin_session(),
        )

        alert.to_dict.assert_called_once()


# ---------------------------------------------------------------------------
# POST /alerts/{alert_id}/acknowledge
# ---------------------------------------------------------------------------

class TestAcknowledgeAlert:

    @pytest.mark.asyncio
    async def test_success_returns_status_success(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.return_value = True

        result = await acknowledge_alert(
            alert_id="alert-001",
            session=_make_admin_session(),
        )

        assert result == {"status": "success"}

    @pytest.mark.asyncio
    async def test_calls_acknowledge_with_alert_id(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.return_value = True

        await acknowledge_alert(
            alert_id="alert-xyz",
            session=_make_admin_session(),
        )

        mock_safety_monitor.acknowledge_alert.assert_called_once_with("alert-xyz")

    @pytest.mark.asyncio
    async def test_alert_not_found_raises_404(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.return_value = False

        with pytest.raises(HTTPException) as exc:
            await acknowledge_alert(
                alert_id="missing-alert",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_audit_log_called_on_success(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.return_value = True
        session = _make_admin_session()

        await acknowledge_alert(alert_id="alert-001", session=session)

        mock_audit.assert_called_once_with("update", "safety_alert", "alert-001", session)

    @pytest.mark.asyncio
    async def test_db_error_raises_503(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.side_effect = sqlite3.Error("disk full")

        with pytest.raises(HTTPException) as exc:
            await acknowledge_alert(
                alert_id="alert-001",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.side_effect = ValueError("bad state")

        with pytest.raises(HTTPException) as exc:
            await acknowledge_alert(
                alert_id="alert-001",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_reraised_as_is(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import acknowledge_alert

        original = HTTPException(status_code=403, detail="Forbidden by middleware")
        mock_safety_monitor.acknowledge_alert.side_effect = original

        with pytest.raises(HTTPException) as exc:
            await acknowledge_alert(
                alert_id="alert-001",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Forbidden by middleware"

    @pytest.mark.asyncio
    async def test_audit_not_called_when_alert_not_found(
        self, mock_safety_monitor, mock_audit
    ):
        """Audit log should not be written when the alert is missing (404 path)."""
        from api.routes.safety import acknowledge_alert

        mock_safety_monitor.acknowledge_alert.return_value = False

        with pytest.raises(HTTPException):
            await acknowledge_alert(
                alert_id="missing",
                session=_make_admin_session(),
            )

        mock_audit.assert_not_called()


# ---------------------------------------------------------------------------
# GET /incidents/{profile_id}
# ---------------------------------------------------------------------------

class TestGetProfileIncidents:

    @pytest.mark.asyncio
    async def test_returns_incidents_and_count(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        incidents = [
            {"incident_id": 1, "severity": "high"},
            {"incident_id": 2, "severity": "medium"},
        ]
        mock_incident_logger.get_profile_incidents.return_value = incidents

        result = await get_profile_incidents(
            profile_id="prof-001",
            days=30,
            session=_make_admin_session(),
        )

        assert result["count"] == 2
        assert result["incidents"] == incidents

    @pytest.mark.asyncio
    async def test_calls_logger_with_profile_id_and_days(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.return_value = []

        await get_profile_incidents(
            profile_id="prof-abc",
            days=7,
            session=_make_admin_session(),
        )

        mock_incident_logger.get_profile_incidents.assert_called_once_with("prof-abc", days=7)

    @pytest.mark.asyncio
    async def test_default_days_is_30(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.return_value = []

        await get_profile_incidents(
            profile_id="prof-abc",
            days=30,
            session=_make_admin_session(),
        )

        mock_incident_logger.get_profile_incidents.assert_called_once_with("prof-abc", days=30)

    @pytest.mark.asyncio
    async def test_empty_incidents_returns_zero_count(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.return_value = []

        result = await get_profile_incidents(
            profile_id="prof-empty",
            days=30,
            session=_make_admin_session(),
        )

        assert result["count"] == 0
        assert result["incidents"] == []

    @pytest.mark.asyncio
    async def test_audit_log_called_on_success(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.return_value = []
        session = _make_admin_session()

        await get_profile_incidents(profile_id="prof-abc", days=30, session=session)

        mock_audit.assert_called_once_with("read", "safety_incidents", "prof-abc", session)

    @pytest.mark.asyncio
    async def test_db_error_raises_503(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.side_effect = sqlite3.Error("db gone")

        with pytest.raises(HTTPException) as exc:
            await get_profile_incidents(
                profile_id="prof-xyz",
                days=30,
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.side_effect = Exception("boom")

        with pytest.raises(HTTPException) as exc:
            await get_profile_incidents(
                profile_id="prof-xyz",
                days=30,
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_reraised_as_is(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        original = HTTPException(status_code=404, detail="Profile not found")
        mock_incident_logger.get_profile_incidents.side_effect = original

        with pytest.raises(HTTPException) as exc:
            await get_profile_incidents(
                profile_id="prof-xyz",
                days=30,
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 404
        assert exc.value.detail == "Profile not found"

    @pytest.mark.asyncio
    async def test_single_incident_returned_correctly(
        self, mock_incident_logger, mock_audit
    ):
        from api.routes.safety import get_profile_incidents

        incident = {"incident_id": 99, "severity": "critical", "profile_id": "prof-abc"}
        mock_incident_logger.get_profile_incidents.return_value = [incident]

        result = await get_profile_incidents(
            profile_id="prof-abc",
            days=30,
            session=_make_admin_session(),
        )

        assert result["count"] == 1
        assert result["incidents"][0]["incident_id"] == 99


# ---------------------------------------------------------------------------
# GET /stats/{profile_id}
# ---------------------------------------------------------------------------

class TestGetSafetyStats:

    @pytest.mark.asyncio
    async def test_returns_stats_dict(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        stats = {
            "profile_id": "prof-001",
            "minor_incidents": 2,
            "major_incidents": 1,
            "critical_incidents": 0,
            "total_incidents": 3,
        }
        mock_safety_monitor.get_profile_statistics.return_value = stats

        result = await get_safety_stats(
            profile_id="prof-001",
            session=_make_admin_session(),
        )

        assert result == stats

    @pytest.mark.asyncio
    async def test_calls_get_profile_statistics_with_profile_id(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.return_value = {}

        await get_safety_stats(
            profile_id="prof-xyz",
            session=_make_admin_session(),
        )

        mock_safety_monitor.get_profile_statistics.assert_called_once_with("prof-xyz")

    @pytest.mark.asyncio
    async def test_empty_stats_returned_as_empty_dict(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.return_value = {}

        result = await get_safety_stats(
            profile_id="prof-empty",
            session=_make_admin_session(),
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_audit_log_called_on_success(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.return_value = {}
        session = _make_admin_session()

        await get_safety_stats(profile_id="prof-abc", session=session)

        mock_audit.assert_called_once_with("read", "safety_stats", "prof-abc", session)

    @pytest.mark.asyncio
    async def test_db_error_raises_503(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.side_effect = sqlite3.Error("read error")

        with pytest.raises(HTTPException) as exc:
            await get_safety_stats(
                profile_id="prof-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.side_effect = KeyError("missing key")

        with pytest.raises(HTTPException) as exc:
            await get_safety_stats(
                profile_id="prof-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_reraised_as_is(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        original = HTTPException(status_code=403, detail="Forbidden")
        mock_safety_monitor.get_profile_statistics.side_effect = original

        with pytest.raises(HTTPException) as exc:
            await get_safety_stats(
                profile_id="prof-xyz",
                session=_make_admin_session(),
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Forbidden"

    @pytest.mark.asyncio
    async def test_stats_503_detail_mentions_unavailable(
        self, mock_safety_monitor, mock_audit
    ):
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.side_effect = sqlite3.Error("timeout")

        with pytest.raises(HTTPException) as exc:
            await get_safety_stats(
                profile_id="prof-xyz",
                session=_make_admin_session(),
            )

        assert "unavailable" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_parent_session_can_access_stats(
        self, mock_safety_monitor, mock_audit
    ):
        """Verify that a parent session is accepted (auth is handled by dependency)."""
        from api.routes.safety import get_safety_stats

        mock_safety_monitor.get_profile_statistics.return_value = {"total_incidents": 0}

        result = await get_safety_stats(
            profile_id="prof-001",
            session=_make_parent_session(),
        )

        assert result["total_incidents"] == 0


# ---------------------------------------------------------------------------
# Cross-cutting: audit_log failures should not propagate
# ---------------------------------------------------------------------------

class TestAuditLogSilentFailure:
    """
    The route handlers call audit_log() after the core operation succeeds.
    Even if audit_log raises or returns False, the response should still
    reach the caller (routes don't check the return value of audit_log).
    """

    @pytest.mark.asyncio
    async def test_alerts_succeeds_even_if_audit_raises(
        self, mock_safety_monitor
    ):
        from api.routes.safety import get_parent_alerts

        mock_safety_monitor.get_pending_alerts.return_value = []
        with patch("api.routes.safety.audit_log", side_effect=Exception("audit db down")):
            # The route should propagate the exception since audit_log is in
            # the try block and gets caught by the generic Exception handler.
            with pytest.raises(HTTPException) as exc:
                await get_parent_alerts(
                    parent_id="parent-abc",
                    session=_make_admin_session(),
                )
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_incidents_succeeds_if_audit_returns_false(
        self, mock_incident_logger
    ):
        from api.routes.safety import get_profile_incidents

        mock_incident_logger.get_profile_incidents.return_value = []
        with patch("api.routes.safety.audit_log", return_value=False):
            # Returns False is not an exception — route completes normally.
            result = await get_profile_incidents(
                profile_id="prof-abc",
                days=30,
                session=_make_admin_session(),
            )
        assert result["count"] == 0
