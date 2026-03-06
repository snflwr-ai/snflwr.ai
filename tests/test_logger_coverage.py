"""
Tests for utils/logger.py — targeting uncovered lines to raise coverage above 85%.

Covers:
    - sanitize_log_value: newlines, carriage returns, non-string types
    - PIISanitizer disabled via LOG_PII_SANITIZE=false
    - PIISanitizer with tuple and dict args containing PII
    - set_user_context with session_id
    - set_correlation_id / get_correlation_id round-trip
    - CorrelationIDFilter context injection
    - SnflwrFormatter structured output (JSON) with extras and exception info
    - SnflwrFormatter color branch
    - PerformanceLogger metric tracking and statistics
    - SafetyLogger incident logging
    - mask_email edge cases
    - Module-level convenience functions
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# sanitize_log_value
# ---------------------------------------------------------------------------

class TestSanitizeLogValue:
    """Tests for the sanitize_log_value function (CWE-117 prevention)."""

    def test_replaces_newlines(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value("line1\nline2") == "line1\\nline2"

    def test_replaces_carriage_returns(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value("line1\rline2") == "line1\\rline2"

    def test_replaces_both(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value("a\r\nb") == "a\\r\\nb"

    def test_integer_input(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value(42) == "42"

    def test_none_input(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value(None) == "None"

    def test_clean_string_unchanged(self):
        from utils.logger import sanitize_log_value
        assert sanitize_log_value("hello world") == "hello world"


# ---------------------------------------------------------------------------
# PIISanitizer — disabled mode
# ---------------------------------------------------------------------------

class TestPIISanitizerDisabled:
    """When LOG_PII_SANITIZE=false, the filter should pass records through untouched."""

    def test_disabled_does_not_redact(self, monkeypatch):
        monkeypatch.setenv("LOG_PII_SANITIZE", "false")
        from utils.logger import PIISanitizer

        sanitizer = PIISanitizer()
        assert sanitizer.enabled is False

        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="user@example.com logged in", args=None, exc_info=None,
        )
        result = sanitizer.filter(record)
        assert result is True
        assert "user@example.com" in record.msg  # NOT redacted


# ---------------------------------------------------------------------------
# PIISanitizer — tuple and dict args
# ---------------------------------------------------------------------------

class TestPIISanitizerArgs:
    """Test PII redaction in record.args (tuple and dict forms)."""

    def _make_sanitizer(self):
        """Create an enabled PIISanitizer regardless of env."""
        from utils.logger import PIISanitizer
        s = PIISanitizer()
        s.enabled = True
        return s

    def test_tuple_args_email_redacted(self):
        sanitizer = self._make_sanitizer()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User %s logged in", args=("alice@example.com",), exc_info=None,
        )
        sanitizer.filter(record)
        assert record.args[0] == "[EMAIL_REDACTED]"

    def test_tuple_args_non_string_unchanged(self):
        sanitizer = self._make_sanitizer()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Count: %d", args=(42,), exc_info=None,
        )
        sanitizer.filter(record)
        assert record.args[0] == 42

    def test_dict_args_email_redacted(self):
        sanitizer = self._make_sanitizer()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User %(email)s logged in", args=None, exc_info=None,
        )
        # Set dict args after construction to avoid LogRecord.__init__ KeyError
        record.args = {"email": "bob@example.com"}
        sanitizer.filter(record)
        assert record.args["email"] == "[EMAIL_REDACTED]"

    def test_dict_args_non_string_unchanged(self):
        sanitizer = self._make_sanitizer()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Port %(port)s", args=None, exc_info=None,
        )
        record.args = {"port": 8080}
        sanitizer.filter(record)
        assert record.args["port"] == 8080

    def test_none_msg_does_not_crash(self):
        """Record with msg=None should not raise."""
        sanitizer = self._make_sanitizer()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=None, args=None, exc_info=None,
        )
        assert sanitizer.filter(record) is True


# ---------------------------------------------------------------------------
# set_user_context / set_correlation_id / get_correlation_id
# ---------------------------------------------------------------------------

class TestContextFunctions:

    def test_set_user_context_with_session_id(self):
        from utils.logger import set_user_context, user_id_var, session_id_var
        set_user_context("user-123", session_id="sess-456")
        assert user_id_var.get() == "user-123"
        assert session_id_var.get() == "sess-456"

    def test_set_user_context_without_session_id(self):
        from utils.logger import set_user_context, user_id_var, session_id_var
        # Reset session first
        session_id_var = __import__("utils.logger", fromlist=["session_id_var"]).session_id_var
        session_id_var.set(None)
        set_user_context("user-789")
        assert user_id_var.get() == "user-789"
        # session_id should remain None (not set)
        assert session_id_var.get() is None

    def test_correlation_id_round_trip(self):
        from utils.logger import set_correlation_id, get_correlation_id
        token = set_correlation_id("req-abc-123")
        assert get_correlation_id() == "req-abc-123"


# ---------------------------------------------------------------------------
# CorrelationIDFilter
# ---------------------------------------------------------------------------

class TestCorrelationIDFilter:

    def test_filter_injects_context_vars(self):
        from utils.logger import (
            CorrelationIDFilter, set_correlation_id,
            set_user_context, correlation_id_var,
        )
        set_correlation_id("corr-001")
        set_user_context("uid-001", session_id="sid-001")

        filt = CorrelationIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=None, exc_info=None,
        )
        result = filt.filter(record)
        assert result is True
        assert record.correlation_id == "corr-001"
        assert record.user_id == "uid-001"
        assert record.session_id == "sid-001"


# ---------------------------------------------------------------------------
# SnflwrFormatter — structured (JSON) output
# ---------------------------------------------------------------------------

class TestSnflwrFormatterStructured:

    def _make_formatter(self, **kwargs):
        from utils.logger import SnflwrFormatter
        return SnflwrFormatter(structured=True, **kwargs)

    def test_structured_output_is_valid_json(self):
        fmt = self._make_formatter()
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=10, msg="hello structured", args=None, exc_info=None,
        )
        record.correlation_id = "c-1"
        record.user_id = "u-1"
        record.session_id = "s-1"

        output = fmt.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello structured"
        assert data["trace"]["correlation_id"] == "c-1"
        assert "@timestamp" in data

    def test_structured_with_exception(self):
        fmt = self._make_formatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="snflwr.test", level=logging.ERROR, pathname="test.py",
            lineno=20, msg="error happened", args=None, exc_info=exc_info,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"

        output = fmt.format(record)
        data = json.loads(output)
        assert "error" in data
        assert data["error"]["type"] == "ValueError"
        assert "boom" in data["error"]["message"]

    def test_structured_extras_included(self):
        fmt = self._make_formatter(include_extras=True)
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=30, msg="with extras", args=None, exc_info=None,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"
        record.custom_field = "custom_value"

        output = fmt.format(record)
        data = json.loads(output)
        assert data["extra"]["custom_field"] == "custom_value"

    def test_structured_non_serializable_extra_becomes_str(self):
        fmt = self._make_formatter(include_extras=True)
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=30, msg="non-serializable", args=None, exc_info=None,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"
        record.weird_obj = object()  # not JSON serializable

        output = fmt.format(record)
        data = json.loads(output)
        assert "weird_obj" in data["extra"]

    def test_structured_trace_removed_when_all_dashes(self):
        fmt = self._make_formatter()
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=10, msg="no trace", args=None, exc_info=None,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"

        output = fmt.format(record)
        data = json.loads(output)
        assert "trace" not in data

    def test_format_adds_missing_context_attrs(self):
        """When record lacks correlation_id etc., format() should add defaults."""
        from utils.logger import SnflwrFormatter, correlation_id_var, user_id_var, session_id_var
        # Clear context vars
        correlation_id_var.set(None)
        user_id_var.set(None)
        session_id_var.set(None)

        fmt = SnflwrFormatter(structured=True)
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=10, msg="no attrs", args=None, exc_info=None,
        )
        # Explicitly ensure attrs are missing
        assert not hasattr(record, "correlation_id")

        output = fmt.format(record)
        data = json.loads(output)
        # Should not crash; context defaults to '-'
        assert data["message"] == "no attrs"


# ---------------------------------------------------------------------------
# SnflwrFormatter — standard with color
# ---------------------------------------------------------------------------

class TestSnflwrFormatterColor:

    def test_color_output_when_tty(self):
        from utils.logger import SnflwrFormatter
        fmt = SnflwrFormatter(use_color=True, structured=False)
        record = logging.LogRecord(
            name="snflwr.test", level=logging.ERROR, pathname="test.py",
            lineno=10, msg="red message", args=None, exc_info=None,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            output = fmt.format(record)

        # Should contain ANSI escape codes
        assert "\033[31m" in output  # Red for ERROR
        assert "\033[0m" in output   # Reset

    def test_no_color_when_not_tty(self):
        from utils.logger import SnflwrFormatter
        fmt = SnflwrFormatter(use_color=True, structured=False)
        record = logging.LogRecord(
            name="snflwr.test", level=logging.INFO, pathname="test.py",
            lineno=10, msg="plain message", args=None, exc_info=None,
        )
        record.correlation_id = "-"
        record.user_id = "-"
        record.session_id = "-"

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            output = fmt.format(record)

        assert "\033[" not in output


# ---------------------------------------------------------------------------
# PerformanceLogger
# ---------------------------------------------------------------------------

class TestPerformanceLogger:

    def test_log_and_get_statistics(self):
        from utils.logger import PerformanceLogger
        perf = PerformanceLogger()
        perf.log_metric("response_time", 100.0, "ms")
        perf.log_metric("response_time", 200.0, "ms")
        perf.log_metric("response_time", 150.0, "ms")

        stats = perf.get_statistics("response_time")
        assert stats is not None
        assert stats["count"] == 3
        assert stats["min"] == 100.0
        assert stats["max"] == 200.0
        assert stats["avg"] == 150.0

    def test_get_statistics_unknown_metric(self):
        from utils.logger import PerformanceLogger
        perf = PerformanceLogger()
        assert perf.get_statistics("nonexistent") is None

    def test_metric_capped_at_1000(self):
        from utils.logger import PerformanceLogger
        perf = PerformanceLogger()
        for i in range(1050):
            perf.log_metric("big", float(i))
        stats = perf.get_statistics("big")
        assert stats["count"] == 1000


# ---------------------------------------------------------------------------
# SafetyLogger
# ---------------------------------------------------------------------------

class TestSafetyLogger:

    def test_log_incident_writes_file(self, tmp_path):
        from utils.logger import SafetyLogger
        sl = SafetyLogger(tmp_path)
        sl.log_incident(
            incident_type="violence",
            child_profile_id="child-1",
            content="some content",
            severity="major",
            metadata={"source": "chat"},
        )
        log_file = tmp_path / "safety_incidents.log"
        assert log_file.exists()
        data = json.loads(log_file.read_text().strip())
        assert data["type"] == "violence"
        assert data["profile_id"] == "child-1"
        assert data["severity"] == "major"
        assert len(data["content"]) <= 500

    def test_log_incident_truncates_long_content(self, tmp_path):
        from utils.logger import SafetyLogger
        sl = SafetyLogger(tmp_path)
        sl.log_incident("test", "child-1", "x" * 1000, "minor")
        data = json.loads((tmp_path / "safety_incidents.log").read_text().strip())
        assert len(data["content"]) == 500

    def test_log_incident_survives_write_error(self, tmp_path):
        from utils.logger import SafetyLogger
        sl = SafetyLogger(tmp_path)
        # Point to a non-writable path
        sl.log_file = Path("/nonexistent/dir/safety.log")
        # Should not raise
        sl.log_incident("test", "child-1", "content", "minor")


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------

class TestMaskEmail:

    def test_normal_email(self):
        from utils.logger import mask_email
        assert mask_email("john@example.com") == "j***@example.com"

    def test_empty_string(self):
        from utils.logger import mask_email
        assert mask_email("") == "***"

    def test_none(self):
        from utils.logger import mask_email
        assert mask_email(None) == "***"

    def test_no_at_sign(self):
        from utils.logger import mask_email
        assert mask_email("notanemail") == "***"

    def test_empty_local_part(self):
        from utils.logger import mask_email
        assert mask_email("@example.com") == "***@example.com"


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:

    def test_get_logger_returns_logger(self):
        from utils.logger import get_logger
        log = get_logger("test_module")
        assert isinstance(log, logging.Logger)
        assert "snflwr.test_module" in log.name

    def test_log_performance_metric(self):
        from utils.logger import log_performance_metric, get_performance_statistics
        log_performance_metric("test_conv_metric", 42.0, "ms")
        stats = get_performance_statistics("test_conv_metric")
        assert stats is not None
        assert stats["count"] >= 1

    def test_log_safety_incident(self, tmp_path):
        from utils.logger import log_safety_incident
        # Just confirm it doesn't crash; the underlying SafetyLogger is tested above
        log_safety_incident("test_type", "prof-1", "content", "minor", {"k": "v"})

    def test_log_system_startup(self):
        from utils.logger import log_system_startup
        # Should not raise
        log_system_startup()


# ---------------------------------------------------------------------------
# LoggerManager
# ---------------------------------------------------------------------------

class TestLoggerManager:

    def test_set_level(self):
        from utils.logger import logger_manager
        logger_manager.set_level("DEBUG")
        app_logger = logging.getLogger("snflwr")
        assert app_logger.level == logging.DEBUG
        # Restore
        logger_manager.set_level("INFO")

    def test_cleanup_does_not_raise(self):
        """cleanup() calls logging.shutdown(); just verify no crash."""
        from utils.logger import logger_manager
        # We don't want to actually shut down logging for the test suite,
        # so we mock logging.shutdown.
        with patch("logging.shutdown") as mock_shutdown:
            logger_manager.cleanup()
            mock_shutdown.assert_called_once()
