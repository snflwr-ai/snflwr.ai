"""
Tests for Safety Monitor Module

Comprehensive tests for MonitoringProfile, SafetyAlert, AlertSeverity,
and SafetyMonitor classes in safety/safety_monitor.py.
"""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import sys

import pytest

from safety.safety_monitor import (
    AlertSeverity,
    MonitoringProfile,
    SafetyAlert,
    SafetyMonitor,
)
from safety.pipeline import Severity, Category, SafetyResult

_safety_monitor_mod = sys.modules["safety.safety_monitor"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_result(is_safe=True, severity=Severity.NONE, reason="ok"):
    """Build a SafetyResult with sensible defaults."""
    return SafetyResult(
        is_safe=is_safe,
        severity=severity,
        category=Category.VALID if is_safe else Category.VIOLENCE,
        reason=reason,
    )


def _unsafe_result(severity=Severity.MAJOR, reason="blocked"):
    return _safe_result(is_safe=False, severity=severity, reason=reason)


def _make_monitor(db=None, pipeline=None):
    """Create a SafetyMonitor with mocked dependencies."""
    mock_db = db or MagicMock()
    with patch.object(_safety_monitor_mod, "safety_pipeline", pipeline or MagicMock()):
        monitor = SafetyMonitor(db=mock_db)
    return monitor


# ============================================================================
# TestMonitoringProfile
# ============================================================================

class TestMonitoringProfile:
    """Tests for the MonitoringProfile dataclass."""

    def test_default_field_values(self):
        profile = MonitoringProfile(profile_id="child1", parent_id="parent1")
        assert profile.profile_id == "child1"
        assert profile.parent_id == "parent1"
        assert profile.minor_incidents == 0
        assert profile.major_incidents == 0
        assert profile.critical_incidents == 0
        assert profile.last_incident_time is None
        assert profile.alert_sent is False
        assert isinstance(profile.monitoring_start, datetime)

    def test_get_total_incidents_zero(self):
        profile = MonitoringProfile(profile_id="c", parent_id="p")
        assert profile.get_total_incidents() == 0

    def test_get_total_incidents_sums_all(self):
        profile = MonitoringProfile(
            profile_id="c",
            parent_id="p",
            minor_incidents=2,
            major_incidents=3,
            critical_incidents=1,
        )
        assert profile.get_total_incidents() == 6

    @patch.object(_safety_monitor_mod, "safety_config")
    def test_should_alert_parent_critical_threshold(self, mock_cfg):
        mock_cfg.ALERT_THRESHOLD_CRITICAL = 1
        mock_cfg.ALERT_THRESHOLD_MAJOR = 3
        mock_cfg.ALERT_THRESHOLD_MINOR = 10
        profile = MonitoringProfile(
            profile_id="c", parent_id="p", critical_incidents=3
        )
        assert profile.should_alert_parent() is True

    @patch.object(_safety_monitor_mod, "safety_config")
    def test_should_alert_parent_major_threshold(self, mock_cfg):
        mock_cfg.ALERT_THRESHOLD_CRITICAL = 1
        mock_cfg.ALERT_THRESHOLD_MAJOR = 3
        mock_cfg.ALERT_THRESHOLD_MINOR = 10
        profile = MonitoringProfile(
            profile_id="c", parent_id="p", major_incidents=3
        )
        assert profile.should_alert_parent() is True

    @patch.object(_safety_monitor_mod, "safety_config")
    def test_should_alert_parent_minor_threshold(self, mock_cfg):
        mock_cfg.ALERT_THRESHOLD_CRITICAL = 1
        mock_cfg.ALERT_THRESHOLD_MAJOR = 3
        mock_cfg.ALERT_THRESHOLD_MINOR = 10
        profile = MonitoringProfile(
            profile_id="c", parent_id="p", minor_incidents=10
        )
        assert profile.should_alert_parent() is True

    @patch.object(_safety_monitor_mod, "safety_config")
    def test_should_alert_parent_below_all_thresholds(self, mock_cfg):
        mock_cfg.ALERT_THRESHOLD_CRITICAL = 1
        mock_cfg.ALERT_THRESHOLD_MAJOR = 3
        mock_cfg.ALERT_THRESHOLD_MINOR = 10
        profile = MonitoringProfile(
            profile_id="c",
            parent_id="p",
            minor_incidents=2,
            major_incidents=1,
            critical_incidents=0,
        )
        assert profile.should_alert_parent() is False

    def test_to_dict_keys(self):
        profile = MonitoringProfile(profile_id="c", parent_id="p")
        d = profile.to_dict()
        expected_keys = {
            "profile_id",
            "parent_id",
            "minor_incidents",
            "major_incidents",
            "critical_incidents",
            "total_incidents",
            "last_incident",
            "alert_sent",
            "monitoring_duration_minutes",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_last_incident_none(self):
        profile = MonitoringProfile(profile_id="c", parent_id="p")
        assert profile.to_dict()["last_incident"] is None

    def test_to_dict_last_incident_iso(self):
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        profile = MonitoringProfile(
            profile_id="c", parent_id="p", last_incident_time=ts
        )
        assert profile.to_dict()["last_incident"] == ts.isoformat()

    def test_to_dict_total_incidents_matches(self):
        profile = MonitoringProfile(
            profile_id="c",
            parent_id="p",
            minor_incidents=1,
            major_incidents=2,
            critical_incidents=3,
        )
        d = profile.to_dict()
        assert d["total_incidents"] == 6

    def test_to_dict_monitoring_duration_positive(self):
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        profile = MonitoringProfile(
            profile_id="c", parent_id="p", monitoring_start=start
        )
        d = profile.to_dict()
        assert d["monitoring_duration_minutes"] >= 4.9


# ============================================================================
# TestSafetyAlert
# ============================================================================

class TestSafetyAlert:
    """Tests for the SafetyAlert dataclass."""

    def _make_alert(self, **overrides):
        defaults = dict(
            alert_id="abc123",
            profile_id="child1",
            parent_id="parent1",
            severity="high",
            incident_count=5,
            description="test alert",
            timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            conversation_snippet="hello...",
            requires_action=True,
        )
        defaults.update(overrides)
        return SafetyAlert(**defaults)

    def test_default_optional_fields(self):
        alert = SafetyAlert(
            alert_id="a",
            profile_id="c",
            parent_id="p",
            severity="low",
            incident_count=1,
            description="desc",
            timestamp=datetime.now(timezone.utc),
        )
        assert alert.conversation_snippet is None
        assert alert.requires_action is False

    def test_to_dict_keys(self):
        alert = self._make_alert()
        d = alert.to_dict()
        expected_keys = {
            "alert_id",
            "profile_id",
            "parent_id",
            "severity",
            "incident_count",
            "description",
            "timestamp",
            "conversation_snippet",
            "requires_action",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_timestamp_iso(self):
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        alert = self._make_alert(timestamp=ts)
        assert alert.to_dict()["timestamp"] == ts.isoformat()

    def test_to_dict_values(self):
        alert = self._make_alert()
        d = alert.to_dict()
        assert d["alert_id"] == "abc123"
        assert d["severity"] == "high"
        assert d["requires_action"] is True
        assert d["conversation_snippet"] == "hello..."


# ============================================================================
# TestAlertSeverityEnum
# ============================================================================

class TestAlertSeverityEnum:
    """Tests for the AlertSeverity enum."""

    def test_enum_values(self):
        assert AlertSeverity.LOW.value == "low"
        assert AlertSeverity.MEDIUM.value == "medium"
        assert AlertSeverity.HIGH.value == "high"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_enum_members_count(self):
        assert len(AlertSeverity) == 4


# ============================================================================
# TestSafetyMonitorInit
# ============================================================================

class TestSafetyMonitorInit:
    """Tests for SafetyMonitor constructor."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    @patch.object(_safety_monitor_mod, "db_manager")
    def test_uses_injected_db(self, mock_db_manager, mock_pipeline):
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        assert monitor.db is mock_db

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    @patch.object(_safety_monitor_mod, "db_manager")
    def test_falls_back_to_module_db_manager(self, mock_db_manager, mock_pipeline):
        monitor = SafetyMonitor()
        assert monitor.db is mock_db_manager

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_filter_is_safety_pipeline(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor.filter is mock_pipeline

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_initial_state_empty(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor._monitoring_profiles == {}
        assert monitor._pending_alerts == []
        assert len(monitor._pattern_detectors) == 4

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_pattern_detectors_names(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        names = {p["name"] for p in monitor._pattern_detectors}
        assert names == {
            "repeated_prohibited_content",
            "escalating_requests",
            "persistent_off_topic",
            "distress_indicators",
        }


# ============================================================================
# TestStartStopMonitoring
# ============================================================================

class TestStartStopMonitoring:
    """Tests for start_monitoring / stop_monitoring / cleanup_inactive_profiles."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_start_monitoring_creates_profile(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        assert "child1" in monitor._monitoring_profiles
        p = monitor._monitoring_profiles["child1"]
        assert p.parent_id == "parent1"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_start_monitoring_idempotent(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        original = monitor._monitoring_profiles["child1"]
        monitor.start_monitoring("child1", "parent2")
        # Should NOT overwrite existing profile
        assert monitor._monitoring_profiles["child1"] is original
        assert monitor._monitoring_profiles["child1"].parent_id == "parent1"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_stop_monitoring_removes_profile(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        monitor.stop_monitoring("child1")
        assert "child1" not in monitor._monitoring_profiles

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_stop_monitoring_cleans_conversation_history(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        monitor._conversation_history["child1"].append("hello")
        monitor.stop_monitoring("child1")
        assert "child1" not in monitor._conversation_history

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_stop_monitoring_nonexistent_profile_no_error(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.stop_monitoring("nonexistent")  # Should not raise

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_thread_safety_start(self, mock_pipeline):
        """Multiple threads calling start_monitoring should not corrupt state."""
        monitor = SafetyMonitor(db=MagicMock())
        errors = []

        def start_profile(i):
            try:
                monitor.start_monitoring(f"child_{i}", f"parent_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=start_profile, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(monitor._monitoring_profiles) == 20


# ============================================================================
# TestCleanup
# ============================================================================

class TestCleanup:
    """Tests for cleanup_inactive_profiles."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_removes_history_for_unmonitored_profiles(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        # Add conversation history without a monitoring profile
        monitor._conversation_history["orphan1"].append("msg")
        monitor._conversation_history["orphan2"].append("msg")
        # Add one that IS monitored
        monitor.start_monitoring("active", "parent")
        monitor._conversation_history["active"].append("msg")

        monitor.cleanup_inactive_profiles()

        assert "orphan1" not in monitor._conversation_history
        assert "orphan2" not in monitor._conversation_history
        assert "active" in monitor._conversation_history

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_cleanup_no_inactive_is_noop(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        monitor._conversation_history["child1"].append("msg")
        monitor.cleanup_inactive_profiles()
        assert "child1" in monitor._conversation_history


# ============================================================================
# TestMonitorMessage
# ============================================================================

class TestMonitorMessage:
    """Tests for SafetyMonitor.monitor_message."""

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_safe_message_returns_none(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _safe_result()
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        result = monitor.monitor_message("child1", "hello", age=10)

        assert result is None

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_returns_safety_alert(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(
            severity=Severity.MAJOR, reason="violence detected"
        )
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        result = monitor.monitor_message("child1", "bad content", age=10)

        assert isinstance(result, SafetyAlert)
        assert result.profile_id == "child1"
        assert result.parent_id == "parent1"
        assert result.description == "violence detected"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_records_incident_in_db(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result()
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "bad stuff", age=10, session_id="sess1")

        mock_db.execute_write.assert_called()
        call_args = mock_db.execute_write.call_args
        assert "INSERT INTO safety_incidents" in call_args[0][0]

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_calls_log_safety_incident(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(reason="test reason")
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "msg", age=10, session_id="s1")

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["incident_type"] == "test reason"
        assert call_kwargs["profile_id"] == "child1"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_auto_starts_monitoring_unknown_profile(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _safe_result()
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"parent_id": "parent_from_db", "name": "Kid", "age": 10}]
        monitor = SafetyMonitor(db=mock_db)

        result = monitor.monitor_message("new_child", "hi", age=10)

        assert result is None
        assert "new_child" in monitor._monitoring_profiles
        assert monitor._monitoring_profiles["new_child"].parent_id == "parent_from_db"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_auto_start_db_lookup_failure_uses_unknown(self, mock_pipeline, mock_log):
        """When DB lookup fails, parent_id defaults to 'unknown'."""
        import sqlite3
        mock_pipeline.check_input.return_value = _safe_result()
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")
        monitor = SafetyMonitor(db=mock_db)

        result = monitor.monitor_message("new_child", "hi", age=10)

        assert "new_child" in monitor._monitoring_profiles
        assert monitor._monitoring_profiles["new_child"].parent_id == "unknown"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_auto_start_db_empty_result_uses_unknown(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _safe_result()
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        monitor = SafetyMonitor(db=mock_db)

        monitor.monitor_message("new_child", "hi", age=10)

        assert monitor._monitoring_profiles["new_child"].parent_id == "unknown"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_keeps_last_20_messages(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _safe_result()
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        for i in range(25):
            monitor.monitor_message("child1", f"message_{i}", age=10)

        assert len(monitor._conversation_history["child1"]) == 20
        assert monitor._conversation_history["child1"][0] == "message_5"
        assert monitor._conversation_history["child1"][-1] == "message_24"

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_default_age_when_none(self, mock_pipeline, mock_log):
        """When age is None, the pipeline should be called with age=12."""
        mock_pipeline.check_input.return_value = _safe_result()
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "hello", age=None)

        mock_pipeline.check_input.assert_called_once_with("hello", 12, "child1")

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_uses_provided_age(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _safe_result()
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "hello", age=8)

        mock_pipeline.check_input.assert_called_once_with("hello", 8, "child1")

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_fail_closed_on_exception(self, mock_pipeline):
        """Any unhandled exception should fail closed."""
        mock_pipeline.check_input.side_effect = RuntimeError("boom")
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        result = monitor.monitor_message("child1", "hello", age=10)

        assert result is None

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_increments_major_incidents(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MAJOR)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "bad", age=10)

        profile = monitor._monitoring_profiles["child1"]
        assert profile.major_incidents == 1

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_increments_minor_incidents(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MINOR)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "bad", age=10)

        profile = monitor._monitoring_profiles["child1"]
        assert profile.minor_incidents == 1

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_increments_critical_incidents(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.CRITICAL)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "dangerous", age=10)

        profile = monitor._monitoring_profiles["child1"]
        assert profile.critical_incidents == 1

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_sets_last_incident_time(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result()
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        before = datetime.now(timezone.utc)
        monitor.monitor_message("child1", "bad", age=10)
        after = datetime.now(timezone.utc)

        profile = monitor._monitoring_profiles["child1"]
        assert profile.last_incident_time is not None
        assert before <= profile.last_incident_time <= after

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_parent_alert_created_when_threshold_reached(
        self, mock_pipeline, mock_cfg, mock_email_svc, mock_log
    ):
        mock_cfg.ALERT_THRESHOLD_CRITICAL = 1
        mock_cfg.ALERT_THRESHOLD_MAJOR = 1
        mock_cfg.ALERT_THRESHOLD_MINOR = 10
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MAJOR)
        mock_email = MagicMock()
        mock_email.send_safety_alert.return_value = (True, None)
        mock_email_svc.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Kid"}]
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        monitor.monitor_message("child1", "bad content", age=10)

        # Alert should have been created and profile marked
        profile = monitor._monitoring_profiles["child1"]
        assert profile.alert_sent is True
        # At least one pending alert for this profile
        assert len(monitor._pending_alerts) >= 1

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_message_alert_added_to_pending(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MAJOR)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        alert = monitor.monitor_message("child1", "bad", age=10)

        assert isinstance(alert, SafetyAlert)
        assert alert in monitor._pending_alerts

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_safe_message_runs_pattern_detection(self, mock_pipeline, mock_log):
        """When message is safe, pattern detection should still run."""
        mock_pipeline.check_input.return_value = _safe_result()
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        with patch.object(monitor, "_run_pattern_detection", return_value=None) as mock_detect:
            monitor.monitor_message("child1", "hello", age=10)
            mock_detect.assert_called_once()

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_pattern_detection_triggers_incident(self, mock_pipeline, mock_log):
        """When pattern detection finds something, an incident is recorded."""
        mock_pipeline.check_input.return_value = _safe_result()
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        with patch.object(
            monitor, "_run_pattern_detection", return_value=("minor", "Off-topic detected")
        ):
            result = monitor.monitor_message("child1", "hello", age=10)

        # Pattern detected but no unsafe content — returns None (no alert object)
        assert result is None
        # But an incident should have been logged
        mock_db.execute_write.assert_called()

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_critical_severity_maps_to_critical_alert(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.CRITICAL)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        alert = monitor.monitor_message("child1", "danger", age=10)

        assert isinstance(alert, SafetyAlert)
        assert alert.severity == AlertSeverity.CRITICAL.value
        assert alert.requires_action is True

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_major_severity_maps_to_high_alert(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MAJOR)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        alert = monitor.monitor_message("child1", "bad", age=10)

        assert isinstance(alert, SafetyAlert)
        assert alert.severity == AlertSeverity.HIGH.value

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_unsafe_minor_severity_maps_to_medium_alert(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result(severity=Severity.MINOR)
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        alert = monitor.monitor_message("child1", "mild", age=10)

        assert isinstance(alert, SafetyAlert)
        assert alert.severity == AlertSeverity.MEDIUM.value

    @patch.object(_safety_monitor_mod, "log_safety_incident")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_conversation_snippet_truncated_to_200(self, mock_pipeline, mock_log):
        mock_pipeline.check_input.return_value = _unsafe_result()
        mock_db = MagicMock()
        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")

        long_msg = "x" * 500
        alert = monitor.monitor_message("child1", long_msg, age=10)

        assert isinstance(alert, SafetyAlert)
        assert len(alert.conversation_snippet) == 200


# ============================================================================
# TestPatternDetection
# ============================================================================

class TestPatternDetection:
    """Tests for pattern detection methods."""

    # -- _detect_repeated_prohibited --

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_repeated_prohibited_false_under_5_messages(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d"]
        assert monitor._detect_repeated_prohibited("child1") is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_repeated_prohibited_true_3_of_5_unsafe(self, mock_pipeline):
        # First call is from constructor, subsequent from detection
        mock_pipeline.check_input.side_effect = [
            _unsafe_result(),
            _unsafe_result(),
            _unsafe_result(),
            _safe_result(),
            _safe_result(),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d", "e"]

        result = monitor._detect_repeated_prohibited("child1")

        assert result is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_repeated_prohibited_false_only_2_unsafe(self, mock_pipeline):
        mock_pipeline.check_input.side_effect = [
            _unsafe_result(),
            _unsafe_result(),
            _safe_result(),
            _safe_result(),
            _safe_result(),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d", "e"]

        result = monitor._detect_repeated_prohibited("child1")

        assert result is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_repeated_prohibited_checks_last_5(self, mock_pipeline):
        """Should only check the last 5 messages, not earlier ones."""
        # 7 messages, but only last 5 matter
        mock_pipeline.check_input.side_effect = [
            _safe_result(),
            _safe_result(),
            _safe_result(),
            _safe_result(),
            _safe_result(),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [
            "old_bad1", "old_bad2", "ok1", "ok2", "ok3", "ok4", "ok5"
        ]

        result = monitor._detect_repeated_prohibited("child1")

        assert result is False
        # Should have been called 5 times (for the last 5)
        assert mock_pipeline.check_input.call_count == 5

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_repeated_prohibited_empty_history(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor._detect_repeated_prohibited("child1") is False

    # -- _detect_escalating_requests --

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_escalating_false_under_4_messages(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c"]
        assert monitor._detect_escalating_requests("child1") is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_escalating_true_increasing_severity(self, mock_pipeline):
        mock_pipeline.check_input.side_effect = [
            _unsafe_result(severity=Severity.MINOR),
            _unsafe_result(severity=Severity.MAJOR),
            _unsafe_result(severity=Severity.MAJOR),
            _unsafe_result(severity=Severity.CRITICAL),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d"]

        result = monitor._detect_escalating_requests("child1")

        assert result is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_escalating_false_decreasing_severity(self, mock_pipeline):
        mock_pipeline.check_input.side_effect = [
            _unsafe_result(severity=Severity.CRITICAL),
            _unsafe_result(severity=Severity.MAJOR),
            _unsafe_result(severity=Severity.MINOR),
            _safe_result(),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d"]

        result = monitor._detect_escalating_requests("child1")

        # Only 3 unsafe results, and last severity (1) > first severity (3) is False
        assert result is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_escalating_false_all_safe(self, mock_pipeline):
        mock_pipeline.check_input.side_effect = [
            _safe_result(), _safe_result(), _safe_result(), _safe_result()
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d"]

        result = monitor._detect_escalating_requests("child1")

        # No unsafe results means severities list is empty, len < 3
        assert result is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_escalating_false_fewer_than_3_unsafe(self, mock_pipeline):
        mock_pipeline.check_input.side_effect = [
            _unsafe_result(severity=Severity.MINOR),
            _safe_result(),
            _unsafe_result(severity=Severity.MAJOR),
            _safe_result(),
        ]
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c", "d"]

        result = monitor._detect_escalating_requests("child1")

        assert result is False

    # -- _detect_persistent_off_topic --

    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_off_topic_false_under_6_messages(self, mock_pipeline, mock_cfg):
        mock_cfg.REDIRECT_TOPICS = {"politics": "civic learning"}
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["politics"] * 5
        assert monitor._detect_persistent_off_topic("child1") is False

    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_off_topic_true_4_of_6_match(self, mock_pipeline, mock_cfg):
        mock_cfg.REDIRECT_TOPICS = {"politics": "civic learning", "religion": "culture"}
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [
            "tell me about politics",
            "more politics please",
            "math homework",
            "what about religion",
            "politics again",
            "science class",
        ]

        result = monitor._detect_persistent_off_topic("child1")

        assert result is True

    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_off_topic_false_under_4_matches(self, mock_pipeline, mock_cfg):
        mock_cfg.REDIRECT_TOPICS = {"politics": "civic learning"}
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [
            "politics",
            "math",
            "science",
            "politics",
            "art",
            "politics",
        ]

        result = monitor._detect_persistent_off_topic("child1")

        assert result is False

    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_off_topic_case_insensitive(self, mock_pipeline, mock_cfg):
        mock_cfg.REDIRECT_TOPICS = {"politics": "civic learning"}
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [
            "POLITICS",
            "Politics",
            "math",
            "PoLiTiCs",
            "poLITICS",
            "other",
        ]

        result = monitor._detect_persistent_off_topic("child1")

        assert result is True

    @patch.object(_safety_monitor_mod, "safety_config")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_off_topic_checks_last_6_only(self, mock_pipeline, mock_cfg):
        mock_cfg.REDIRECT_TOPICS = {"politics": "civic learning"}
        monitor = SafetyMonitor(db=MagicMock())
        # 10 messages, but only last 6 should be checked
        monitor._conversation_history["child1"] = [
            "politics", "politics", "politics", "politics",  # old
            "math", "math", "math", "math", "math", "math",  # recent 6
        ]

        result = monitor._detect_persistent_off_topic("child1")

        assert result is False

    # -- _detect_distress_indicators --

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_empty_history_false(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor._detect_distress_indicators("child1") is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_detects_help_me(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["please help me"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_detects_scared(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["I am scared"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_detects_afraid(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["I'm so afraid"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_detects_depressed(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["I feel depressed"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_detects_nobody_cares(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["nobody cares about me"]
        assert monitor._detect_distress_indicators("child1") is True

    @pytest.mark.parametrize("keyword", [
        "help me", "scared", "afraid", "worried", "sad", "depressed", "alone", "nobody cares"
    ])
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_all_keywords(self, mock_pipeline, keyword):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [f"I feel {keyword}"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_case_insensitive(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["I AM SCARED"]
        assert monitor._detect_distress_indicators("child1") is True

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_checks_last_3_messages_only(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = [
            "I am scared",  # old (outside last 3)
            "math is fun",
            "science rules",
            "coding is cool",
        ]
        assert monitor._detect_distress_indicators("child1") is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_distress_no_keywords_false(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["I love math", "Science is great"]
        assert monitor._detect_distress_indicators("child1") is False


# ============================================================================
# TestCheckForPatterns
# ============================================================================

class TestCheckForPatterns:
    """Tests for check_for_patterns (public wrapper)."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_none_when_no_profile(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor.check_for_patterns("nonexistent") is None

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_none_when_no_pattern(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        with patch.object(monitor, "_run_pattern_detection", return_value=None):
            assert monitor.check_for_patterns("child1") is None

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_alert_when_pattern_found(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        with patch.object(
            monitor,
            "_run_pattern_detection",
            return_value=("major", "Escalating requests"),
        ):
            alert = monitor.check_for_patterns("child1")

        assert isinstance(alert, SafetyAlert)
        assert alert.description == "Escalating requests"
        assert alert.severity == AlertSeverity.HIGH.value

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_critical_pattern_sets_requires_action(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        with patch.object(
            monitor,
            "_run_pattern_detection",
            return_value=("critical", "Distress indicators"),
        ):
            alert = monitor.check_for_patterns("child1")

        assert alert.requires_action is True
        assert alert.severity == AlertSeverity.CRITICAL.value

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_pattern_alert_added_to_pending(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        with patch.object(
            monitor,
            "_run_pattern_detection",
            return_value=("minor", "Off-topic"),
        ):
            alert = monitor.check_for_patterns("child1")

        assert alert in monitor._pending_alerts


# ============================================================================
# TestAlerts
# ============================================================================

class TestAlerts:
    """Tests for alert management: get_pending_alerts, acknowledge_alert, get_latest_alert."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_pending_alerts_empty(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor.get_pending_alerts() == []

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_pending_alerts_all(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        a2 = SafetyAlert(
            alert_id="a2", profile_id="c2", parent_id="p2",
            severity="high", incident_count=2, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1, a2]

        result = monitor.get_pending_alerts()
        assert len(result) == 2

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_pending_alerts_filtered_by_parent(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        a2 = SafetyAlert(
            alert_id="a2", profile_id="c2", parent_id="p2",
            severity="high", incident_count=2, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1, a2]

        result = monitor.get_pending_alerts(parent_id="p1")
        assert len(result) == 1
        assert result[0].alert_id == "a1"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_pending_alerts_no_match(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1]

        result = monitor.get_pending_alerts(parent_id="p999")
        assert result == []

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_acknowledge_alert_removes_it(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1]

        result = monitor.acknowledge_alert("a1")
        assert result is True
        assert len(monitor._pending_alerts) == 0

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_acknowledge_alert_nonexistent_returns_false(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        result = monitor.acknowledge_alert("nonexistent")
        assert result is False

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_acknowledge_only_removes_matching(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        a2 = SafetyAlert(
            alert_id="a2", profile_id="c2", parent_id="p2",
            severity="high", incident_count=2, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1, a2]

        monitor.acknowledge_alert("a1")

        assert len(monitor._pending_alerts) == 1
        assert monitor._pending_alerts[0].alert_id == "a2"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_latest_alert_returns_most_recent(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="first",
            timestamp=datetime.now(timezone.utc)
        )
        a2 = SafetyAlert(
            alert_id="a2", profile_id="c1", parent_id="p1",
            severity="high", incident_count=2, description="second",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1, a2]

        result = monitor.get_latest_alert("c1")
        assert result.alert_id == "a2"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_latest_alert_returns_none_no_match(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor.get_latest_alert("c1") is None

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_get_latest_alert_filters_by_profile(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="low", incident_count=1, description="c1 alert",
            timestamp=datetime.now(timezone.utc)
        )
        a2 = SafetyAlert(
            alert_id="a2", profile_id="c2", parent_id="p2",
            severity="high", incident_count=2, description="c2 alert",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1, a2]

        result = monitor.get_latest_alert("c2")
        assert result.alert_id == "a2"
        assert result.profile_id == "c2"


# ============================================================================
# TestStatistics
# ============================================================================

class TestStatistics:
    """Tests for get_profile_statistics and get_system_statistics."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_profile_statistics_returns_dict_for_existing(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        stats = monitor.get_profile_statistics("child1")
        assert isinstance(stats, dict)
        assert stats["profile_id"] == "child1"
        assert stats["parent_id"] == "parent1"
        assert stats["minor_incidents"] == 0

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_profile_statistics_empty_for_unknown(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor.get_profile_statistics("nonexistent") == {}

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_system_statistics_empty(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        stats = monitor.get_system_statistics()
        assert stats["monitored_profiles"] == 0
        assert stats["total_incidents"] == 0
        assert stats["profiles_with_incidents"] == 0
        assert stats["pending_alerts"] == 0
        assert stats["pattern_detectors"] == 4

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_system_statistics_with_data(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("c1", "p1")
        monitor.start_monitoring("c2", "p2")
        monitor._monitoring_profiles["c1"].minor_incidents = 3
        monitor._monitoring_profiles["c1"].major_incidents = 1

        a1 = SafetyAlert(
            alert_id="a1", profile_id="c1", parent_id="p1",
            severity="high", incident_count=4, description="d",
            timestamp=datetime.now(timezone.utc)
        )
        monitor._pending_alerts = [a1]

        stats = monitor.get_system_statistics()
        assert stats["monitored_profiles"] == 2
        assert stats["total_incidents"] == 4
        assert stats["profiles_with_incidents"] == 1
        assert stats["pending_alerts"] == 1

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_profile_statistics_matches_to_dict(self, mock_pipeline):
        """get_profile_statistics should return the same data as profile.to_dict()."""
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")
        monitor._monitoring_profiles["child1"].minor_incidents = 2

        stats = monitor.get_profile_statistics("child1")
        profile_dict = monitor._monitoring_profiles["child1"].to_dict()

        assert stats["minor_incidents"] == profile_dict["minor_incidents"]
        assert stats["total_incidents"] == profile_dict["total_incidents"]


# ============================================================================
# TestRunPatternDetection
# ============================================================================

class TestRunPatternDetection:
    """Tests for _run_pattern_detection."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_none_when_no_patterns_match(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        # Patch all detectors to return False
        for pattern in monitor._pattern_detectors:
            pattern["detector"] = MagicMock(return_value=False)

        profile = monitor._monitoring_profiles["child1"]
        result = monitor._run_pattern_detection("child1", profile)
        assert result is None

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_first_matching_pattern(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        # First detector matches
        monitor._pattern_detectors[0]["detector"] = MagicMock(return_value=True)
        for p in monitor._pattern_detectors[1:]:
            p["detector"] = MagicMock(return_value=False)

        profile = monitor._monitoring_profiles["child1"]
        result = monitor._run_pattern_detection("child1", profile)

        assert result is not None
        severity, description = result
        assert severity == monitor._pattern_detectors[0]["severity"]
        assert description == monitor._pattern_detectors[0]["description"]

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_short_circuits_on_first_match(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor.start_monitoring("child1", "parent1")

        # First detector matches; second should not be called
        mock_first = MagicMock(return_value=True)
        mock_second = MagicMock(return_value=False)
        monitor._pattern_detectors[0]["detector"] = mock_first
        monitor._pattern_detectors[1]["detector"] = mock_second

        profile = monitor._monitoring_profiles["child1"]
        monitor._run_pattern_detection("child1", profile)

        mock_first.assert_called_once()
        mock_second.assert_not_called()


# ============================================================================
# TestCreateParentAlert (internal method)
# ============================================================================

class TestCreateParentAlert:
    """Tests for _create_parent_alert."""

    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_creates_alert_and_marks_sent(self, mock_pipeline, mock_get_email):
        mock_email = MagicMock()
        mock_email.send_safety_alert.return_value = (True, None)
        mock_get_email.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "TestKid"}]

        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")
        profile = monitor._monitoring_profiles["child1"]
        profile.major_incidents = 2

        monitor._create_parent_alert(profile, "sess1")

        assert profile.alert_sent is True
        assert len(monitor._pending_alerts) >= 1
        # DB should be updated
        mock_db.execute_write.assert_called()

    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_alert_severity_critical_when_critical_incidents(self, mock_pipeline, mock_get_email):
        mock_email = MagicMock()
        mock_email.send_safety_alert.return_value = (True, None)
        mock_get_email.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Kid"}]

        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")
        profile = monitor._monitoring_profiles["child1"]
        profile.critical_incidents = 1

        monitor._create_parent_alert(profile, "sess1")

        alert = [a for a in monitor._pending_alerts if a.profile_id == "child1"][-1]
        assert alert.severity == "critical"
        assert alert.requires_action is True

    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_alert_severity_high_when_major_ge_2(self, mock_pipeline, mock_get_email):
        mock_email = MagicMock()
        mock_email.send_safety_alert.return_value = (True, None)
        mock_get_email.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Kid"}]

        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")
        profile = monitor._monitoring_profiles["child1"]
        profile.major_incidents = 2

        monitor._create_parent_alert(profile, "sess1")

        alert = [a for a in monitor._pending_alerts if a.profile_id == "child1"][-1]
        assert alert.severity == "high"

    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_alert_severity_medium_otherwise(self, mock_pipeline, mock_get_email):
        mock_email = MagicMock()
        mock_email.send_safety_alert.return_value = (True, None)
        mock_get_email.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Kid"}]

        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")
        profile = monitor._monitoring_profiles["child1"]
        profile.minor_incidents = 5

        monitor._create_parent_alert(profile, "sess1")

        alert = [a for a in monitor._pending_alerts if a.profile_id == "child1"][-1]
        assert alert.severity == "medium"

    @patch.object(_safety_monitor_mod, "_get_email_service")
    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_email_failure_does_not_prevent_alert(self, mock_pipeline, mock_get_email):
        """Email failure should not stop alert creation."""
        import smtplib
        mock_email = MagicMock()
        mock_email.send_safety_alert.side_effect = smtplib.SMTPException("fail")
        mock_get_email.return_value = mock_email
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Kid"}]

        monitor = SafetyMonitor(db=mock_db)
        monitor.start_monitoring("child1", "parent1")
        profile = monitor._monitoring_profiles["child1"]
        profile.major_incidents = 1

        monitor._create_parent_alert(profile, "sess1")

        # Alert should still exist
        assert profile.alert_sent is True
        assert len(monitor._pending_alerts) >= 1


# ============================================================================
# TestGenerateAlertDescription
# ============================================================================

class TestGenerateAlertDescription:
    """Tests for _generate_alert_description."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_critical_description(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        profile = MonitoringProfile(
            profile_id="c", parent_id="p",
            critical_incidents=2, major_incidents=1
        )
        desc = monitor._generate_alert_description(profile)
        assert "Critical" in desc
        assert "2 critical" in desc
        assert "1 major" in desc

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_major_description(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        profile = MonitoringProfile(
            profile_id="c", parent_id="p",
            major_incidents=3, minor_incidents=2
        )
        desc = monitor._generate_alert_description(profile)
        assert "Multiple" in desc
        assert "3 major" in desc

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_generic_description(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        profile = MonitoringProfile(
            profile_id="c", parent_id="p",
            minor_incidents=5
        )
        desc = monitor._generate_alert_description(profile)
        assert "requiring attention" in desc
        assert "5 total" in desc


# ============================================================================
# TestGetRecentConversationSnippet
# ============================================================================

class TestGetRecentConversationSnippet:
    """Tests for _get_recent_conversation_snippet."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_empty_history_returns_none(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        assert monitor._get_recent_conversation_snippet("child1") is None

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_last_3_messages(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["msg1", "msg2", "msg3", "msg4", "msg5"]

        snippet = monitor._get_recent_conversation_snippet("child1")

        assert "msg3" in snippet
        assert "msg4" in snippet
        assert "msg5" in snippet
        assert "msg1" not in snippet

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_truncates_long_messages(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        long_msg = "x" * 100
        monitor._conversation_history["child1"] = [long_msg]

        snippet = monitor._get_recent_conversation_snippet("child1")

        assert snippet.endswith("...")
        # Truncated to 50 chars + "..."
        assert len(snippet) == 53

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_short_messages_not_truncated(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["hi"]

        snippet = monitor._get_recent_conversation_snippet("child1")

        assert snippet == "hi"
        assert "..." not in snippet

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_messages_joined_by_pipe(self, mock_pipeline):
        monitor = SafetyMonitor(db=MagicMock())
        monitor._conversation_history["child1"] = ["a", "b", "c"]

        snippet = monitor._get_recent_conversation_snippet("child1")

        assert snippet == "a | b | c"


# ============================================================================
# TestGetChildProfileName
# ============================================================================

class TestGetChildProfileName:
    """Tests for _get_child_profile_name."""

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_name_from_db(self, mock_pipeline):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{"name": "Alice"}]
        monitor = SafetyMonitor(db=mock_db)

        name = monitor._get_child_profile_name("child1")
        assert name == "Alice"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_default_when_not_found(self, mock_pipeline):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []
        monitor = SafetyMonitor(db=mock_db)

        name = monitor._get_child_profile_name("child1")
        assert name == "Your Child"

    @patch.object(_safety_monitor_mod, "safety_pipeline")
    def test_returns_default_on_db_error(self, mock_pipeline):
        import sqlite3
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")
        monitor = SafetyMonitor(db=mock_db)

        name = monitor._get_child_profile_name("child1")
        assert name == "Your Child"
