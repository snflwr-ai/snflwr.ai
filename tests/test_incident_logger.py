"""
Test Suite for Safety Incident Logger
Tests incident logging, retrieval, resolution, statistics, parent reports, and cleanup
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call
import json

import pytest

from safety.incident_logger import IncidentLogger, SafetyIncident

import sys

_incident_logger_mod = sys.modules["safety.incident_logger"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(overrides=None):
    """Build a dict that looks like a DB row from safety_incidents."""
    now = datetime.now(timezone.utc)
    row = {
        'incident_id': 1,
        'profile_id': 'child-001',
        'session_id': 'sess-001',
        'incident_type': 'violence',
        'severity': 'major',
        'content_snippet': 'encrypted_some content',
        'timestamp': now.isoformat(),
        'parent_notified': 0,
        'parent_notified_at': None,
        'resolved': 0,
        'resolved_at': None,
        'resolution_notes': None,
        'metadata': 'encrypted_meta',
    }
    if overrides:
        row.update(overrides)
    return row


def _encrypt_side_effect(value):
    """Simulate encryption by prefixing."""
    return f"encrypted_{value}"


def _decrypt_side_effect(value):
    """Simulate decryption by stripping prefix."""
    if isinstance(value, str) and value.startswith("encrypted_"):
        return value[len("encrypted_"):]
    return value


def _encrypt_dict_side_effect(d):
    """Simulate dict encryption as a JSON string with prefix."""
    return f"encrypted_{json.dumps(d)}"


def _decrypt_dict_side_effect(value):
    """Simulate dict decryption."""
    if isinstance(value, str) and value.startswith("encrypted_"):
        return json.loads(value[len("encrypted_"):])
    return value


# Default child profile row returned during WebSocket broadcast lookup.
_CHILD_PROFILE_ROW = {'parent_id': 'parent-001', 'name': 'Emma', 'age': 10}


def _log_incident_query_side_effect(incident_id=1):
    """Return a side_effect list for execute_query during a successful log_incident call.

    Call sequence:
      1. Post-insert ID lookup -> [{'incident_id': incident_id}]
      2. _broadcast_incident_websocket child_profiles lookup -> [child profile row]
    """
    return [
        [{'incident_id': incident_id}],
        [_CHILD_PROFILE_ROW],
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a MagicMock database."""
    db = MagicMock()
    db.execute_query = MagicMock(return_value=[])
    db.execute_write = MagicMock()
    return db


@pytest.fixture
def mock_encryption():
    """Patch encryption_manager on the incident_logger module."""
    with patch.object(_incident_logger_mod, "encryption_manager") as enc:
        enc.encrypt_string = MagicMock(side_effect=_encrypt_side_effect)
        enc.decrypt_string = MagicMock(side_effect=_decrypt_side_effect)
        enc.encrypt_dict = MagicMock(side_effect=_encrypt_dict_side_effect)
        enc.decrypt_dict = MagicMock(side_effect=_decrypt_dict_side_effect)
        yield enc


@pytest.fixture
def mock_websocket():
    """Patch get_websocket_manager to return None."""
    with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=None):
        yield


@pytest.fixture
def mock_email():
    """Patch get_email_system to return None."""
    with patch.object(_incident_logger_mod, "get_email_system", return_value=None):
        yield


@pytest.fixture
def mock_email_crypto():
    """Patch get_email_crypto."""
    with patch.object(_incident_logger_mod, "get_email_crypto") as ec:
        ec.return_value.decrypt_email = MagicMock(return_value="parent@example.com")
        yield ec


@pytest.fixture
def logger(mock_db, mock_encryption, mock_websocket, mock_email, mock_email_crypto):
    """Create an IncidentLogger with all external dependencies mocked."""
    il = IncidentLogger(db=mock_db)
    # The constructor sets self.encryption from the module-level singleton.
    # Because we patched it, re-assign so the instance uses the mock.
    il.encryption = mock_encryption
    return il


# =========================================================================
# SafetyIncident dataclass
# =========================================================================

class TestSafetyIncident:
    """Tests for the SafetyIncident dataclass and its to_dict method."""

    def test_to_dict_basic_fields(self):
        """to_dict includes all basic scalar fields."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        incident = SafetyIncident(
            incident_id=42,
            profile_id="child-001",
            session_id="sess-abc",
            incident_type="violence",
            severity="critical",
            content_snippet="bad words",
            timestamp=now,
            parent_notified=False,
            parent_notified_at=None,
            resolved=False,
            resolved_at=None,
            resolution_notes=None,
            metadata={"key": "value"},
        )
        d = incident.to_dict()

        assert d['incident_id'] == 42
        assert d['profile_id'] == "child-001"
        assert d['session_id'] == "sess-abc"
        assert d['incident_type'] == "violence"
        assert d['severity'] == "critical"
        assert d['content_snippet'] == "bad words"
        assert d['parent_notified'] is False
        assert d['resolved'] is False
        assert d['resolution_notes'] is None
        assert d['metadata'] == {"key": "value"}

    def test_to_dict_timestamp_iso(self):
        """timestamp is serialized as ISO-8601."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        incident = SafetyIncident(
            incident_id=1, profile_id="p", session_id=None,
            incident_type="t", severity="minor", content_snippet="c",
            timestamp=now, parent_notified=False, parent_notified_at=None,
            resolved=False, resolved_at=None, resolution_notes=None, metadata={},
        )
        d = incident.to_dict()
        assert d['timestamp'] == now.isoformat()

    def test_to_dict_optional_datetime_fields(self):
        """parent_notified_at and resolved_at serialized when present."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        later = now + timedelta(hours=1)
        incident = SafetyIncident(
            incident_id=1, profile_id="p", session_id=None,
            incident_type="t", severity="minor", content_snippet="c",
            timestamp=now, parent_notified=True, parent_notified_at=now,
            resolved=True, resolved_at=later, resolution_notes="fixed", metadata={},
        )
        d = incident.to_dict()
        assert d['parent_notified_at'] == now.isoformat()
        assert d['resolved_at'] == later.isoformat()

    def test_to_dict_none_optional_datetimes(self):
        """parent_notified_at and resolved_at are None when not set."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        incident = SafetyIncident(
            incident_id=1, profile_id="p", session_id=None,
            incident_type="t", severity="minor", content_snippet="c",
            timestamp=now, parent_notified=False, parent_notified_at=None,
            resolved=False, resolved_at=None, resolution_notes=None, metadata={},
        )
        d = incident.to_dict()
        assert d['parent_notified_at'] is None
        assert d['resolved_at'] is None


# =========================================================================
# log_incident
# =========================================================================

class TestLogIncident:
    """Tests for IncidentLogger.log_incident."""

    def test_invalid_severity_returns_false(self, logger, mock_db):
        """Invalid severity values are rejected."""
        success, incident_id = logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="extreme",
            content_snippet="bad content",
        )
        assert success is False
        assert incident_id is None
        mock_db.execute_write.assert_not_called()

    @pytest.mark.parametrize("severity", ["minor", "major", "critical"])
    def test_valid_severity_accepted(self, logger, mock_db, severity):
        """All three valid severity levels are accepted."""
        side = list(_log_incident_query_side_effect(10))
        if severity in ("major", "critical"):
            # _send_parent_alert also queries child_profiles
            side.append([_CHILD_PROFILE_ROW])
        mock_db.execute_query.side_effect = side

        success, incident_id = logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity=severity,
            content_snippet="content",
        )
        assert success is True
        assert incident_id == 10

    def test_content_truncated_to_500(self, logger, mock_db, mock_encryption):
        """Content snippets longer than 500 chars are truncated before encryption."""
        long_content = "A" * 1000
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(5)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet=long_content,
        )
        # encrypt_string should receive only the first 500 chars
        mock_encryption.encrypt_string.assert_called_once_with("A" * 500)

    def test_content_encrypted(self, logger, mock_db, mock_encryption):
        """Content snippet is encrypted before DB insert."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="secret",
        )
        mock_encryption.encrypt_string.assert_called_once_with("secret")

    def test_metadata_encrypted_when_present(self, logger, mock_db, mock_encryption):
        """Metadata dict is encrypted when provided."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)
        meta = {"reason": "keyword match"}

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
            metadata=meta,
        )
        mock_encryption.encrypt_dict.assert_called_once_with(meta)

    def test_metadata_none_when_absent(self, logger, mock_db, mock_encryption):
        """No metadata encryption when metadata is not provided."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
        )
        mock_encryption.encrypt_dict.assert_not_called()

    def test_db_insert_called(self, logger, mock_db):
        """execute_write is called with INSERT statement."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
        )
        assert mock_db.execute_write.called
        sql_arg = mock_db.execute_write.call_args_list[0][0][0]
        assert "INSERT INTO safety_incidents" in sql_arg

    def test_returns_incident_id(self, logger, mock_db):
        """Successful log returns (True, incident_id)."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(77)

        success, incident_id = logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
        )
        assert success is True
        assert incident_id == 77

    def test_returns_false_when_no_query_result(self, logger, mock_db):
        """Returns (False, None) when the post-insert query returns no rows."""
        mock_db.execute_query.return_value = []

        success, incident_id = logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
        )
        assert success is False
        assert incident_id is None

    def test_db_error_returns_false(self, logger, mock_db):
        """DB error during insert returns (False, None)."""
        mock_db.execute_write.side_effect = sqlite3.Error("disk I/O error")

        success, incident_id = logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
        )
        assert success is False
        assert incident_id is None

    def test_session_id_passed_through(self, logger, mock_db):
        """session_id is included in the DB insert parameters."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
            session_id="sess-999",
        )
        write_params = mock_db.execute_write.call_args_list[0][0][1]
        # session_id is the second param in the tuple
        assert write_params[1] == "sess-999"

    def test_websocket_broadcast_called(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """WebSocket broadcast looks up child_profiles for the parent."""
        with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=None):
            mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="minor",
                content_snippet="x",
            )
            # The broadcast method queries child_profiles (second query call)
            assert mock_db.execute_query.call_count >= 2
            second_call_sql = mock_db.execute_query.call_args_list[1][0][0]
            assert "child_profiles" in second_call_sql

    @pytest.mark.parametrize("severity", ["major", "critical"])
    def test_parent_alert_sent_for_major_critical(self, mock_db, mock_encryption, mock_websocket, mock_email_crypto, severity):
        """Parent alert is triggered for major and critical severity when send_alert=True."""
        with patch.object(_incident_logger_mod, "get_email_system", return_value=None):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],                    # post-insert ID query
                [{'parent_id': 'p1', 'name': 'Emma'}],  # ws broadcast child_profiles
                [{'parent_id': 'p1', 'name': 'Emma', 'age': 10}],  # _send_parent_alert child_profiles
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity=severity,
                content_snippet="bad",
                send_alert=True,
            )
            # Should have attempted to write parent_alerts
            write_calls = mock_db.execute_write.call_args_list
            alert_insert = [c for c in write_calls if "parent_alerts" in str(c)]
            assert len(alert_insert) >= 1

    def test_no_parent_alert_for_minor(self, logger, mock_db):
        """No parent alert for minor severity."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="minor",
            content_snippet="x",
            send_alert=True,
        )
        write_calls = mock_db.execute_write.call_args_list
        alert_insert = [c for c in write_calls if "parent_alerts" in str(c)]
        assert len(alert_insert) == 0

    def test_no_parent_alert_when_send_alert_false(self, logger, mock_db):
        """No parent alert when send_alert is False even for critical."""
        mock_db.execute_query.side_effect = _log_incident_query_side_effect(1)

        logger.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="critical",
            content_snippet="x",
            send_alert=False,
        )
        write_calls = mock_db.execute_write.call_args_list
        alert_insert = [c for c in write_calls if "parent_alerts" in str(c)]
        assert len(alert_insert) == 0


# =========================================================================
# get_incident
# =========================================================================

class TestGetIncident:
    """Tests for IncidentLogger.get_incident."""

    def test_returns_safety_incident(self, logger, mock_db, mock_encryption):
        """Returns a SafetyIncident for a valid row."""
        now = datetime.now(timezone.utc)
        mock_db.execute_query.return_value = [_make_row({
            'incident_id': 5,
            'timestamp': now.isoformat(),
        })]

        result = logger.get_incident(5)
        assert isinstance(result, SafetyIncident)
        assert result.incident_id == 5

    def test_decrypts_content_snippet(self, logger, mock_db, mock_encryption):
        """Content snippet is decrypted from the DB value."""
        mock_db.execute_query.return_value = [_make_row({
            'content_snippet': 'encrypted_hello world',
        })]

        result = logger.get_incident(1)
        assert result.content_snippet == "hello world"
        mock_encryption.decrypt_string.assert_any_call("encrypted_hello world")

    def test_decrypts_metadata(self, logger, mock_db, mock_encryption):
        """Metadata is decrypted when present."""
        meta_json = json.dumps({"flag": True})
        mock_db.execute_query.return_value = [_make_row({
            'metadata': f'encrypted_{meta_json}',
        })]

        result = logger.get_incident(1)
        assert result.metadata == {"flag": True}

    def test_empty_metadata_when_none(self, logger, mock_db):
        """Metadata defaults to empty dict when DB value is None."""
        mock_db.execute_query.return_value = [_make_row({
            'metadata': None,
        })]

        result = logger.get_incident(1)
        assert result.metadata == {}

    def test_decrypts_resolution_notes(self, logger, mock_db, mock_encryption):
        """Resolution notes are decrypted when present."""
        mock_db.execute_query.return_value = [_make_row({
            'resolution_notes': 'encrypted_all good',
            'resolved': 1,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        })]

        result = logger.get_incident(1)
        assert result.resolution_notes == "all good"

    def test_resolution_notes_none_when_absent(self, logger, mock_db):
        """Resolution notes are None when not set in DB."""
        mock_db.execute_query.return_value = [_make_row({
            'resolution_notes': None,
        })]

        result = logger.get_incident(1)
        assert result.resolution_notes is None

    def test_returns_none_for_nonexistent(self, logger, mock_db):
        """Returns None when no row matches the incident ID."""
        mock_db.execute_query.return_value = []
        result = logger.get_incident(999)
        assert result is None

    def test_returns_none_on_db_error(self, logger, mock_db):
        """Returns None on database error."""
        mock_db.execute_query.side_effect = sqlite3.Error("connection lost")
        result = logger.get_incident(1)
        assert result is None

    def test_parent_notified_at_parsed(self, logger, mock_db):
        """parent_notified_at is parsed from ISO string."""
        notified = datetime(2025, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        mock_db.execute_query.return_value = [_make_row({
            'parent_notified': 1,
            'parent_notified_at': notified.isoformat(),
        })]

        result = logger.get_incident(1)
        assert result.parent_notified is True
        assert result.parent_notified_at == notified

    def test_resolved_at_parsed(self, logger, mock_db):
        """resolved_at is parsed from ISO string."""
        resolved_time = datetime(2025, 7, 2, 15, 30, 0, tzinfo=timezone.utc)
        mock_db.execute_query.return_value = [_make_row({
            'resolved': 1,
            'resolved_at': resolved_time.isoformat(),
        })]

        result = logger.get_incident(1)
        assert result.resolved is True
        assert result.resolved_at == resolved_time

    def test_metadata_decrypt_failure_returns_empty_dict(self, logger, mock_db, mock_encryption):
        """If metadata decryption fails, metadata falls back to empty dict."""
        mock_encryption.decrypt_dict.side_effect = ValueError("bad token")
        mock_db.execute_query.return_value = [_make_row({
            'metadata': 'garbage_encrypted_data',
        })]

        result = logger.get_incident(1)
        assert result.metadata == {}

    def test_resolution_notes_decrypt_failure_returns_none(self, logger, mock_db, mock_encryption):
        """If resolution_notes decryption fails, falls back to None."""
        mock_encryption.decrypt_string.side_effect = [
            "decrypted_snippet",  # first call for content_snippet
            ValueError("bad token"),  # second call for resolution_notes
        ]
        mock_db.execute_query.return_value = [_make_row({
            'resolution_notes': 'bad_encrypted',
            'metadata': None,
        })]

        result = logger.get_incident(1)
        assert result.resolution_notes is None


# =========================================================================
# get_profile_incidents
# =========================================================================

class TestGetProfileIncidents:
    """Tests for IncidentLogger.get_profile_incidents."""

    def test_returns_list_of_incidents(self, logger, mock_db, mock_encryption):
        """Returns a list of SafetyIncident objects."""
        now = datetime.now(timezone.utc)
        mock_db.execute_query.return_value = [
            _make_row({'incident_id': 1, 'timestamp': now.isoformat()}),
            _make_row({'incident_id': 2, 'timestamp': now.isoformat()}),
        ]

        results = logger.get_profile_incidents("child-001")
        assert len(results) == 2
        assert all(isinstance(r, SafetyIncident) for r in results)

    def test_filters_by_profile_id(self, logger, mock_db):
        """Query includes profile_id filter."""
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("child-001")

        sql, params = mock_db.execute_query.call_args[0]
        assert "profile_id = ?" in sql
        assert "child-001" in params

    def test_filters_by_date_range(self, logger, mock_db):
        """Query includes timestamp cutoff based on days parameter."""
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("child-001", days=7)

        sql, params = mock_db.execute_query.call_args[0]
        assert "timestamp >= ?" in sql

    def test_filters_by_severity(self, logger, mock_db):
        """Optional severity filter adds AND clause."""
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("child-001", severity="critical")

        sql, params = mock_db.execute_query.call_args[0]
        assert "severity = ?" in sql
        assert "critical" in params

    def test_unresolved_only_filter(self, logger, mock_db):
        """unresolved_only adds resolved = 0 clause."""
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("child-001", unresolved_only=True)

        sql = mock_db.execute_query.call_args[0][0]
        assert "resolved = 0" in sql

    def test_decrypts_content_snippets(self, logger, mock_db, mock_encryption):
        """All returned incidents have decrypted content."""
        mock_db.execute_query.return_value = [
            _make_row({'content_snippet': 'encrypted_text1'}),
            _make_row({'content_snippet': 'encrypted_text2'}),
        ]

        results = logger.get_profile_incidents("child-001")
        assert results[0].content_snippet == "text1"
        assert results[1].content_snippet == "text2"

    def test_decrypts_metadata(self, logger, mock_db, mock_encryption):
        """Metadata in each row is decrypted."""
        meta_json = json.dumps({"source": "filter"})
        mock_db.execute_query.return_value = [
            _make_row({'metadata': f'encrypted_{meta_json}'}),
        ]

        results = logger.get_profile_incidents("child-001")
        assert results[0].metadata == {"source": "filter"}

    def test_returns_empty_list_on_error(self, logger, mock_db):
        """Returns empty list on DB error."""
        mock_db.execute_query.side_effect = sqlite3.Error("timeout")
        results = logger.get_profile_incidents("child-001")
        assert results == []

    def test_order_by_timestamp_desc(self, logger, mock_db):
        """Results are ordered by timestamp DESC."""
        mock_db.execute_query.return_value = []
        logger.get_profile_incidents("child-001")

        sql = mock_db.execute_query.call_args[0][0]
        assert "ORDER BY timestamp DESC" in sql

    def test_resolution_notes_decrypted(self, logger, mock_db, mock_encryption):
        """Resolution notes in rows are decrypted."""
        now = datetime.now(timezone.utc)
        mock_db.execute_query.return_value = [_make_row({
            'resolution_notes': 'encrypted_resolved it',
            'resolved': 1,
            'resolved_at': now.isoformat(),
        })]

        results = logger.get_profile_incidents("child-001")
        assert results[0].resolution_notes == "resolved it"


# =========================================================================
# get_unresolved_incidents
# =========================================================================

class TestGetUnresolvedIncidents:
    """Tests for IncidentLogger.get_unresolved_incidents."""

    def test_delegates_to_get_profile_incidents(self, logger, mock_db):
        """get_unresolved_incidents delegates with unresolved_only=True."""
        mock_db.execute_query.return_value = []

        logger.get_unresolved_incidents("child-001")

        sql = mock_db.execute_query.call_args[0][0]
        assert "resolved = 0" in sql


# =========================================================================
# get_incidents_by_severity
# =========================================================================

class TestIncidentsBySeverity:
    """Tests for IncidentLogger.get_incidents_by_severity."""

    def test_min_severity_filters_gte_level(self, logger, mock_db):
        """min_severity='major' returns major and critical."""
        mock_db.execute_query.return_value = []

        logger.get_incidents_by_severity("child-001", min_severity="major")

        sql, params = mock_db.execute_query.call_args[0]
        assert "severity IN" in sql
        # params should include profile_id + the valid severities
        param_list = list(params)
        assert "child-001" in param_list
        assert "major" in param_list
        assert "critical" in param_list
        assert "minor" not in param_list

    def test_min_severity_minor_returns_all(self, logger, mock_db):
        """min_severity='minor' returns all three severities."""
        mock_db.execute_query.return_value = []

        logger.get_incidents_by_severity("child-001", min_severity="minor")

        params = list(mock_db.execute_query.call_args[0][1])
        assert "minor" in params
        assert "major" in params
        assert "critical" in params

    def test_min_severity_critical_returns_only_critical(self, logger, mock_db):
        """min_severity='critical' returns only critical."""
        mock_db.execute_query.return_value = []

        logger.get_incidents_by_severity("child-001", min_severity="critical")

        params = list(mock_db.execute_query.call_args[0][1])
        assert "critical" in params
        assert "major" not in params
        assert "minor" not in params

    def test_exact_severity_backward_compat(self, logger, mock_db):
        """severity param (deprecated) does exact match."""
        mock_db.execute_query.return_value = []

        logger.get_incidents_by_severity("child-001", severity="major")

        sql, params = mock_db.execute_query.call_args[0]
        assert "severity = ?" in sql
        assert "major" in params

    def test_no_filter_returns_all(self, logger, mock_db):
        """No severity filter returns all incidents for the profile."""
        mock_db.execute_query.return_value = []

        logger.get_incidents_by_severity("child-001")

        sql, params = mock_db.execute_query.call_args[0]
        assert "severity" not in sql.split("WHERE")[1] or "profile_id" in sql
        assert "child-001" in params

    def test_returns_safety_incident_objects(self, logger, mock_db):
        """Returned items are SafetyIncident objects."""
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.return_value = [{
            'incident_id': 1,
            'profile_id': 'child-001',
            'session_id': None,
            'incident_type': 'violence',
            'severity': 'critical',
            'content_snippet': 'some text',
            'timestamp': now,
            'parent_notified': 0,
            'parent_notified_at': None,
            'resolved': 0,
            'resolved_at': None,
            'resolution_notes': None,
            'metadata': None,
        }]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert isinstance(results[0], SafetyIncident)

    def test_db_error_returns_empty_list(self, logger, mock_db):
        """DB error returns empty list."""
        mock_db.execute_query.side_effect = sqlite3.Error("table locked")
        results = logger.get_incidents_by_severity("child-001", min_severity="major")
        assert results == []

    def test_metadata_parsed_from_json(self, logger, mock_db):
        """Metadata string is parsed as JSON."""
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.return_value = [{
            'incident_id': 1,
            'profile_id': 'child-001',
            'session_id': None,
            'incident_type': 'test',
            'severity': 'minor',
            'content_snippet': 'text',
            'timestamp': now,
            'parent_notified': 0,
            'parent_notified_at': None,
            'resolved': 0,
            'resolved_at': None,
            'resolution_notes': None,
            'metadata': json.dumps({"key": "val"}),
        }]

        results = logger.get_incidents_by_severity("child-001")
        assert results[0].metadata == {"key": "val"}


# =========================================================================
# mark_parent_notified
# =========================================================================

class TestMarkParentNotified:
    """Tests for IncidentLogger.mark_parent_notified."""

    def test_success_returns_true(self, logger, mock_db):
        """Returns True on successful DB write."""
        result = logger.mark_parent_notified(42)
        assert result is True

    def test_updates_correct_fields(self, logger, mock_db):
        """UPDATE sets parent_notified=1 and parent_notified_at."""
        logger.mark_parent_notified(42)

        sql, params = mock_db.execute_write.call_args[0]
        assert "parent_notified = 1" in sql
        assert "parent_notified_at = ?" in sql
        assert params[1] == 42  # incident_id is second param

    def test_db_error_returns_false(self, logger, mock_db):
        """Returns False on database error."""
        mock_db.execute_write.side_effect = sqlite3.Error("write error")
        result = logger.mark_parent_notified(42)
        assert result is False

    def test_timestamp_is_iso_utc(self, logger, mock_db):
        """The notified_at timestamp is a valid ISO string."""
        logger.mark_parent_notified(42)

        params = mock_db.execute_write.call_args[0][1]
        timestamp_str = params[0]
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(timestamp_str)
        assert parsed.tzinfo is not None


# =========================================================================
# resolve_incident
# =========================================================================

class TestResolveIncident:
    """Tests for IncidentLogger.resolve_incident."""

    def test_success_returns_true(self, logger, mock_db):
        """Returns True on successful resolve."""
        result = logger.resolve_incident(10, "Issue addressed")
        assert result is True

    def test_encrypts_resolution_notes(self, logger, mock_db, mock_encryption):
        """Resolution notes are encrypted before DB write."""
        logger.resolve_incident(10, "Issue addressed")
        mock_encryption.encrypt_string.assert_called_with("Issue addressed")

    def test_updates_resolved_fields(self, logger, mock_db):
        """UPDATE sets resolved=1, resolved_at, and resolution_notes."""
        logger.resolve_incident(10, "fixed")

        sql, params = mock_db.execute_write.call_args[0]
        assert "resolved = 1" in sql
        assert "resolved_at = ?" in sql
        assert "resolution_notes = ?" in sql
        # incident_id is the third param
        assert params[2] == 10

    def test_db_error_returns_false(self, logger, mock_db):
        """Returns False on database error."""
        mock_db.execute_write.side_effect = sqlite3.Error("write error")
        result = logger.resolve_incident(10, "notes")
        assert result is False

    def test_none_resolution_notes_not_encrypted(self, logger, mock_db, mock_encryption):
        """None resolution_notes are not encrypted (passed as None)."""
        logger.resolve_incident(10, None)
        # encrypt_string should not be called for None
        mock_encryption.encrypt_string.assert_not_called()

    def test_resolved_at_timestamp_is_valid_iso(self, logger, mock_db):
        """resolved_at is a valid ISO timestamp."""
        logger.resolve_incident(10, "done")

        params = mock_db.execute_write.call_args[0][1]
        timestamp_str = params[0]
        parsed = datetime.fromisoformat(timestamp_str)
        assert parsed.tzinfo is not None


# =========================================================================
# get_incident_statistics
# =========================================================================

class TestIncidentStatistics:
    """Tests for IncidentLogger.get_incident_statistics."""

    def test_returns_dict_with_expected_keys(self, logger, mock_db):
        """Statistics dict contains required top-level keys."""
        mock_db.execute_query.side_effect = [
            [],  # severity query
            [],  # type query
        ]

        stats = logger.get_incident_statistics(profile_id="child-001")
        assert 'total_incidents' in stats
        assert 'by_severity' in stats
        assert 'unresolved' in stats
        assert 'awaiting_parent_notification' in stats
        assert 'top_incident_types' in stats

    def test_aggregates_severity_counts(self, logger, mock_db):
        """Severity rows are aggregated into by_severity dict."""
        mock_db.execute_query.side_effect = [
            [
                {'severity': 'minor', 'count': 5, 'unresolved': 2, 'not_notified': 1},
                {'severity': 'critical', 'count': 3, 'unresolved': 3, 'not_notified': 3},
            ],
            [],  # type query
        ]

        stats = logger.get_incident_statistics(profile_id="child-001")
        assert stats['total_incidents'] == 8
        assert stats['unresolved'] == 5
        assert stats['awaiting_parent_notification'] == 4
        assert stats['by_severity']['minor']['count'] == 5
        assert stats['by_severity']['critical']['unresolved'] == 3

    def test_top_incident_types(self, logger, mock_db):
        """top_incident_types list is populated from the type query."""
        mock_db.execute_query.side_effect = [
            [],  # severity query
            [
                {'incident_type': 'violence', 'count': 10},
                {'incident_type': 'self_harm', 'count': 5},
            ],
        ]

        stats = logger.get_incident_statistics(profile_id="child-001")
        assert len(stats['top_incident_types']) == 2
        assert stats['top_incident_types'][0] == {'type': 'violence', 'count': 10}

    def test_no_profile_id_queries_all(self, logger, mock_db):
        """When profile_id is None, queries all incidents."""
        mock_db.execute_query.side_effect = [[], []]

        logger.get_incident_statistics(profile_id=None, days=14)

        # First query should NOT have profile_id filter
        first_sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "profile_id = ?" not in first_sql

    def test_with_profile_id_filters(self, logger, mock_db):
        """When profile_id is given, queries filter by it."""
        mock_db.execute_query.side_effect = [[], []]

        logger.get_incident_statistics(profile_id="child-001", days=7)

        first_sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "profile_id = ?" in first_sql

    def test_db_error_returns_empty_dict(self, logger, mock_db):
        """DB error returns empty dict."""
        mock_db.execute_query.side_effect = sqlite3.Error("timeout")
        stats = logger.get_incident_statistics()
        assert stats == {}

    def test_days_parameter_affects_cutoff(self, logger, mock_db):
        """days parameter controls the time window."""
        mock_db.execute_query.side_effect = [[], []]

        logger.get_incident_statistics(profile_id="child-001", days=3)

        first_params = mock_db.execute_query.call_args_list[0][0][1]
        cutoff_str = first_params[1]  # second param is cutoff_date
        cutoff = datetime.fromisoformat(cutoff_str)
        now = datetime.now(timezone.utc)
        # The cutoff should be approximately 3 days ago
        diff = now - cutoff
        assert 2 < diff.days < 4

    def test_time_period_days_in_stats(self, logger, mock_db):
        """time_period_days in the result reflects the days param."""
        mock_db.execute_query.side_effect = [[], []]

        stats = logger.get_incident_statistics(profile_id="child-001", days=14)
        assert stats['time_period_days'] == 14


# =========================================================================
# generate_parent_report
# =========================================================================

class TestParentReport:
    """Tests for IncidentLogger.generate_parent_report."""

    def _make_report_row(self, overrides=None):
        """Build a dict for the parent report query result."""
        row = {
            'profile_id': 'child-001',
            'child_name': 'Emma',
            'incident_count': 10,
            'critical': 2,
            'major': 3,
            'minor': 5,
            'latest_incident': datetime.now(timezone.utc).isoformat(),
        }
        if overrides:
            row.update(overrides)
        return row

    def test_returns_report_dict(self, logger, mock_db):
        """Returns a non-empty report dictionary."""
        mock_db.execute_query.side_effect = [
            [self._make_report_row()],  # main report query
            [],  # get_profile_incidents for unresolved
        ]

        report = logger.generate_parent_report("parent-001", profile_id="child-001")
        assert 'parent_id' in report
        assert report['parent_id'] == "parent-001"
        assert 'profiles' in report
        assert 'summary' in report

    def test_report_profiles_contain_severity_breakdown(self, logger, mock_db):
        """Each profile entry has by_severity breakdown."""
        mock_db.execute_query.side_effect = [
            [self._make_report_row({'critical': 1, 'major': 2, 'minor': 7})],
            [],  # unresolved
        ]

        report = logger.generate_parent_report("parent-001", profile_id="child-001")
        profile = report['profiles'][0]
        assert profile['by_severity']['critical'] == 1
        assert profile['by_severity']['major'] == 2
        assert profile['by_severity']['minor'] == 7

    def test_report_summary_totals(self, logger, mock_db):
        """Summary aggregates across all profiles."""
        mock_db.execute_query.side_effect = [
            [
                self._make_report_row({'profile_id': 'c1', 'child_name': 'Emma', 'incident_count': 5, 'critical': 1, 'major': 2, 'minor': 2}),
                self._make_report_row({'profile_id': 'c2', 'child_name': 'Noah', 'incident_count': 3, 'critical': 0, 'major': 1, 'minor': 2}),
            ],
            [],  # unresolved for c1
            [],  # unresolved for c2
        ]

        report = logger.generate_parent_report("parent-001")
        summary = report['summary']
        assert summary['total_profiles_with_incidents'] == 2
        assert summary['total_incidents'] == 8
        assert summary['critical_incidents'] == 1
        assert summary['major_incidents'] == 3
        assert summary['minor_incidents'] == 4

    def test_unresolved_incidents_included(self, logger, mock_db, mock_encryption):
        """Unresolved incidents are fetched and included in each profile."""
        now = datetime.now(timezone.utc)
        mock_db.execute_query.side_effect = [
            [self._make_report_row()],  # main query
            # get_profile_incidents returns one unresolved incident
            [_make_row({
                'incident_id': 99,
                'incident_type': 'violence',
                'severity': 'major',
                'resolved': 0,
                'timestamp': now.isoformat(),
                'content_snippet': 'encrypted_short',
            })],
        ]

        report = logger.generate_parent_report("parent-001", profile_id="child-001")
        profile = report['profiles'][0]
        assert len(profile['unresolved_incidents']) >= 1
        assert profile['unresolved_incidents'][0]['incident_id'] == 99

    def test_report_period_days(self, logger, mock_db):
        """Report reflects the requested period."""
        mock_db.execute_query.side_effect = [[], []]

        report = logger.generate_parent_report("parent-001", days=14)
        assert report['report_period_days'] == 14

    def test_generated_at_is_iso_timestamp(self, logger, mock_db):
        """generated_at is a valid ISO timestamp."""
        mock_db.execute_query.side_effect = [[], []]

        report = logger.generate_parent_report("parent-001")
        parsed = datetime.fromisoformat(report['generated_at'])
        assert parsed is not None

    def test_no_profile_id_queries_all_children(self, logger, mock_db):
        """When profile_id is None, the query does not filter by profile."""
        mock_db.execute_query.side_effect = [[], []]

        logger.generate_parent_report("parent-001", profile_id=None)

        sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "si.profile_id = ?" not in sql

    def test_with_profile_id_filters(self, logger, mock_db):
        """When profile_id is given, the query filters by it."""
        mock_db.execute_query.side_effect = [[], []]

        logger.generate_parent_report("parent-001", profile_id="child-001")

        sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "si.profile_id = ?" in sql

    def test_db_error_returns_empty_dict(self, logger, mock_db):
        """DB error returns empty dict."""
        mock_db.execute_query.side_effect = sqlite3.Error("timeout")
        report = logger.generate_parent_report("parent-001")
        assert report == {}

    def test_joins_child_profiles(self, logger, mock_db):
        """Report query joins safety_incidents with child_profiles."""
        mock_db.execute_query.side_effect = [[], []]

        logger.generate_parent_report("parent-001")

        sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "JOIN child_profiles" in sql

    def test_content_preview_truncated(self, logger, mock_db, mock_encryption):
        """Content preview in unresolved incidents is truncated to 100 chars."""
        long_content = "X" * 200
        now = datetime.now(timezone.utc)
        mock_db.execute_query.side_effect = [
            [self._make_report_row()],
            [_make_row({
                'content_snippet': f'encrypted_{long_content}',
                'timestamp': now.isoformat(),
                'resolved': 0,
            })],
        ]

        report = logger.generate_parent_report("parent-001", profile_id="child-001")
        preview = report['profiles'][0]['unresolved_incidents'][0]['content_preview']
        assert len(preview) <= 104  # 100 chars + "..."
        assert preview.endswith("...")


# =========================================================================
# cleanup_old_incidents
# =========================================================================

class TestCleanup:
    """Tests for IncidentLogger.cleanup_old_incidents."""

    def test_uses_provided_retention_days(self, logger, mock_db):
        """Uses the explicitly provided retention_days value."""
        logger.cleanup_old_incidents(retention_days=60)

        sql, params = mock_db.execute_write.call_args[0]
        assert "DELETE FROM safety_incidents" in sql
        assert "resolved = 1" in sql
        cutoff = datetime.fromisoformat(params[0])
        diff = datetime.now(timezone.utc) - cutoff
        assert 59 < diff.days < 61

    @patch.object(_incident_logger_mod, "safety_config")
    def test_uses_config_default_when_no_retention_days(self, mock_safety_config, logger, mock_db):
        """Falls back to safety_config.SAFETY_LOG_RETENTION_DAYS when not specified."""
        mock_safety_config.SAFETY_LOG_RETENTION_DAYS = 90

        logger.cleanup_old_incidents()

        sql, params = mock_db.execute_write.call_args[0]
        cutoff = datetime.fromisoformat(params[0])
        diff = datetime.now(timezone.utc) - cutoff
        assert 89 < diff.days < 91

    def test_deletes_only_resolved(self, logger, mock_db):
        """Only resolved incidents are deleted."""
        logger.cleanup_old_incidents(retention_days=30)

        sql = mock_db.execute_write.call_args[0][0]
        assert "resolved = 1" in sql

    def test_deletes_older_than_cutoff(self, logger, mock_db):
        """DELETE uses resolved_at < cutoff condition."""
        logger.cleanup_old_incidents(retention_days=30)

        sql = mock_db.execute_write.call_args[0][0]
        assert "resolved_at < ?" in sql

    def test_db_error_handled(self, logger, mock_db):
        """DB error is caught without raising."""
        mock_db.execute_write.side_effect = sqlite3.Error("cannot delete")
        # Should not raise
        logger.cleanup_old_incidents(retention_days=30)
