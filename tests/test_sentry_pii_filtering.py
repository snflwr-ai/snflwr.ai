"""
Tests for Sentry PII filtering (COPPA compliance).

Verifies that before_send_filter and before_breadcrumb_filter correctly
scrub all personally identifiable information before events reach Sentry.
Student data must never leave the school's infrastructure.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from copy import deepcopy

# sentry_sdk is optional — skip if not installed
pytest.importorskip("sentry_sdk")


# ---------------------------------------------------------------------------
# Fixtures — sample Sentry events
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_event():
    """A Sentry event containing PII that should be scrubbed."""
    return {
        "request": {
            "headers": {
                "Authorization": "Bearer eyJhbG...",
                "Cookie": "session=abc123; csrf_token=xyz",
                "X-CSRF-Token": "xyz789",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            "query_string": "email=student@school.edu&token=secret123",
            "url": "https://snflwr.school.edu/api/chat/send",
            "method": "POST",
        },
        "user": {
            "id": "parent-123",
            "email": "parent@example.com",
            "username": "parent_user",
            "name": "Jane Doe",
            "role": "parent",
        },
        "extra": {
            "email_address": "secret@test.com",
            "user_password": "hunter2",
            "auth_token": "tok_123",
            "api_key_internal": "key_abc",
            "secret_value": "s3cr3t",
            "request_path": "/api/chat/send",
            "response_time_ms": 150,
        },
    }


@pytest.fixture
def sample_hint():
    return {}


# ---------------------------------------------------------------------------
# before_send_filter
# ---------------------------------------------------------------------------


class TestBeforeSendFilter:

    def _get_filter(self):
        from utils.sentry_config import before_send_filter
        return before_send_filter

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_authorization_header(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["request"]["headers"]["Authorization"] == "[Filtered]"

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_cookie_header(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["request"]["headers"]["Cookie"] == "[Filtered]"

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_csrf_header(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["request"]["headers"]["X-CSRF-Token"] == "[Filtered]"

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_preserves_non_sensitive_headers(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["request"]["headers"]["Content-Type"] == "application/json"

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_query_string(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["request"]["query_string"] == "[Filtered]"
        assert "email" not in result["request"]["query_string"]

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_user_pii_keeps_id_and_role(self, sample_event, sample_hint):
        """COPPA: Only user_id and role should survive; email/name/username removed."""
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["user"]["id"] == "parent-123"
        assert result["user"]["role"] == "parent"
        assert "email" not in result["user"]
        assert "username" not in result["user"]
        assert "name" not in result["user"]

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_scrubs_sensitive_extra_keys(self, sample_event, sample_hint):
        """Extra context keys matching sensitive patterns must be filtered."""
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["extra"]["email_address"] == "[Filtered]"
        assert result["extra"]["user_password"] == "[Filtered]"
        assert result["extra"]["auth_token"] == "[Filtered]"
        assert result["extra"]["api_key_internal"] == "[Filtered]"
        assert result["extra"]["secret_value"] == "[Filtered]"

    @patch.dict(os.environ, {"ENVIRONMENT": "production", "SENTRY_SEND_IN_DEV": "false"})
    def test_preserves_non_sensitive_extra_keys(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result["extra"]["request_path"] == "/api/chat/send"
        assert result["extra"]["response_time_ms"] == 150

    @patch.dict(os.environ, {"ENVIRONMENT": "development", "SENTRY_SEND_IN_DEV": "false"})
    def test_drops_events_in_dev_by_default(self, sample_event, sample_hint):
        """Non-production events should be dropped unless SENTRY_SEND_IN_DEV=true."""
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result is None

    @patch.dict(os.environ, {"ENVIRONMENT": "development", "SENTRY_SEND_IN_DEV": "true"})
    def test_sends_events_in_dev_when_enabled(self, sample_event, sample_hint):
        f = self._get_filter()
        result = f(deepcopy(sample_event), sample_hint)
        assert result is not None

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_handles_event_without_request(self, sample_hint):
        """Events without request data should not crash."""
        f = self._get_filter()
        event = {"user": {"id": "u1", "email": "test@test.com"}}
        result = f(event, sample_hint)
        assert result is not None
        assert "email" not in result["user"]

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_handles_event_without_user(self, sample_hint):
        """Events without user data should not crash."""
        f = self._get_filter()
        event = {"extra": {"request_path": "/api/health"}}
        result = f(event, sample_hint)
        assert result is not None

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_handles_event_without_extra(self, sample_hint):
        """Events without extra context should not crash."""
        f = self._get_filter()
        event = {"user": {"id": "u1"}}
        result = f(event, sample_hint)
        assert result is not None


# ---------------------------------------------------------------------------
# before_breadcrumb_filter
# ---------------------------------------------------------------------------


class TestBeforeBreadcrumbFilter:

    def _get_filter(self):
        from utils.sentry_config import before_breadcrumb_filter
        return before_breadcrumb_filter

    def test_filters_select_queries(self):
        """SQL SELECT queries should be redacted to prevent PII leaks."""
        f = self._get_filter()
        crumb = {
            "category": "query",
            "data": {"query": "SELECT email, name FROM accounts WHERE parent_id = 'p1'"},
        }
        result = f(crumb, {})
        assert result["data"]["query"] == "SELECT [filtered]"

    def test_filters_insert_queries(self):
        f = self._get_filter()
        crumb = {
            "category": "query",
            "data": {"query": "INSERT INTO accounts (email, password_hash) VALUES ('e', 'h')"},
        }
        result = f(crumb, {})
        assert result["data"]["query"] == "INSERT [filtered]"

    def test_filters_update_queries(self):
        f = self._get_filter()
        crumb = {
            "category": "query",
            "data": {"query": "UPDATE accounts SET email = 'new@test.com' WHERE parent_id = 'p1'"},
        }
        result = f(crumb, {})
        assert result["data"]["query"] == "UPDATE [filtered]"

    def test_strips_query_params_from_http_urls(self):
        """HTTP breadcrumbs should strip query parameters (may contain tokens)."""
        f = self._get_filter()
        crumb = {
            "category": "httplib",
            "data": {"url": "https://api.example.com/endpoint?token=secret&user=admin"},
        }
        result = f(crumb, {})
        assert result["data"]["url"] == "https://api.example.com/endpoint"
        assert "token" not in result["data"]["url"]

    def test_preserves_non_query_breadcrumbs(self):
        """Non-query, non-HTTP breadcrumbs should pass through unchanged."""
        f = self._get_filter()
        crumb = {
            "category": "default",
            "message": "Something happened",
            "data": {"key": "value"},
        }
        result = f(crumb, {})
        assert result == crumb

    def test_handles_missing_data_key(self):
        """Breadcrumbs without data should not crash."""
        f = self._get_filter()
        crumb = {"category": "query"}
        result = f(crumb, {})
        assert result is not None


# ---------------------------------------------------------------------------
# init_sentry
# ---------------------------------------------------------------------------


class TestInitSentry:

    @patch.dict(os.environ, {"SENTRY_ENABLED": "false"})
    @patch("utils.sentry_config.sentry_sdk")
    def test_disabled_does_not_init(self, mock_sdk):
        from utils.sentry_config import init_sentry
        init_sentry()
        mock_sdk.init.assert_not_called()

    @patch.dict(os.environ, {"SENTRY_ENABLED": "true", "SENTRY_DSN": ""})
    @patch("utils.sentry_config.sentry_sdk")
    def test_no_dsn_does_not_init(self, mock_sdk):
        from utils.sentry_config import init_sentry
        init_sentry()
        mock_sdk.init.assert_not_called()

    @patch.dict(os.environ, {
        "SENTRY_ENABLED": "true",
        "SENTRY_DSN": "https://key@sentry.io/123",
        "SENTRY_ENVIRONMENT": "production",
    })
    @patch("utils.sentry_config.sentry_sdk")
    def test_enabled_with_dsn_inits(self, mock_sdk):
        from utils.sentry_config import init_sentry
        init_sentry()
        mock_sdk.init.assert_called_once()
        call_kwargs = mock_sdk.init.call_args[1]
        assert call_kwargs["dsn"] == "https://key@sentry.io/123"
        assert call_kwargs["send_default_pii"] is False  # COPPA compliance
        assert call_kwargs["before_send"] is not None
        assert call_kwargs["before_breadcrumb"] is not None


# ---------------------------------------------------------------------------
# set_user_context — COPPA compliant
# ---------------------------------------------------------------------------


class TestSetUserContext:

    @patch("utils.sentry_config.sentry_sdk")
    def test_only_sends_id_and_role(self, mock_sdk):
        from utils.sentry_config import set_user_context
        set_user_context("user-123", role="parent")
        mock_sdk.set_user.assert_called_once_with({"id": "user-123", "role": "parent"})

    @patch("utils.sentry_config.sentry_sdk")
    def test_id_only_without_role(self, mock_sdk):
        from utils.sentry_config import set_user_context
        set_user_context("user-456")
        mock_sdk.set_user.assert_called_once_with({"id": "user-456"})
