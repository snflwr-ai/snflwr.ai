"""
Test Suite for utils/error_tracking.py
Covers ErrorRecord dataclass, ErrorTracker methods, and the track_exceptions decorator.
Target: 80%+ coverage of utils/error_tracking.py
"""

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call
import pytest

from utils.error_tracking import ErrorTracker, ErrorRecord, track_exceptions, error_tracker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def tracker(mock_db):
    with patch('utils.error_tracking.db_manager', mock_db):
        t = ErrorTracker()
        t.db = mock_db
        return t


def _make_db_row(overrides=None):
    """Return a dict resembling a row from error_tracking."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        'error_id': 1,
        'error_hash': 'abcd1234abcd1234',
        'error_type': 'ValueError',
        'error_message': 'something went wrong',
        'module': 'app.py',
        'function': 'my_func',
        'line_number': 42,
        'stack_trace': 'Traceback ...',
        'first_seen': now,
        'last_seen': now,
        'occurrence_count': 1,
        'severity': 'error',
        'resolved': 0,
        'user_id': None,
        'session_id': None,
        'context': None,
    }
    if overrides:
        row.update(overrides)
    return row


# ===========================================================================
# ErrorRecord dataclass
# ===========================================================================

class TestErrorRecord:
    def test_basic_instantiation(self):
        now = datetime.now(timezone.utc)
        record = ErrorRecord(
            error_id=1,
            error_hash='abc',
            error_type='ValueError',
            error_message='oops',
            module='mod.py',
            function='fn',
            line_number=10,
            stack_trace='trace',
            first_seen=now,
            last_seen=now,
            occurrence_count=3,
            severity='error',
            resolved=False,
        )
        assert record.error_id == 1
        assert record.error_type == 'ValueError'
        assert record.resolved is False
        assert record.user_id is None
        assert record.context is None

    def test_optional_fields(self):
        now = datetime.now(timezone.utc)
        record = ErrorRecord(
            error_id=2,
            error_hash='xyz',
            error_type='RuntimeError',
            error_message='boom',
            module='m.py',
            function='f',
            line_number=0,
            stack_trace='',
            first_seen=now,
            last_seen=now,
            occurrence_count=1,
            severity='critical',
            resolved=True,
            user_id='user-001',
            session_id='sess-001',
            context={'key': 'value'},
        )
        assert record.user_id == 'user-001'
        assert record.session_id == 'sess-001'
        assert record.context == {'key': 'value'}


# ===========================================================================
# _generate_error_hash
# ===========================================================================

class TestGenerateErrorHash:
    def test_deterministic(self, tracker):
        h1 = tracker._generate_error_hash('ValueError', 'msg', 'file.py', 'func', 42)
        h2 = tracker._generate_error_hash('ValueError', 'msg', 'file.py', 'func', 42)
        assert h1 == h2

    def test_length_16(self, tracker):
        h = tracker._generate_error_hash('TypeError', 'err', 'mod.py', 'fn', 0)
        assert len(h) == 16

    def test_different_inputs_different_hashes(self, tracker):
        h1 = tracker._generate_error_hash('ValueError', 'msg', 'file.py', 'func', 42)
        h2 = tracker._generate_error_hash('TypeError', 'msg', 'file.py', 'func', 42)
        assert h1 != h2

    def test_hash_excludes_message(self, tracker):
        """Hash is based on type:module:function:lineno — not the message text."""
        h1 = tracker._generate_error_hash('ValueError', 'msg one', 'file.py', 'func', 42)
        h2 = tracker._generate_error_hash('ValueError', 'msg two', 'file.py', 'func', 42)
        assert h1 == h2


# ===========================================================================
# _get_error_by_hash
# ===========================================================================

class TestGetErrorByHash:
    def test_returns_none_when_not_found(self, tracker, mock_db):
        mock_db.execute_query.return_value = []
        result = tracker._get_error_by_hash('nonexistent')
        assert result is None

    def test_returns_first_row_when_found(self, tracker, mock_db):
        row = {'error_id': 7, 'error_hash': 'abc123', 'occurrence_count': 3}
        mock_db.execute_query.return_value = [row]
        result = tracker._get_error_by_hash('abc123')
        assert result == row


# ===========================================================================
# _create_error_record
# ===========================================================================

class TestCreateErrorRecord:
    def test_returns_error_id(self, tracker, mock_db):
        mock_db.execute_query.return_value = [{'error_id': 99}]
        eid = tracker._create_error_record(
            error_hash='hash1',
            error_type='ValueError',
            error_message='oops',
            module='mod.py',
            function='fn',
            line_number=5,
            stack_trace='trace',
            severity='error',
            user_id=None,
            session_id=None,
            context=None,
        )
        assert eid == 99
        mock_db.execute_write.assert_called_once()

    def test_returns_minus_one_when_no_result(self, tracker, mock_db):
        mock_db.execute_query.return_value = []
        eid = tracker._create_error_record(
            error_hash='h',
            error_type='E',
            error_message='m',
            module='mod.py',
            function='f',
            line_number=0,
            stack_trace='',
            severity='warning',
            user_id=None,
            session_id=None,
            context=None,
        )
        assert eid == -1

    def test_serialises_context_to_string(self, tracker, mock_db):
        mock_db.execute_query.return_value = [{'error_id': 10}]
        tracker._create_error_record(
            error_hash='h2',
            error_type='E',
            error_message='m',
            module='mod.py',
            function='f',
            line_number=1,
            stack_trace='',
            severity='error',
            user_id=None,
            session_id=None,
            context={'foo': 'bar'},
        )
        # The context dict should be str()'d into the INSERT call
        args = mock_db.execute_write.call_args[0][1]
        # context is the last positional param
        assert "foo" in args[-1]


# ===========================================================================
# _update_error_occurrence
# ===========================================================================

class TestUpdateErrorOccurrence:
    def test_returns_same_error_id(self, tracker, mock_db):
        result = tracker._update_error_occurrence(42)
        assert result == 42
        mock_db.execute_write.assert_called_once()

    def test_passes_correct_id_to_query(self, tracker, mock_db):
        tracker._update_error_occurrence(7)
        args = mock_db.execute_write.call_args[0][1]
        assert args[-1] == 7


# ===========================================================================
# capture_exception
# ===========================================================================

class TestCaptureException:
    def test_new_error_creates_record_and_returns_id(self, tracker, mock_db):
        """New error: hash lookup returns [], then INSERT + SELECT returns id 42."""
        mock_db.execute_query.side_effect = [
            [],                    # _get_error_by_hash
            [{'error_id': 42}],   # _create_error_record SELECT
        ]
        try:
            raise ValueError("test error")
        except ValueError as e:
            eid = tracker.capture_exception(e)

        assert eid == 42

    def test_existing_error_updates_and_returns_id(self, tracker, mock_db):
        """Existing error: hash lookup returns existing row → update called."""
        existing = {'error_id': 7, 'error_hash': 'abc', 'occurrence_count': 3}
        mock_db.execute_query.return_value = [existing]

        try:
            raise ValueError("existing error")
        except ValueError as e:
            eid = tracker.capture_exception(e)

        assert eid == 7
        # Only the UPDATE should have been written (not an INSERT)
        mock_db.execute_write.assert_called_once()

    def test_returns_minus_one_on_db_failure(self, tracker, mock_db):
        """DB failure in execute_query → returns -1 without raising."""
        mock_db.execute_query.side_effect = Exception("db exploded")

        try:
            raise RuntimeError("trigger")
        except RuntimeError as e:
            eid = tracker.capture_exception(e)

        assert eid == -1

    def test_exception_without_traceback_uses_unknown_defaults(self, tracker, mock_db):
        """Exception with no __traceback__ → module/function = 'unknown', line = 0."""
        mock_db.execute_query.side_effect = [
            [],
            [{'error_id': 5}],
        ]
        exc = ValueError("no tb")
        # exc.__traceback__ is None at this point
        eid = tracker.capture_exception(exc)
        assert eid == 5

    def test_capture_exception_with_user_and_session(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [
            [],
            [{'error_id': 55}],
        ]
        try:
            raise KeyError("key missing")
        except KeyError as e:
            eid = tracker.capture_exception(
                e, severity='warning', user_id='u1', session_id='s1',
                context={'page': 'home'}
            )
        assert eid == 55

    def test_check_alert_threshold_called(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [[], [{'error_id': 1}]]
        with patch.object(tracker, '_check_alert_threshold') as mock_thresh:
            try:
                raise ValueError("x")
            except ValueError as e:
                tracker.capture_exception(e)
            mock_thresh.assert_called_once()


# ===========================================================================
# capture_error
# ===========================================================================

class TestCaptureError:
    def test_with_explicit_location_creates_new_record(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [[], [{'error_id': 20}]]
        eid = tracker.capture_error(
            'CustomError', 'something bad',
            severity='error',
            module='app.py',
            function='handler',
            line_number=100,
        )
        assert eid == 20

    def test_with_existing_hash_updates_record(self, tracker, mock_db):
        existing = {'error_id': 9, 'error_hash': 'hh', 'occurrence_count': 2}
        mock_db.execute_query.return_value = [existing]
        eid = tracker.capture_error(
            'CustomError', 'something bad',
            module='app.py', function='handler', line_number=100,
        )
        assert eid == 9

    def test_without_explicit_module_auto_detects(self, tracker, mock_db):
        """When module/function not given, frame introspection fills them in."""
        mock_db.execute_query.side_effect = [[], [{'error_id': 30}]]
        eid = tracker.capture_error('AutoError', 'auto msg')
        assert eid == 30
        # The INSERT args should have a non-None module
        insert_args = mock_db.execute_write.call_args[0][1]
        # module is index 3 in the INSERT tuple (hash, type, msg, module, ...)
        assert insert_args[3] is not None

    def test_returns_minus_one_on_internal_failure(self, tracker, mock_db):
        mock_db.execute_query.side_effect = Exception("kaboom")
        eid = tracker.capture_error('E', 'msg')
        assert eid == -1

    def test_stack_trace_passed_through(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [[], [{'error_id': 7}]]
        tracker.capture_error(
            'E', 'msg',
            module='m.py', function='f', line_number=1,
            stack_trace='my custom trace',
        )
        insert_args = mock_db.execute_write.call_args[0][1]
        # stack_trace is index 6 in INSERT params
        assert 'my custom trace' in insert_args[6]


# ===========================================================================
# _check_alert_threshold
# ===========================================================================

class TestCheckAlertThreshold:
    def test_critical_alerts_on_first_occurrence(self, tracker):
        with patch.object(tracker, '_send_alert') as mock_alert:
            tracker._check_alert_threshold('hash1', 'critical')
            mock_alert.assert_called_once_with('hash1', 1, 'critical')

    def test_error_does_not_alert_before_threshold(self, tracker):
        with patch.object(tracker, '_send_alert') as mock_alert:
            for _ in range(9):
                tracker._check_alert_threshold('hash2', 'error')
            mock_alert.assert_not_called()

    def test_error_alerts_at_threshold(self, tracker):
        with patch.object(tracker, '_send_alert') as mock_alert:
            for _ in range(10):
                tracker._check_alert_threshold('hash3', 'error')
            mock_alert.assert_called()
            args = mock_alert.call_args[0]
            assert args[1] >= 10

    def test_old_timestamps_purged_outside_window(self, tracker):
        """Timestamps older than ERROR_TIME_WINDOW should be evicted."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=3700)
        tracker._error_cache['hashX'] = [old_time] * 15  # 15 stale entries

        with patch.object(tracker, '_send_alert') as mock_alert:
            # Only 1 new entry added; old ones purged → count == 1 < threshold
            tracker._check_alert_threshold('hashX', 'error')
            mock_alert.assert_not_called()

    def test_warning_severity_does_not_alert_before_threshold(self, tracker):
        with patch.object(tracker, '_send_alert') as mock_alert:
            tracker._check_alert_threshold('hashW', 'warning')
            mock_alert.assert_not_called()


# ===========================================================================
# _send_alert
# ===========================================================================

class TestSendAlert:
    def test_smtp_disabled_no_email_sent(self, tracker):
        with patch('utils.error_tracking.logger') as mock_logger, \
             patch('builtins.__import__') as _:
            # Patch at module level so deferred import sees the mock
            mock_cfg = MagicMock()
            mock_cfg.SMTP_ENABLED = False
            mock_cfg.ADMIN_EMAIL = ''
            with patch.dict('sys.modules', {
                'config': MagicMock(system_config=mock_cfg),
                'utils.email_alerts': MagicMock(),
            }):
                tracker._send_alert('abc123', 5, 'error')
                # Should have logged a warning but not crashed
                mock_logger.warning.assert_called_once()

    def test_smtp_enabled_sends_email(self, tracker):
        mock_email_system = MagicMock()
        mock_cfg = MagicMock()
        mock_cfg.SMTP_ENABLED = True
        mock_cfg.ADMIN_EMAIL = 'admin@test.com'

        mock_email_module = MagicMock()
        mock_email_module.email_alert_system = mock_email_system
        mock_config_module = MagicMock()
        mock_config_module.system_config = mock_cfg

        with patch.dict('sys.modules', {
            'utils.email_alerts': mock_email_module,
            'config': mock_config_module,
        }):
            tracker._send_alert('abc123', 5, 'error')

        mock_email_system.send_error_alert.assert_called_once()
        kwargs = mock_email_system.send_error_alert.call_args[1]
        assert kwargs['admin_email'] == 'admin@test.com'
        assert kwargs['error_count'] == 5

    def test_send_alert_logs_warning(self, tracker):
        mock_cfg = MagicMock()
        mock_cfg.SMTP_ENABLED = False
        mock_cfg.ADMIN_EMAIL = ''
        mock_email_module = MagicMock()
        mock_config_module = MagicMock()
        mock_config_module.system_config = mock_cfg

        with patch.dict('sys.modules', {
            'utils.email_alerts': mock_email_module,
            'config': mock_config_module,
        }), patch('utils.error_tracking.logger') as mock_log:
            tracker._send_alert('myhash', 3, 'critical')
            mock_log.warning.assert_called_once()
            warning_msg = mock_log.warning.call_args[0][0]
            assert 'myhash' in warning_msg
            assert '3' in warning_msg

    def test_send_alert_handles_import_failure_gracefully(self, tracker):
        """If email module is unavailable, _send_alert should not propagate the error."""
        with patch.dict('sys.modules', {'utils.email_alerts': None, 'config': None}):
            # Should not raise
            try:
                tracker._send_alert('h', 1, 'error')
            except Exception as exc:
                pytest.fail(f"_send_alert raised unexpectedly: {exc}")


# ===========================================================================
# get_error_summary
# ===========================================================================

class TestGetErrorSummary:
    def test_returns_correct_structure(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [
            [{'count': 5}],
            [{'severity': 'error', 'count': 3, 'total_occurrences': 10}],
            [{'count': 2}],
            [{'error_type': 'ValueError', 'error_message': 'test',
              'module': 'app.py', 'occurrence_count': 5, 'severity': 'error'}],
        ]
        summary = tracker.get_error_summary(days=7)
        assert summary['total_unique_errors'] == 5
        assert summary['unresolved_errors'] == 2
        assert summary['period_days'] == 7
        assert len(summary['by_severity']) == 1
        assert summary['by_severity'][0]['severity'] == 'error'
        assert len(summary['most_frequent']) == 1

    def test_handles_empty_db_results(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [[], [], [], []]
        summary = tracker.get_error_summary(days=30)
        assert summary['total_unique_errors'] == 0
        assert summary['unresolved_errors'] == 0
        assert summary['by_severity'] == []
        assert summary['most_frequent'] == []

    def test_custom_days_parameter(self, tracker, mock_db):
        mock_db.execute_query.side_effect = [
            [{'count': 1}], [{'severity': 'warning', 'count': 1, 'total_occurrences': 1}],
            [{'count': 0}], [],
        ]
        summary = tracker.get_error_summary(days=14)
        assert summary['period_days'] == 14


# ===========================================================================
# get_error_details
# ===========================================================================

class TestGetErrorDetails:
    def test_returns_none_for_missing_error(self, tracker, mock_db):
        mock_db.execute_query.return_value = []
        result = tracker.get_error_details(9999)
        assert result is None

    def test_returns_error_record_for_found_error(self, tracker, mock_db):
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.return_value = [{
            'error_id': 1,
            'error_hash': 'abcd1234abcd1234',
            'error_type': 'ValueError',
            'error_message': 'something wrong',
            'module': 'app.py',
            'function': 'process',
            'line_number': 42,
            'stack_trace': 'Traceback...',
            'first_seen': now,
            'last_seen': now,
            'occurrence_count': 3,
            'severity': 'error',
            'resolved': 0,
            'user_id': None,
            'session_id': None,
            'context': None,
        }]
        result = tracker.get_error_details(1)
        assert isinstance(result, ErrorRecord)
        assert result.error_type == 'ValueError'
        assert result.line_number == 42
        assert result.resolved is False
        assert result.occurrence_count == 3

    def test_context_json_parsed(self, tracker, mock_db):
        now = datetime.now(timezone.utc).isoformat()
        ctx = json.dumps({'key': 'value'})
        mock_db.execute_query.return_value = [_make_db_row({'context': ctx})]
        result = tracker.get_error_details(1)
        assert result.context == {'key': 'value'}

    def test_resolved_flag_cast_to_bool(self, tracker, mock_db):
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.return_value = [_make_db_row({'resolved': 1})]
        result = tracker.get_error_details(1)
        assert result.resolved is True


# ===========================================================================
# mark_resolved
# ===========================================================================

class TestMarkResolved:
    def test_returns_true_on_success(self, tracker, mock_db):
        result = tracker.mark_resolved(42)
        assert result is True
        mock_db.execute_write.assert_called_once()

    def test_passes_resolution_notes(self, tracker, mock_db):
        tracker.mark_resolved(5, resolution_notes="Fixed in v1.2")
        args = mock_db.execute_write.call_args[0][1]
        assert args[0] == "Fixed in v1.2"

    def test_returns_false_on_db_error(self, tracker, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("constraint violated")
        result = tracker.mark_resolved(99)
        assert result is False

    def test_resolution_notes_defaults_to_none(self, tracker, mock_db):
        tracker.mark_resolved(3)
        args = mock_db.execute_write.call_args[0][1]
        assert args[0] is None


# ===========================================================================
# cleanup_old_errors
# ===========================================================================

class TestCleanupOldErrors:
    def test_returns_zero_when_no_old_errors(self, tracker, mock_db):
        mock_db.execute_query.return_value = [{'count': 0}]
        count = tracker.cleanup_old_errors()
        assert count == 0
        # execute_write should NOT have been called
        mock_db.execute_write.assert_not_called()

    def test_deletes_and_returns_count(self, tracker, mock_db):
        mock_db.execute_query.return_value = [{'count': 7}]
        count = tracker.cleanup_old_errors(retention_days=30)
        assert count == 7
        mock_db.execute_write.assert_called_once()

    def test_returns_zero_when_count_result_empty(self, tracker, mock_db):
        mock_db.execute_query.return_value = []
        count = tracker.cleanup_old_errors()
        assert count == 0
        mock_db.execute_write.assert_not_called()

    def test_custom_retention_days(self, tracker, mock_db):
        mock_db.execute_query.return_value = [{'count': 3}]
        count = tracker.cleanup_old_errors(retention_days=180)
        assert count == 3


# ===========================================================================
# track_exceptions decorator
# ===========================================================================

class TestTrackExceptionsDecorator:
    def test_reraises_exception(self):
        @track_exceptions(severity='critical')
        def boom():
            raise RuntimeError("boom")

        with patch.object(error_tracker, 'capture_exception', return_value=1):
            with pytest.raises(RuntimeError, match="boom"):
                boom()

    def test_capture_exception_called_on_failure(self):
        @track_exceptions(severity='warning')
        def fail():
            raise ValueError("fail value")

        with patch.object(error_tracker, 'capture_exception', return_value=2) as mock_cap:
            with pytest.raises(ValueError):
                fail()
            mock_cap.assert_called_once()
            # First positional arg is the exception
            exc_arg = mock_cap.call_args[0][0]
            assert isinstance(exc_arg, ValueError)

    def test_passes_severity_to_capture(self):
        @track_exceptions(severity='critical')
        def critical_fn():
            raise OSError("disk full")

        with patch.object(error_tracker, 'capture_exception', return_value=3) as mock_cap:
            with pytest.raises(OSError):
                critical_fn()
            kwargs = mock_cap.call_args[1]
            assert kwargs.get('severity') == 'critical'

    def test_passthrough_return_value(self):
        @track_exceptions()
        def good():
            return 42

        assert good() == 42

    def test_passthrough_with_args(self):
        @track_exceptions()
        def add(a, b):
            return a + b

        assert add(3, 4) == 7

    def test_context_includes_function_name(self):
        @track_exceptions(severity='error')
        def named_fn():
            raise TypeError("type mismatch")

        with patch.object(error_tracker, 'capture_exception', return_value=1) as mock_cap:
            with pytest.raises(TypeError):
                named_fn()
            kwargs = mock_cap.call_args[1]
            assert kwargs['context']['function'] == 'named_fn'

    def test_default_severity_is_error(self):
        @track_exceptions()
        def default_fn():
            raise AttributeError("no attr")

        with patch.object(error_tracker, 'capture_exception', return_value=1) as mock_cap:
            with pytest.raises(AttributeError):
                default_fn()
            kwargs = mock_cap.call_args[1]
            assert kwargs['severity'] == 'error'
