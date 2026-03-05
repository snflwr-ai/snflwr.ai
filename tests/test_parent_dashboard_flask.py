"""
Tests for safety/parent_dashboard.py — Flask Parent Dashboard App

Covers:
    - Dashboard page rendering
    - Analytics API endpoint
    - Unreviewed incidents endpoint
    - Mark incident reviewed endpoint
    - User report endpoint
    - Export incidents endpoint (JSON + CSV)
    - Authentication requirement (PARENT_DASHBOARD_PASSWORD)
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Skip entire module if Flask is not installed
flask = pytest.importorskip("flask", reason="Flask not installed")

# Set the required env var BEFORE importing the module (it checks at import time)
os.environ.setdefault("PARENT_DASHBOARD_PASSWORD", "test-secret-password-32chars!!")


@pytest.fixture
def mock_incident_logger():
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
        yield mock


@pytest.fixture
def mock_db():
    with patch("storage.database.db_manager") as mock:
        yield mock


_TEST_DASHBOARD_PASSWORD = "test-secret-password-32chars!!"


@pytest.fixture
def auth_headers():
    """HTTP Basic auth headers using the test password."""
    import base64
    credentials = base64.b64encode(f"admin:{_TEST_DASHBOARD_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


@pytest.fixture
def client(mock_incident_logger, mock_db):
    """Create Flask test client."""
    from safety.parent_dashboard import app
    app.config['TESTING'] = True
    with patch("safety.parent_dashboard.ADMIN_PASSWORD", _TEST_DASHBOARD_PASSWORD):
        with app.test_client() as c:
            yield c


class TestDashboard:

    def test_dashboard_renders(self, client, auth_headers):
        response = client.get('/', headers=auth_headers)
        assert response.status_code == 200
        assert b"Parent Dashboard" in response.data

    def test_dashboard_html_contains_required_elements(self, client, auth_headers):
        response = client.get('/', headers=auth_headers)
        html = response.data.decode()
        assert "loadDashboard" in html
        assert "Unreviewed Incidents" in html


class TestAnalyticsEndpoint:

    def test_get_analytics(self, client, auth_headers, mock_incident_logger):
        response = client.get('/api/analytics?days=7', headers=auth_headers)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["total_incidents"] == 10
        mock_incident_logger.get_incident_statistics.assert_called_once_with(days=7)

    def test_get_analytics_custom_days(self, client, auth_headers, mock_incident_logger):
        client.get('/api/analytics?days=30', headers=auth_headers)
        mock_incident_logger.get_incident_statistics.assert_called_with(days=30)


class TestUnreviewedIncidents:

    def test_get_unreviewed(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {
                "incident_id": 1,
                "profile_id": "prof1",
                "session_id": "sess1",
                "incident_type": "inappropriate_content",
                "severity": "high",
                "content_snippet": "test",
                "timestamp": "2024-01-01T00:00:00",
                "parent_notified": 0,
                "resolved": 0,
                "metadata": "{}",
            }
        ]
        response = client.get('/api/incidents/unreviewed', headers=auth_headers)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["incident_id"] == 1

    def test_get_unreviewed_with_severity_filter(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get('/api/incidents/unreviewed?severity=high', headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert "severity = ?" in query

    def test_get_unreviewed_with_limit(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get('/api/incidents/unreviewed?limit=10', headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert 10 in params

    def test_db_error(self, client, auth_headers, mock_db):
        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")
        response = client.get('/api/incidents/unreviewed', headers=auth_headers)
        assert response.status_code == 500


class TestMarkReviewed:

    def test_mark_reviewed(self, client, auth_headers, mock_incident_logger):
        response = client.post(
            '/api/incidents/1/review',
            data=json.dumps({"notes": "Reviewed by parent"}),
            content_type='application/json',
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        mock_incident_logger.resolve_incident.assert_called_once_with(1, "Reviewed by parent")

    def test_mark_reviewed_no_notes(self, client, auth_headers, mock_incident_logger):
        response = client.post(
            '/api/incidents/1/review',
            data=json.dumps({}),
            content_type='application/json',
            headers=auth_headers,
        )
        assert response.status_code == 200
        mock_incident_logger.resolve_incident.assert_called_once_with(1, '')


class TestUserReport:

    def test_get_report(self, client, auth_headers, mock_incident_logger):
        response = client.get('/api/user/user123/report?days=30', headers=auth_headers)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "total_incidents" in data
        mock_incident_logger.generate_parent_report.assert_called_once_with(
            parent_id="user123", days=30
        )


class TestExportIncidents:

    def test_export_json(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2024-01-01"},
        ]
        response = client.get('/api/export?format=json', headers=auth_headers)
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_export_csv(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = [
            {"incident_id": 1, "severity": "high", "timestamp": "2024-01-01"},
        ]
        response = client.get('/api/export?format=csv', headers=auth_headers)
        assert response.status_code == 200
        assert 'text/csv' in response.content_type
        assert b"incident_id" in response.data

    def test_export_csv_empty(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        response = client.get('/api/export?format=csv', headers=auth_headers)
        assert response.status_code == 200

    def test_export_with_date_filter(self, client, auth_headers, mock_db):
        mock_db.execute_query.return_value = []
        client.get('/api/export?start_date=2024-01-01&end_date=2024-12-31', headers=auth_headers)
        call_args = mock_db.execute_query.call_args
        query = call_args[0][0]
        assert "timestamp >= ?" in query
        assert "timestamp <= ?" in query

    def test_export_db_error(self, client, auth_headers, mock_db):
        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")
        response = client.get('/api/export', headers=auth_headers)
        assert response.status_code == 500


class TestPasswordRequired:

    def test_password_env_var_is_set(self):
        """Verify the module loaded with PARENT_DASHBOARD_PASSWORD set."""
        from safety.parent_dashboard import ADMIN_PASSWORD
        assert ADMIN_PASSWORD is not None
        assert len(ADMIN_PASSWORD) > 0
