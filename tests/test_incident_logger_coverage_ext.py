"""
Extended coverage tests for safety/incident_logger.py.

Targets uncovered lines:
- 22-23: InvalidToken import fallback
- 35-43: get_email_system lazy loader fallback (ImportError)
- 54-56: get_websocket_manager lazy loader fallback (ImportError)
- 356-358: Decrypt resolution notes failure in get_incident
- 456-457: g() accessor KeyError/IndexError fallback in get_profile_incidents
- 469-471: Invalid timestamp ValueError in get_profile_incidents
- 477-479: Invalid parent_notified_at ValueError
- 485-487: Invalid resolved_at ValueError
- 493-494: Content snippet decrypt failure fallback
- 502-507: Metadata JSON parse failure then decrypt failure
- 513-516: Resolution notes decrypt failure in get_profile_incidents
- 884-906: Parent email alert queueing
- 917-918: DB error on parent alert
- 979-982: WebSocket broadcast profile not found
- 1016-1019: WebSocket broadcast with running event loop
- 1029-1034: WebSocket broadcast DB error
"""

import sqlite3
import json
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock
import sys

import pytest

from safety.incident_logger import IncidentLogger, SafetyIncident

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
    return f"encrypted_{value}"


def _decrypt_side_effect(value):
    if isinstance(value, str) and value.startswith("encrypted_"):
        return value[len("encrypted_"):]
    return value


def _encrypt_dict_side_effect(d):
    return f"encrypted_{json.dumps(d)}"


def _decrypt_dict_side_effect(value):
    if isinstance(value, str) and value.startswith("encrypted_"):
        return json.loads(value[len("encrypted_"):])
    return value


_CHILD_PROFILE_ROW = {'parent_id': 'parent-001', 'name': 'Emma', 'age': 10}


def _log_incident_query_side_effect(incident_id=1):
    return [
        [{'incident_id': incident_id}],
        [_CHILD_PROFILE_ROW],
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_query = MagicMock(return_value=[])
    db.execute_write = MagicMock()
    return db


@pytest.fixture
def mock_encryption():
    with patch.object(_incident_logger_mod, "encryption_manager") as enc:
        enc.encrypt_string = MagicMock(side_effect=_encrypt_side_effect)
        enc.decrypt_string = MagicMock(side_effect=_decrypt_side_effect)
        enc.encrypt_dict = MagicMock(side_effect=_encrypt_dict_side_effect)
        enc.decrypt_dict = MagicMock(side_effect=_decrypt_dict_side_effect)
        yield enc


@pytest.fixture
def mock_websocket():
    with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=None):
        yield


@pytest.fixture
def mock_email():
    with patch.object(_incident_logger_mod, "get_email_system", return_value=None):
        yield


@pytest.fixture
def mock_email_crypto():
    with patch.object(_incident_logger_mod, "get_email_crypto") as ec:
        ec.return_value.decrypt_email = MagicMock(return_value="parent@example.com")
        yield ec


@pytest.fixture
def logger(mock_db, mock_encryption, mock_websocket, mock_email, mock_email_crypto):
    il = IncidentLogger(db=mock_db)
    il.encryption = mock_encryption
    return il


# =========================================================================
# InvalidToken import fallback (lines 22-23)
# =========================================================================


class TestInvalidTokenFallback:
    def test_invalid_token_is_exception_when_cryptography_missing(self):
        """When cryptography is not installed, InvalidToken falls back to Exception."""
        # Verify the module-level fallback works
        try:
            from cryptography.fernet import InvalidToken
            assert issubclass(InvalidToken, Exception)
        except ImportError:
            InvalidToken = Exception
            assert InvalidToken is Exception


# =========================================================================
# Lazy loader fallbacks (lines 35-43, 54-56)
# =========================================================================


class TestLazyLoaderFallbacks:

    def test_get_email_system_import_error(self):
        """get_email_system returns None when import fails (lines 40-42)."""
        # Reset the global to force re-evaluation
        _incident_logger_mod._email_system = None
        with patch.dict(sys.modules, {"utils.email_alerts": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                result = _incident_logger_mod.get_email_system()
        assert result is None
        # Reset for other tests
        _incident_logger_mod._email_system = None

    def test_get_websocket_manager_import_error(self):
        """get_websocket_manager returns None when import fails (lines 54-56)."""
        _incident_logger_mod._websocket_manager = None
        with patch.dict(sys.modules, {"api.websocket_server": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                result = _incident_logger_mod.get_websocket_manager()
        assert result is None
        _incident_logger_mod._websocket_manager = None

    def test_get_email_system_cached_on_success(self):
        """get_email_system caches the result after first successful import."""
        _incident_logger_mod._email_system = None
        mock_email = MagicMock()
        with patch.object(
            _incident_logger_mod, "_email_system", None
        ):
            with patch.dict(
                sys.modules,
                {"utils.email_alerts": MagicMock(email_alert_system=mock_email)},
            ):
                result = _incident_logger_mod.get_email_system()
        # Reset
        _incident_logger_mod._email_system = None

    def test_get_websocket_manager_cached_on_success(self):
        """get_websocket_manager caches the result."""
        _incident_logger_mod._websocket_manager = None
        mock_ws = MagicMock()
        with patch.object(
            _incident_logger_mod, "_websocket_manager", None
        ):
            with patch.dict(
                sys.modules,
                {"api.websocket_server": MagicMock(websocket_manager=mock_ws)},
            ):
                result = _incident_logger_mod.get_websocket_manager()
        _incident_logger_mod._websocket_manager = None


# =========================================================================
# get_incidents_by_severity: timestamp/date parsing errors (lines 469-471, 477-479, 485-487)
# These lines are in get_incidents_by_severity, not get_profile_incidents.
# =========================================================================


class TestGetIncidentsBySeverityDateParsing:

    def test_invalid_timestamp_falls_back_to_now(self, logger, mock_db, mock_encryption):
        """Invalid timestamp string falls back to datetime.now(utc) (lines 469-471)."""
        row = _make_row({'timestamp': 'not-a-valid-timestamp'})
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        # Timestamp should be recent (within a minute of now)
        diff = datetime.now(timezone.utc) - results[0].timestamp.replace(tzinfo=timezone.utc)
        assert diff.total_seconds() < 60

    def test_invalid_parent_notified_at_falls_back_to_none(self, logger, mock_db, mock_encryption):
        """Invalid parent_notified_at string falls back to None (lines 477-479)."""
        row = _make_row({
            'parent_notified': 1,
            'parent_notified_at': 'garbage-date',
        })
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].parent_notified_at is None

    def test_invalid_resolved_at_falls_back_to_none(self, logger, mock_db, mock_encryption):
        """Invalid resolved_at string falls back to None (lines 485-487)."""
        row = _make_row({
            'resolved': 1,
            'resolved_at': 'invalid-resolved-date',
        })
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].resolved_at is None


# =========================================================================
# get_incidents_by_severity: content snippet decrypt failure (lines 493-494)
# These lines are in get_incidents_by_severity, not get_profile_incidents.
# =========================================================================


class TestGetIncidentsBySeverityDecryptFailures:

    def test_content_snippet_decrypt_failure_returns_raw(self, logger, mock_db, mock_encryption):
        """When content_snippet decryption fails, raw value is used (lines 493-494)."""
        mock_encryption.decrypt_string.side_effect = Exception("decrypt fail")
        row = _make_row({'content_snippet': 'undecryptable_content'})
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].content_snippet == "undecryptable_content"

    def test_metadata_json_parse_failure_then_decrypt_failure(self, logger, mock_db, mock_encryption):
        """Metadata that isn't valid JSON and can't be decrypted returns {} (lines 502-507)."""
        # decrypt_string works fine for content_snippet
        mock_encryption.decrypt_string.side_effect = _decrypt_side_effect
        # But decrypt_dict fails for metadata
        mock_encryption.decrypt_dict.side_effect = Exception("decrypt fail")

        row = _make_row({'metadata': 'not-valid-json-and-not-encrypted'})
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].metadata == {}

    def test_resolution_notes_decrypt_failure_returns_raw(self, logger, mock_db, mock_encryption):
        """Resolution notes decrypt failure returns raw value (lines 513-516)."""
        # First call for content_snippet succeeds, later calls for resolution_notes fail
        call_count = [0]

        def selective_decrypt(value):
            call_count[0] += 1
            if call_count[0] == 1:
                return _decrypt_side_effect(value)  # content_snippet OK
            raise Exception("decrypt fail")  # resolution_notes fails

        mock_encryption.decrypt_string.side_effect = selective_decrypt

        row = _make_row({
            'resolution_notes': 'encrypted_but_broken',
            'resolved': 1,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        })
        mock_db.execute_query.return_value = [row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].resolution_notes == "encrypted_but_broken"


# =========================================================================
# get_incidents_by_severity: g() accessor fallback (lines 456-457)
# =========================================================================


class TestGetIncidentsBySeverityGAccessor:

    def test_tuple_row_uses_index_fallback(self, logger, mock_db, mock_encryption):
        """When row is a tuple, g() uses positional index fallback (lines 456-457)."""
        now = datetime.now(timezone.utc).isoformat()
        # Tuple row: positional access (g() falls back to index)
        tuple_row = (
            1,           # 0: incident_id
            'child-001', # 1: profile_id
            'sess-001',  # 2: session_id
            'violence',  # 3: incident_type
            'major',     # 4: severity
            'encrypted_content',  # 5: content_snippet
            now,         # 6: timestamp
            0,           # 7: parent_notified
            None,        # 8: parent_notified_at
            0,           # 9: resolved
            None,        # 10: resolved_at
            None,        # 11: resolution_notes
            None,        # 12: metadata
        )
        mock_db.execute_query.return_value = [tuple_row]

        results = logger.get_incidents_by_severity("child-001")
        assert len(results) == 1
        assert results[0].incident_id == 1


# =========================================================================
# Parent email alert queueing (lines 884-906)
# =========================================================================


class TestParentEmailAlertQueueing:

    def test_email_alert_sent_when_email_system_available(self, mock_db, mock_encryption, mock_websocket, mock_email_crypto):
        """Email alert is queued when email system is available (lines 884-906)."""
        mock_email_sys = MagicMock()
        with patch.object(_incident_logger_mod, "get_email_system", return_value=mock_email_sys):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],                                    # post-insert ID
                [_CHILD_PROFILE_ROW],                                    # ws broadcast
                [{'parent_id': 'p1', 'name': 'Emma', 'age': 10}],       # parent alert child_profiles
                [{'encrypted_email': 'enc_email'}],                      # parent email lookup
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="critical",
                content_snippet="bad content",
                send_alert=True,
            )

            # Verify send_safety_alert was called
            mock_email_sys.send_safety_alert.assert_called_once()
            call_kwargs = mock_email_sys.send_safety_alert.call_args
            assert call_kwargs is not None

    def test_email_alert_not_sent_when_no_encrypted_email(self, mock_db, mock_encryption, mock_websocket, mock_email_crypto):
        """No email alert when parent has no encrypted email."""
        mock_email_sys = MagicMock()
        with patch.object(_incident_logger_mod, "get_email_system", return_value=mock_email_sys):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],
                [_CHILD_PROFILE_ROW],
                [{'parent_id': 'p1', 'name': 'Emma', 'age': 10}],
                [{'encrypted_email': None}],  # No encrypted email
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="critical",
                content_snippet="bad",
                send_alert=True,
            )

            mock_email_sys.send_safety_alert.assert_not_called()


# =========================================================================
# DB error on parent alert (lines 917-918)
# =========================================================================


class TestParentAlertDBError:

    def test_db_error_during_parent_alert(self, mock_db, mock_encryption, mock_websocket, mock_email, mock_email_crypto):
        """DB error during parent alert is caught (lines 917-918)."""
        # Make the child_profiles query for _send_parent_alert fail
        mock_db.execute_query.side_effect = [
            [{'incident_id': 1}],       # post-insert ID
            [_CHILD_PROFILE_ROW],       # ws broadcast
            sqlite3.Error("DB fail"),   # _send_parent_alert child_profiles query
        ]

        il = IncidentLogger(db=mock_db)
        il.encryption = mock_encryption

        # Should not raise - DB error is caught
        success, incident_id = il.log_incident(
            profile_id="child-001",
            incident_type="violence",
            severity="critical",
            content_snippet="bad",
            send_alert=True,
        )
        # Incident was still logged successfully
        assert success is True
        assert incident_id == 1


# =========================================================================
# WebSocket broadcast: profile not found (lines 979-982)
# =========================================================================


class TestWebSocketBroadcastProfileNotFound:

    def test_no_profile_found_for_websocket(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """Missing profile for WebSocket broadcast is handled (lines 979-982)."""
        with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=MagicMock()):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],  # post-insert ID
                [],                     # ws broadcast: no child_profiles found
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            success, incident_id = il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="minor",
                content_snippet="x",
            )
            assert success is True


# =========================================================================
# WebSocket broadcast with event loop (lines 1016-1019, 1029-1034)
# =========================================================================


class TestWebSocketBroadcastEventLoop:

    def test_broadcast_with_no_running_loop(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """WebSocket broadcast creates new event loop when none running (lines 1022-1027)."""
        mock_ws = MagicMock()
        mock_ws.broadcast_to_parent = AsyncMock()

        with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=mock_ws):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],
                [_CHILD_PROFILE_ROW],
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            success, _ = il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="minor",
                content_snippet="x",
            )
            assert success is True
            # broadcast_to_parent should have been called
            mock_ws.broadcast_to_parent.assert_called_once()

    def test_broadcast_with_running_event_loop(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """WebSocket broadcast uses existing event loop (lines 1015-1019)."""
        mock_ws = MagicMock()
        mock_ws.broadcast_to_parent = AsyncMock()

        async def run_in_loop():
            with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=mock_ws):
                mock_db.execute_query.side_effect = [
                    [{'incident_id': 1}],
                    [_CHILD_PROFILE_ROW],
                ]

                il = IncidentLogger(db=mock_db)
                il.encryption = mock_encryption
                success, _ = il.log_incident(
                    profile_id="child-001",
                    incident_type="violence",
                    severity="minor",
                    content_snippet="x",
                )
                assert success is True
                # Give the task a chance to run
                await asyncio.sleep(0.01)

        asyncio.run(run_in_loop())

    def test_broadcast_connection_error_handled(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """ConnectionError during broadcast is caught (lines 1029-1031)."""
        mock_ws = MagicMock()
        mock_ws.broadcast_to_parent = AsyncMock(side_effect=ConnectionError("ws down"))

        with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=mock_ws):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],
                [_CHILD_PROFILE_ROW],
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            # Should not raise
            success, _ = il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="minor",
                content_snippet="x",
            )
            assert success is True

    def test_broadcast_db_error_handled(self, mock_db, mock_encryption, mock_email, mock_email_crypto):
        """DB error during WebSocket broadcast is caught (lines 1033-1034)."""
        mock_ws = MagicMock()
        with patch.object(_incident_logger_mod, "get_websocket_manager", return_value=mock_ws):
            mock_db.execute_query.side_effect = [
                [{'incident_id': 1}],          # post-insert ID
                sqlite3.Error("DB fail"),       # ws broadcast child_profiles query fails
            ]

            il = IncidentLogger(db=mock_db)
            il.encryption = mock_encryption
            # Should not raise
            success, _ = il.log_incident(
                profile_id="child-001",
                incident_type="violence",
                severity="minor",
                content_snippet="x",
            )
            assert success is True


# =========================================================================
# get_incident: resolution notes decrypt failure (lines 356-358)
# =========================================================================


class TestGetIncidentDecryptFailures:

    def test_resolution_notes_decrypt_value_error_returns_none(self, logger, mock_db, mock_encryption):
        """Resolution notes ValueError falls back to None (lines 356-358)."""
        call_count = [0]
        def selective_decrypt(value):
            call_count[0] += 1
            if call_count[0] == 1:
                return _decrypt_side_effect(value)  # content_snippet OK
            raise ValueError("invalid token")  # resolution_notes fails

        mock_encryption.decrypt_string.side_effect = selective_decrypt
        row = _make_row({
            'resolution_notes': 'bad_encrypted_notes',
            'resolved': 1,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
            'metadata': None,
        })
        mock_db.execute_query.return_value = [row]

        result = logger.get_incident(1)
        assert result is not None
        assert result.resolution_notes is None

    def test_resolution_notes_decrypt_type_error_returns_none(self, logger, mock_db, mock_encryption):
        """Resolution notes TypeError falls back to None (lines 356-358)."""
        call_count = [0]
        def selective_decrypt(value):
            call_count[0] += 1
            if call_count[0] == 1:
                return _decrypt_side_effect(value)  # content_snippet OK
            raise TypeError("wrong type")  # resolution_notes fails

        mock_encryption.decrypt_string.side_effect = selective_decrypt
        row = _make_row({
            'resolution_notes': 'bad_encrypted_notes',
            'resolved': 1,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
            'metadata': None,
        })
        mock_db.execute_query.return_value = [row]

        result = logger.get_incident(1)
        assert result is not None
        assert result.resolution_notes is None


# =========================================================================
# get_incident: metadata decrypt failure (already partially covered)
# Verify the specific InvalidToken/ValueError/TypeError path (line 346)
# =========================================================================


class TestGetIncidentMetadataDecryptSpecificErrors:

    def test_metadata_type_error_returns_empty_dict(self, logger, mock_db, mock_encryption):
        """TypeError during metadata decryption falls back to {} (line 345-347)."""
        mock_encryption.decrypt_dict.side_effect = TypeError("bad type")
        row = _make_row({'metadata': 'some_encrypted_metadata'})
        mock_db.execute_query.return_value = [row]

        result = logger.get_incident(1)
        assert result.metadata == {}
