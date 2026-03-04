"""
Test Suite for Parent Dashboard Flask App (safety/parent_dashboard.py)

Covers:
    - Authentication (require_auth decorator, HTTP Basic auth, timing-safe comparison)
    - Dashboard page rendering (GET /)
    - Analytics API endpoint (GET /api/analytics)
    - Unreviewed incidents endpoint (GET /api/incidents/unreviewed)
    - Mark incident reviewed endpoint (POST /api/incidents/<id>/review)
    - User safety report endpoint (GET /api/user/<user_id>/report)
    - Export incidents endpoint (GET /api/export) in JSON and CSV formats
    - Encryption/decryption of incident fields
    - Database error handling
    - Edge cases (empty data, missing fields, bad params)
"""

import base64
import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# Flask is optional -- skip the entire module if not installed
flask = pytest.importorskip("flask", reason="Flask not installed")

# The module raises RuntimeError at import time if this env var is missing.
# Set it once before any import of the module.
os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_incident_logger():
    """Patch the module-level incident_logger used by all route handlers."""
    with patch("safety.parent_dashboard.incident_logger") as mock:
        mock.get_incident_statistics.return_value = {
            "total_incidents": 10,
            "unresolved": 3,
            "awaiting_parent_notification": 1,
        }
        mock.generate_parent_report.return_value = {
            "total_incidents": 5,
            "severity_breakdown": {"high": 2, "medium": 3},
        }
        mock.resolve_incident.return_value = True
        yield mock


@pytest.fixture
def mock_db():
    """Patch db_manager at its source so local imports inside routes pick it up."""
    with patch("storage.database.db_manager") as mock:
        yield mock


@pytest.fixture
def mock_encryption():
    """Patch EncryptionManager so decryption calls are intercepted."""
    enc_instance = MagicMock()
    enc_instance.decrypt_string.return_value = "decrypted snippet"
    enc_instance.decrypt_dict.return_value = {"decrypted_key": "decrypted_value"}
    with patch("storage.encryption.EncryptionManager", return_value=enc_instance) as cls_mock:
        cls_mock._instance = enc_instance
        yield enc_instance


@pytest.fixture
def app_instance(mock_incident_logger, mock_db):
    """Return the Flask app with mocked dependencies."""
    from safety.parent_dashboard import app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app_instance):
    """Flask test client without authentication headers."""
    with app_instance.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    """Valid HTTP Basic auth headers matching the env-var password."""
    password = os.environ["PARENT_DASHBOARD_PASSWORD"]
    creds = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture
def bad_auth_headers():
    """HTTP Basic auth headers with an incorrect password."""
    creds = base64.b64encode(b"admin:wrong-password").decode()
    return {"Authorization": f"Basic {creds}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_incident_row(**overrides):
    """Return a dict that looks like a DB row from safety_incidents."""
    row = {
        "incident_id": 1,
        "profile_id": "prof-abc",
        "session_id": "sess-123",
        "incident_type": "inappropriate_content",
        "severity": "high",
        "content_snippet": "encrypted_snippet_data",
        "timestamp": "2025-06-15T10:30:00",
        "parent_notified": 0,
        "resolved": 0,
        "metadata": "encrypted_metadata_blob",
    }
    row.update(overrides)
    return row


# ===================================================================
# Authentication Tests
# ===================================================================

class TestAuthentication:
    """Verify that require_auth protects every route."""

    # -- unauthenticated requests --

    def test_dashboard_requires_auth(self, client):
        resp = client.get("/")
        assert resp.status_code == 401
        data = json.loads(resp.data)
        assert data["error"] == "Authentication required"

    def test_analytics_requires_auth(self, client):
        resp = client.get("/api/analytics")
        assert resp.status_code == 401

    def test_unreviewed_requires_auth(self, client):
        resp = client.get("/api/incidents/unreviewed")
        assert resp.status_code == 401

    def test_review_requires_auth(self, client):
        resp = client.post("/api/incidents/1/review")
        assert resp.status_code == 401

    def test_user_report_requires_auth(self, client):
        resp = client.get("/api/user/user1/report")
        assert resp.status_code == 401

    def test_export_requires_auth(self, client):
        resp = client.get("/api/export")
        assert resp.status_code == 401

    # -- wrong password --

    def test_wrong_password_returns_401(self, client, bad_auth_headers):
        resp = client.get("/", headers=bad_auth_headers)
        assert resp.status_code == 401

    def test_wrong_password_analytics(self, client, bad_auth_headers):
        resp = client.get("/api/analytics", headers=bad_auth_headers)
        assert resp.status_code == 401

    # -- www-authenticate header --

    def test_401_includes_www_authenticate_header(self, client):
        resp = client.get("/")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert "Basic" in resp.headers["WWW-Authenticate"]
        assert 'realm="Parent Dashboard"' in resp.headers["WWW-Authenticate"]

    # -- correct password --

    def test_correct_password_grants_access(self, client, auth_headers):
        resp = client.get("/", headers=auth_headers)
        assert resp.status_code == 200

    def test_empty_authorization_header(self, client):
        """Request with empty Authorization header should still 401."""
        resp = client.get("/", headers={"Authorization": ""})
        assert resp.status_code == 401

    # -- ADMIN_PASSWORD module variable --

    def test_admin_password_is_loaded(self):
        from safety.parent_dashboard import ADMIN_PASSWORD
        assert ADMIN_PASSWORD is not None
        assert len(ADMIN_PASSWORD) > 0


# ===================================================================
# Dashboard (GET /)
# ===================================================================

class TestDashboardPage:

    def test_dashboard_renders_html(self, client, auth_headers):
        resp = client.get("/", headers=auth_headers)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Parent Dashboard" in html

    def test_dashboard_contains_js_loader(self, client, auth_headers):
        resp = client.get("/", headers=auth_headers)
        html = resp.data.decode()
        assert "loadDashboard" in html

    def test_dashboard_contains_key_sections(self, client, auth_headers):
        resp = client.get("/", headers=auth_headers)
        html = resp.data.decode()
        assert "Unreviewed Incidents" in html
        assert "Overview" in html

    def test_dashboard_content_type(self, client, auth_headers):
        resp = client.get("/", headers=auth_headers)
        assert "text/html" in resp.content_type


# ===================================================================
# Analytics (GET /api/analytics)
# ===================================================================

class TestAnalyticsEndpoint:

    def test_get_analytics_default_days(self, client, auth_headers, mock_incident_logger):
        resp = client.get("/api/analytics", headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_incidents"] == 10
        assert data["unresolved"] == 3
        mock_incident_logger.get_incident_statistics.assert_called_once_with(days=7)

    def test_get_analytics_custom_days(self, client, auth_headers, mock_incident_logger):
        resp = client.get("/api/analytics?days=30", headers=auth_headers)
        assert resp.status_code == 200
        mock_incident_logger.get_incident_statistics.assert_called_once_with(days=30)

    def test_get_analytics_days_1(self, client, auth_headers, mock_incident_logger):
        client.get("/api/analytics?days=1", headers=auth_headers)
        mock_incident_logger.get_incident_statistics.assert_called_once_with(days=1)

    def test_analytics_returns_json(self, client, auth_headers):
        resp = client.get("/api/analytics", headers=auth_headers)
        assert resp.content_type == "application/json"


# ===================================================================
# Unreviewed Incidents (GET /api/incidents/unreviewed)
# ===================================================================

class TestUnreviewedIncidents:

    def test_get_unreviewed_empty(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []

    def test_get_unreviewed_with_results(self, client, auth_headers, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [_make_incident_row()]
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["incident_id"] == 1
        assert data[0]["profile_id"] == "prof-abc"
        assert data[0]["severity"] == "high"

    def test_unreviewed_decrypts_content_snippet(self, client, auth_headers, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [_make_incident_row()]
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        data = json.loads(resp.data)
        assert data[0]["content_snippet"] == "decrypted snippet"
        mock_encryption.decrypt_string.assert_called_once_with("encrypted_snippet_data")

    def test_unreviewed_decrypts_metadata(self, client, auth_headers, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [_make_incident_row()]
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        data = json.loads(resp.data)
        assert data[0]["metadata"] == {"decrypted_key": "decrypted_value"}
        mock_encryption.decrypt_dict.assert_called_once_with("encrypted_metadata_blob")

    def test_decryption_failure_snippet_falls_back(self, client, auth_headers, mock_db):
        """When decrypt_string raises, content_snippet becomes '[encrypted]'."""
        bad_enc = MagicMock()
        bad_enc.decrypt_string.side_effect = Exception("decrypt error")
        bad_enc.decrypt_dict.return_value = {}
        with patch("storage.encryption.EncryptionManager", return_value=bad_enc):
            mock_db.execute_query.return_value = [_make_incident_row()]
            resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
            data = json.loads(resp.data)
            assert data[0]["content_snippet"] == "[encrypted]"

    def test_decryption_failure_metadata_falls_back(self, client, auth_headers, mock_db):
        """When decrypt_dict raises, metadata becomes empty dict."""
        bad_enc = MagicMock()
        bad_enc.decrypt_string.return_value = "ok"
        bad_enc.decrypt_dict.side_effect = Exception("decrypt error")
        with patch("storage.encryption.EncryptionManager", return_value=bad_enc):
            mock_db.execute_query.return_value = [_make_incident_row()]
            resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
            data = json.loads(resp.data)
            assert data[0]["metadata"] == {}

    def test_unreviewed_null_snippet_skips_decryption(self, client, auth_headers, mock_db, mock_encryption):
        """Rows with no content_snippet should not attempt decryption."""
        row = _make_incident_row(content_snippet=None, metadata=None)
        mock_db.execute_query.return_value = [row]
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        data = json.loads(resp.data)
        assert data[0]["content_snippet"] is None
        mock_encryption.decrypt_string.assert_not_called()

    def test_unreviewed_empty_string_snippet_skips_decryption(self, client, auth_headers, mock_db, mock_encryption):
        """Rows with empty-string content_snippet should not attempt decryption."""
        row = _make_incident_row(content_snippet="", metadata="")
        mock_db.execute_query.return_value = [row]
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        data = json.loads(resp.data)
        # Empty string is falsy so decryption is skipped
        assert data[0]["content_snippet"] == ""
        mock_encryption.decrypt_string.assert_not_called()

    def test_severity_filter_in_query(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed?severity=high", headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        params = call_args[0][1]
        assert "severity = ?" in query
        assert "high" in params

    def test_limit_parameter(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed?limit=10", headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert 10 in params

    def test_default_limit_is_50(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed", headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert 50 in params

    def test_severity_and_limit_combined(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed?severity=medium&limit=5", headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        params = call_args[0][1]
        assert "severity = ?" in query
        assert "medium" in params
        assert 5 in params

    def test_query_orders_by_timestamp_desc(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        assert "ORDER BY timestamp DESC" in query

    def test_query_filters_unresolved(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/incidents/unreviewed", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        assert "resolved = 0" in query

    def test_db_error_returns_500(self, client, auth_headers, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("connection lost")
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert "error" in data

    def test_multiple_incidents_returned(self, client, auth_headers, mock_db, mock_encryption):
        rows = [
            _make_incident_row(incident_id=1, severity="high"),
            _make_incident_row(incident_id=2, severity="medium"),
            _make_incident_row(incident_id=3, severity="low"),
        ]
        mock_db.execute_query.return_value = rows
        resp = client.get("/api/incidents/unreviewed", headers=auth_headers)
        data = json.loads(resp.data)
        assert len(data) == 3
        assert [d["incident_id"] for d in data] == [1, 2, 3]


# ===================================================================
# Mark Incident Reviewed (POST /api/incidents/<id>/review)
# ===================================================================

class TestMarkReviewed:

    def test_mark_reviewed_with_notes(self, client, auth_headers, mock_incident_logger):
        resp = client.post(
            "/api/incidents/42/review",
            data=json.dumps({"notes": "Parent reviewed"}),
            content_type="application/json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        mock_incident_logger.resolve_incident.assert_called_once_with(42, "Parent reviewed")

    def test_mark_reviewed_empty_json_body(self, client, auth_headers, mock_incident_logger):
        """Sending an empty JSON object should default notes to ''."""
        resp = client.post(
            "/api/incidents/1/review",
            data=json.dumps({}),
            content_type="application/json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mock_incident_logger.resolve_incident.assert_called_once_with(1, "")

    def test_mark_reviewed_with_empty_notes(self, client, auth_headers, mock_incident_logger):
        """Sending notes as empty string should still succeed."""
        resp = client.post(
            "/api/incidents/1/review",
            data=json.dumps({"notes": ""}),
            content_type="application/json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mock_incident_logger.resolve_incident.assert_called_once_with(1, "")

    def test_mark_reviewed_no_notes_key(self, client, auth_headers, mock_incident_logger):
        resp = client.post(
            "/api/incidents/5/review",
            data=json.dumps({"other": "data"}),
            content_type="application/json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        mock_incident_logger.resolve_incident.assert_called_once_with(5, "")

    def test_mark_reviewed_different_ids(self, client, auth_headers, mock_incident_logger):
        """Verify the incident_id URL parameter is passed correctly."""
        client.post(
            "/api/incidents/99/review",
            data=json.dumps({"notes": "ok"}),
            content_type="application/json",
            headers=auth_headers,
        )
        mock_incident_logger.resolve_incident.assert_called_once_with(99, "ok")


# ===================================================================
# User Report (GET /api/user/<user_id>/report)
# ===================================================================

class TestUserReport:

    def test_get_report_default_days(self, client, auth_headers, mock_incident_logger):
        resp = client.get("/api/user/user-abc/report", headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "total_incidents" in data
        mock_incident_logger.generate_parent_report.assert_called_once_with(
            parent_id="user-abc", days=30
        )

    def test_get_report_custom_days(self, client, auth_headers, mock_incident_logger):
        resp = client.get("/api/user/user-abc/report?days=7", headers=auth_headers)
        assert resp.status_code == 200
        mock_incident_logger.generate_parent_report.assert_called_once_with(
            parent_id="user-abc", days=7
        )

    def test_get_report_different_user(self, client, auth_headers, mock_incident_logger):
        client.get("/api/user/parent-xyz/report", headers=auth_headers)
        mock_incident_logger.generate_parent_report.assert_called_once_with(
            parent_id="parent-xyz", days=30
        )

    def test_report_returns_json(self, client, auth_headers):
        resp = client.get("/api/user/u1/report", headers=auth_headers)
        assert resp.content_type == "application/json"


# ===================================================================
# Export Incidents (GET /api/export)
# ===================================================================

class TestExportIncidents:

    def test_export_json_default(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2025-01-15"},
        ]
        resp = client.get("/api/export", headers=auth_headers)
        assert resp.status_code == 200
        assert "application/json" in resp.content_type
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["incident_id"] == 1

    def test_export_json_explicit(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 2, "severity": "low", "timestamp": "2025-02-01"},
        ]
        resp = client.get("/api/export?format=json", headers=auth_headers)
        assert resp.status_code == 200
        assert "application/json" in resp.content_type

    def test_export_csv(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2025-01-15"},
        ]
        resp = client.get("/api/export?format=csv", headers=auth_headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        body = resp.data.decode()
        assert "incident_id" in body  # header row
        assert "high" in body

    def test_export_csv_content_disposition(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2025-01-15"},
        ]
        resp = client.get("/api/export?format=csv", headers=auth_headers)
        assert "Content-Disposition" in resp.headers
        assert "attachment" in resp.headers["Content-Disposition"]
        assert "incidents_" in resp.headers["Content-Disposition"]
        assert ".csv" in resp.headers["Content-Disposition"]

    def test_export_csv_empty(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        resp = client.get("/api/export?format=csv", headers=auth_headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_json_empty(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        resp = client.get("/api/export?format=json", headers=auth_headers)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []

    def test_export_with_start_date(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/export?start_date=2025-01-01", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        params = mock_db.execute_query.call_args[0][1]
        assert "timestamp >= ?" in query
        assert "2025-01-01" in params

    def test_export_with_end_date(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/export?end_date=2025-12-31", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        params = mock_db.execute_query.call_args[0][1]
        assert "timestamp <= ?" in query
        assert "2025-12-31" in params

    def test_export_with_date_range(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get(
            "/api/export?start_date=2025-01-01&end_date=2025-06-30",
            headers=auth_headers,
        )
        query = mock_db.execute_query.call_args[0][0]
        params = mock_db.execute_query.call_args[0][1]
        assert "timestamp >= ?" in query
        assert "timestamp <= ?" in query
        assert "2025-01-01" in params
        assert "2025-06-30" in params

    def test_export_without_dates_has_no_date_filter(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/export", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        assert "timestamp >= ?" not in query
        assert "timestamp <= ?" not in query

    def test_export_query_orders_by_timestamp_desc(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get("/api/export", headers=auth_headers)
        query = mock_db.execute_query.call_args[0][0]
        assert "ORDER BY timestamp DESC" in query

    def test_export_db_error_returns_500(self, client, auth_headers, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("disk full")
        resp = client.get("/api/export", headers=auth_headers)
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert "error" in data

    def test_export_csv_multiple_rows(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2025-01-01"},
            {"incident_id": 2, "severity": "low", "timestamp": "2025-01-02"},
            {"incident_id": 3, "severity": "medium", "timestamp": "2025-01-03"},
        ]
        resp = client.get("/api/export?format=csv", headers=auth_headers)
        body = resp.data.decode()
        lines = [line for line in body.strip().split("\n") if line]
        # 1 header + 3 data rows
        assert len(lines) == 4


# ===================================================================
# Module-level password check
# ===================================================================

class TestModuleImportGuard:

    def test_missing_password_env_var_raises(self):
        """Importing the module without PARENT_DASHBOARD_PASSWORD raises RuntimeError."""
        import importlib
        import safety.parent_dashboard as pd

        # Save original and clear
        original = os.environ.get("PARENT_DASHBOARD_PASSWORD")
        try:
            os.environ.pop("PARENT_DASHBOARD_PASSWORD", None)
            with pytest.raises(RuntimeError, match="CRITICAL SECURITY ERROR"):
                importlib.reload(pd)
        finally:
            # Restore so other tests are not affected
            if original is not None:
                os.environ["PARENT_DASHBOARD_PASSWORD"] = original
            else:
                os.environ["PARENT_DASHBOARD_PASSWORD"] = "test-secret-password-32chars!!"
            # Reload again with the env var restored
            importlib.reload(pd)


# ===================================================================
# require_auth decorator details
# ===================================================================

class TestRequireAuthDecorator:

    def test_no_authorization_header_at_all(self, client):
        """A request with absolutely no Authorization header gets 401."""
        resp = client.get("/api/analytics")
        assert resp.status_code == 401

    def test_password_none_is_rejected(self, client):
        """If auth.password is None, hmac.compare_digest should still work ('' vs password)."""
        # Sending Basic auth with just a username and no password
        creds = base64.b64encode(b"admin:").decode()
        resp = client.get("/api/analytics", headers={"Authorization": f"Basic {creds}"})
        # Empty password != our test password
        assert resp.status_code == 401

    def test_correct_password_any_username(self, client):
        """The decorator only checks password, not username."""
        password = os.environ["PARENT_DASHBOARD_PASSWORD"]
        creds = base64.b64encode(f"anyuser:{password}".encode()).decode()
        resp = client.get("/api/analytics", headers={"Authorization": f"Basic {creds}"})
        assert resp.status_code == 200
