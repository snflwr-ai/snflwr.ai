"""
Tests for safety/incident_logger.py — COPPA/FERPA Audit Trail

Compliance-critical paths tested:
    - log_incident: content encryption, severity validation, parent alert dispatch
    - get_incident: content decryption, metadata decryption
    - get_profile_incidents: date filtering, severity filtering, unresolved filter
    - mark_parent_notified: parent notification timestamp
    - resolve_incident: resolution notes encryption
    - get_incident_statistics: severity breakdown, per-profile filtering
    - generate_parent_report: parent-scoped data, JOIN with child_profiles
    - cleanup_old_incidents: data retention policy
    - _send_parent_alert: parent lookup, email notification, audit trail
    - _format_alert_message: incident type formatting
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import sys

import pytest

from safety.incident_logger import IncidentLogger, SafetyIncident

_incident_logger_mod = sys.modules["safety.incident_logger"]


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_encryption():
    m = MagicMock()
    m.encrypt_string.side_effect = lambda s: f"enc:{s}"
    m.decrypt_string.side_effect = lambda s: s.replace("enc:", "")
    m.encrypt_dict.side_effect = lambda d: f"enc_dict:{d}"
    m.decrypt_dict.side_effect = lambda s: {"key": "value"}
    return m


@pytest.fixture
def logger(mock_db, mock_encryption):
    il = IncidentLogger(db=mock_db)
    il.encryption = mock_encryption
    return il


# --------------------------------------------------------------------------
# log_incident
# --------------------------------------------------------------------------

class TestLogIncident:

    def test_log_minor_incident(self, logger, mock_db):
        mock_db.execute_query.return_value = [{'incident_id': 42}]

        with patch.object(logger, '_broadcast_incident_websocket'):
            success, incident_id = logger.log_incident(
                profile_id="prof1",
                incident_type="violence",
                severity="minor",
                content_snippet="test content",
            )
        assert success is True
        assert incident_id == 42
        mock_db.execute_write.assert_called_once()

    def test_log_critical_sends_parent_alert(self, logger, mock_db):
        mock_db.execute_query.return_value = [{'incident_id': 1}]

        with patch.object(logger, '_broadcast_incident_websocket'), \
             patch.object(logger, '_send_parent_alert') as alert:
            logger.log_incident(
                profile_id="prof1",
                incident_type="self_harm",
                severity="critical",
                content_snippet="concerning content",
            )
            alert.assert_called_once_with("prof1", 1, "critical", "self_harm")

    def test_log_major_sends_parent_alert(self, logger, mock_db):
        mock_db.execute_query.return_value = [{'incident_id': 2}]

        with patch.object(logger, '_broadcast_incident_websocket'), \
             patch.object(logger, '_send_parent_alert') as alert:
            logger.log_incident(
                profile_id="prof1",
                incident_type="violence",
                severity="major",
                content_snippet="test",
            )
            alert.assert_called_once()

    def test_log_minor_does_not_send_alert(self, logger, mock_db):
        mock_db.execute_query.return_value = [{'incident_id': 3}]

        with patch.object(logger, '_broadcast_incident_websocket'), \
             patch.object(logger, '_send_parent_alert') as alert:
            logger.log_incident(
                profile_id="prof1",
                incident_type="mild",
                severity="minor",
                content_snippet="test",
            )
            alert.assert_not_called()

    def test_send_alert_false_skips_alert(self, logger, mock_db):
        mock_db.execute_query.return_value = [{'incident_id': 4}]

        with patch.object(logger, '_broadcast_incident_websocket'), \
             patch.object(logger, '_send_parent_alert') as alert:
            logger.log_incident(
                profile_id="prof1",
                incident_type="violence",
                severity="critical",
                content_snippet="test",
                send_alert=False,
            )
            alert.assert_not_called()

    def test_invalid_severity_rejected(self, logger):
        success, _ = logger.log_incident(
            profile_id="prof1",
            incident_type="test",
            severity="invalid",
            content_snippet="test",
        )
        assert success is False

    def test_content_is_encrypted(self, logger, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [{'incident_id': 5}]

        with patch.object(logger, '_broadcast_incident_websocket'):
            logger.log_incident(
                profile_id="prof1",
                incident_type="test",
                severity="minor",
                content_snippet="sensitive content",
            )
        mock_encryption.encrypt_string.assert_called_with("sensitive content")

    def test_metadata_is_encrypted(self, logger, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [{'incident_id': 6}]

        with patch.object(logger, '_broadcast_incident_websocket'):
            logger.log_incident(
                profile_id="prof1",
                incident_type="test",
                severity="minor",
                content_snippet="test",
                metadata={"key": "value"},
            )
        mock_encryption.encrypt_dict.assert_called_once()

    def test_content_truncated_to_500(self, logger, mock_db, mock_encryption):
        mock_db.execute_query.return_value = [{'incident_id': 7}]
        long_content = "x" * 1000

        with patch.object(logger, '_broadcast_incident_websocket'):
            logger.log_incident(
                profile_id="prof1",
                incident_type="test",
                severity="minor",
                content_snippet=long_content,
            )
        # Should truncate to 500 before encryption
        mock_encryption.encrypt_string.assert_called_with("x" * 500)

    def test_db_error_returns_false(self, logger, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        success, _ = logger.log_incident(
            profile_id="prof1",
            incident_type="test",
            severity="minor",
            content_snippet="test",
        )
        assert success is False


# --------------------------------------------------------------------------
# get_incident
# --------------------------------------------------------------------------

class TestGetIncident:

    def test_get_existing_incident(self, logger, mock_db):
        mock_db.execute_query.return_value = [{
            'incident_id': 1,
            'profile_id': 'prof1',
            'session_id': 'sess1',
            'incident_type': 'violence',
            'severity': 'critical',
            'content_snippet': 'enc:test content',
            'timestamp': '2024-01-01T00:00:00+00:00',
            'parent_notified': 1,
            'parent_notified_at': '2024-01-01T01:00:00+00:00',
            'resolved': 0,
            'resolved_at': None,
            'resolution_notes': None,
            'metadata': 'enc_dict:test',
        }]

        incident = logger.get_incident(1)
        assert incident is not None
        assert incident.incident_id == 1
        assert incident.content_snippet == "test content"
        assert incident.parent_notified is True

    def test_get_nonexistent_incident(self, logger, mock_db):
        mock_db.execute_query.return_value = []
        incident = logger.get_incident(999)
        assert incident is None

    def test_get_incident_db_error(self, logger, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("fail")
        incident = logger.get_incident(1)
        assert incident is None


# --------------------------------------------------------------------------
# get_profile_incidents
# --------------------------------------------------------------------------

class TestGetProfileIncidents:

    def test_basic_query(self, logger, mock_db):
        mock_db.execute_query.return_value = [{
            'incident_id': 1,
            'profile_id': 'prof1',
            'session_id': None,
            'incident_type': 'test',
            'severity': 'minor',
            'content_snippet': 'enc:test',
            'timestamp': '2024-01-01T00:00:00+00:00',
            'parent_notified': 0,
            'parent_notified_at': None,
            'resolved': 0,
            'resolved_at': None,
            'resolution_notes': None,
            'metadata': None,
        }]

        incidents = logger.get_profile_incidents("prof1", days=30)
        assert len(incidents) == 1

    def test_severity_filter(self, logger, mock_db):
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("prof1", severity="critical")
        query = mock_db.execute_query.call_args[0][0]
        assert "severity = ?" in query

    def test_unresolved_filter(self, logger, mock_db):
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("prof1", unresolved_only=True)
        query = mock_db.execute_query.call_args[0][0]
        assert "resolved = 0" in query

    def test_db_error_returns_empty(self, logger, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("fail")
        incidents = logger.get_profile_incidents("prof1")
        assert incidents == []


# --------------------------------------------------------------------------
# mark_parent_notified
# --------------------------------------------------------------------------

class TestMarkParentNotified:

    def test_mark_notified(self, logger, mock_db):
        mock_db.execute_write.return_value = None
        result = logger.mark_parent_notified(1)
        assert result is True
        query = mock_db.execute_write.call_args[0][0]
        assert "parent_notified = 1" in query

    def test_mark_notified_db_error(self, logger, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        result = logger.mark_parent_notified(1)
        assert result is False


# --------------------------------------------------------------------------
# resolve_incident
# --------------------------------------------------------------------------

class TestResolveIncident:

    def test_resolve_with_notes(self, logger, mock_db, mock_encryption):
        mock_db.execute_write.return_value = None
        result = logger.resolve_incident(1, "Reviewed and resolved")
        assert result is True
        mock_encryption.encrypt_string.assert_called_with("Reviewed and resolved")
        query = mock_db.execute_write.call_args[0][0]
        assert "resolved = 1" in query

    def test_resolve_without_notes(self, logger, mock_db):
        mock_db.execute_write.return_value = None
        result = logger.resolve_incident(1, "")
        assert result is True

    def test_resolve_db_error(self, logger, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        result = logger.resolve_incident(1, "notes")
        assert result is False


# --------------------------------------------------------------------------
# get_incident_statistics
# --------------------------------------------------------------------------

class TestGetIncidentStatistics:

    def test_basic_statistics(self, logger, mock_db):
        mock_db.execute_query.side_effect = [
            [  # severity breakdown
                {'severity': 'minor', 'count': 5, 'unresolved': 2, 'not_notified': 1},
                {'severity': 'critical', 'count': 1, 'unresolved': 1, 'not_notified': 1},
            ],
            [  # incident types
                {'incident_type': 'violence', 'count': 3},
                {'incident_type': 'bullying', 'count': 2},
            ],
        ]

        stats = logger.get_incident_statistics(days=30)
        assert stats['total_incidents'] == 6
        assert stats['unresolved'] == 3
        assert stats['by_severity']['critical']['count'] == 1

    def test_statistics_with_profile_filter(self, logger, mock_db):
        mock_db.execute_query.side_effect = [[], []]
        logger.get_incident_statistics(profile_id="prof1", days=7)
        query = mock_db.execute_query.call_args_list[0][0][0]
        assert "profile_id = ?" in query

    def test_statistics_db_error(self, logger, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("fail")
        stats = logger.get_incident_statistics()
        assert stats == {}


# --------------------------------------------------------------------------
# generate_parent_report — FERPA scoped to parent's children
# --------------------------------------------------------------------------

class TestGenerateParentReport:

    def test_report_for_parent(self, logger, mock_db):
        mock_db.execute_query.side_effect = [
            [{  # joined profile + incident data
                'profile_id': 'prof1',
                'child_name': 'Tommy',
                'incident_count': 3,
                'critical': 1,
                'major': 1,
                'minor': 1,
                'latest_incident': '2024-01-01T00:00:00',
            }],
            [{  # unresolved incidents for profile
                'incident_id': 1, 'profile_id': 'prof1', 'session_id': None,
                'incident_type': 'violence', 'severity': 'critical',
                'content_snippet': 'enc:test', 'timestamp': '2024-01-01T00:00:00+00:00',
                'parent_notified': 0, 'parent_notified_at': None,
                'resolved': 0, 'resolved_at': None, 'resolution_notes': None,
                'metadata': None,
            }],
        ]

        report = logger.generate_parent_report("parent1", days=7)
        assert report['parent_id'] == "parent1"
        assert len(report['profiles']) == 1
        assert report['profiles'][0]['child_name'] == 'Tommy'
        assert report['summary']['total_incidents'] == 3
        assert report['summary']['critical_incidents'] == 1

    def test_report_with_profile_filter(self, logger, mock_db):
        mock_db.execute_query.side_effect = [[], []]
        logger.generate_parent_report("parent1", profile_id="prof1")
        query = mock_db.execute_query.call_args_list[0][0][0]
        assert "si.profile_id = ?" in query

    def test_report_joins_on_parent_id(self, logger, mock_db):
        """FERPA: Report must JOIN on parent_id to scope data."""
        mock_db.execute_query.side_effect = [[], []]
        logger.generate_parent_report("parent1")
        query = mock_db.execute_query.call_args_list[0][0][0]
        assert "cp.parent_id = ?" in query

    def test_report_db_error(self, logger, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("fail")
        report = logger.generate_parent_report("parent1")
        assert report == {}


# --------------------------------------------------------------------------
# cleanup_old_incidents — Data Retention
# --------------------------------------------------------------------------

class TestCleanup:

    def test_cleanup_uses_config_default(self, logger, mock_db):
        mock_db.execute_write.return_value = None

        with patch.object(_incident_logger_mod, "safety_config") as cfg:
            cfg.SAFETY_LOG_RETENTION_DAYS = 90
            logger.cleanup_old_incidents()

        query = mock_db.execute_write.call_args[0][0]
        assert "resolved = 1" in query  # Only deletes resolved

    def test_cleanup_custom_retention(self, logger, mock_db):
        mock_db.execute_write.return_value = None
        logger.cleanup_old_incidents(retention_days=30)
        mock_db.execute_write.assert_called_once()

    def test_cleanup_only_deletes_resolved(self, logger, mock_db):
        """COPPA/FERPA: Unresolved incidents must NOT be deleted."""
        mock_db.execute_write.return_value = None
        logger.cleanup_old_incidents(retention_days=1)
        query = mock_db.execute_write.call_args[0][0]
        assert "resolved = 1" in query

    def test_cleanup_db_error(self, logger, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        # Should not raise
        logger.cleanup_old_incidents(retention_days=30)


# --------------------------------------------------------------------------
# _send_parent_alert
# --------------------------------------------------------------------------

class TestSendParentAlert:

    def test_alert_stores_in_db(self, logger, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'p1', 'name': 'Tommy', 'age': 10}],  # profile lookup
            [{'encrypted_email': 'enc_email'}],  # parent email
        ]
        mock_db.execute_write.return_value = None

        with patch.object(_incident_logger_mod, "get_email_system", return_value=None):
            logger._send_parent_alert("prof1", 1, "critical", "violence")

        # Should have inserted into parent_alerts (first write call)
        all_calls = mock_db.execute_write.call_args_list
        insert_queries = [c[0][0] for c in all_calls]
        assert any("INSERT INTO parent_alerts" in q for q in insert_queries)

    def test_alert_profile_not_found(self, logger, mock_db):
        mock_db.execute_query.return_value = []
        # Should not raise — just log
        logger._send_parent_alert("missing", 1, "critical", "violence")

    def test_alert_marks_incident_notified(self, logger, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'p1', 'name': 'Tommy', 'age': 10}],
            [],  # no email
        ]
        mock_db.execute_write.return_value = None

        with patch.object(_incident_logger_mod, "get_email_system", return_value=None), \
             patch.object(logger, 'mark_parent_notified') as mpn:
            logger._send_parent_alert("prof1", 1, "critical", "violence")
            mpn.assert_called_once_with(1)


# --------------------------------------------------------------------------
# _format_alert_message
# --------------------------------------------------------------------------

class TestFormatAlertMessage:

    def test_violence_message(self, logger):
        msg = logger._format_alert_message("Tommy", 10, "critical", "violence", 1)
        assert "URGENT" in msg
        assert "Tommy" in msg
        assert "#1" in msg

    def test_self_harm_message(self, logger):
        msg = logger._format_alert_message("Tommy", 10, "critical", "self_harm", 2)
        assert "self-harm" in msg

    def test_unknown_type(self, logger):
        msg = logger._format_alert_message("Tommy", 10, "minor", "unknown_type", 3)
        assert "Safety concern" in msg

    def test_severity_levels(self, logger):
        minor = logger._format_alert_message("T", 10, "minor", "test", 1)
        major = logger._format_alert_message("T", 10, "major", "test", 1)
        critical = logger._format_alert_message("T", 10, "critical", "test", 1)
        assert "Minor" in minor
        assert "Important" in major
        assert "URGENT" in critical


# --------------------------------------------------------------------------
# SafetyIncident.to_dict
# --------------------------------------------------------------------------

class TestSafetyIncidentDataclass:

    def test_to_dict(self):
        incident = SafetyIncident(
            incident_id=1,
            profile_id="prof1",
            session_id="sess1",
            incident_type="test",
            severity="minor",
            content_snippet="test content",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            parent_notified=True,
            parent_notified_at=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
            resolved=False,
            resolved_at=None,
            resolution_notes=None,
            metadata={"key": "value"},
        )
        d = incident.to_dict()
        assert d['incident_id'] == 1
        assert d['timestamp'] == '2024-01-01T00:00:00+00:00'
        assert d['parent_notified'] is True
        assert d['resolved'] is False
        assert d['resolved_at'] is None
