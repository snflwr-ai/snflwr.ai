"""A missing tracing dependency must be distinguishable from a runtime failure.

`langfuse` was absent from requirements.lock for an unknown period, so the
Docker image never contained it. `_get_client()` caught the ImportError in the
same broad handler as bad keys and unreachable hosts, logged one generic
warning, and returned None. Tracing was off and looked exactly like tracing that
had nothing to report.

The distinction matters operationally: bad keys and an unreachable host are
transient runtime conditions that may fix themselves. A missing import means the
IMAGE IS BUILT WRONG — it can never recover, and it needs a human. Same
severity for both is what let this sit unnoticed.

Broad catching is kept deliberately: tracing must never take down a child's
tutoring session. What changes is that the build defect is loud and separable,
and that whether tracing is actually live becomes queryable.
"""

import logging

import pytest

from utils import observability as obs


class _Capture(logging.Handler):
    """Capture records straight off the module logger.

    utils/logger.py sets propagate=False, so pytest's caplog — which attaches to
    the ROOT logger — never sees these records. Asserting on caplog here would
    produce a test that fails while the code is correct.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def logs():
    handler = _Capture()
    obs.logger.addHandler(handler)
    previous = obs.logger.level
    obs.logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        obs.logger.removeHandler(handler)
        obs.logger.setLevel(previous)


@pytest.fixture(autouse=True)
def _reset_client_state():
    """_get_client memoizes into module globals; reset around each test."""
    obs._client = None
    obs._init_failed = False
    obs._init_error = None
    yield
    obs._client = None
    obs._init_failed = False
    obs._init_error = None


@pytest.fixture
def _enabled(monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", True, raising=False)
    monkeypatch.setattr(system_config, "LANGFUSE_PUBLIC_KEY", "pk-test", raising=False)
    monkeypatch.setattr(system_config, "LANGFUSE_SECRET_KEY", "sk-test", raising=False)
    monkeypatch.setattr(
        system_config, "LANGFUSE_HOST", "http://localhost:3000", raising=False
    )


def test_missing_dependency_logs_at_error(monkeypatch, logs, _enabled):
    """The build-defect case is ERROR, not WARNING — it cannot self-resolve."""
    import builtins

    real_import = builtins.__import__

    def _no_langfuse(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("No module named 'langfuse'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_langfuse)

    assert obs._get_client() is None

    errors = [r for r in logs if r.levelno >= logging.ERROR]
    assert errors, "a missing tracing dependency must log at ERROR"
    assert "not installed" in errors[0].getMessage().lower()


def test_missing_dependency_message_is_actionable(monkeypatch, logs, _enabled):
    """The log line must say what to do, not just what broke."""
    import builtins

    real_import = builtins.__import__

    def _no_langfuse(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("No module named 'langfuse'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_langfuse)
    obs._get_client()

    joined = " ".join(r.getMessage() for r in logs).lower()
    assert "requirements.lock" in joined, "should point at the lockfile"


def test_runtime_failure_stays_a_warning(monkeypatch, logs, _enabled):
    """Bad keys / unreachable host are transient — they must NOT be escalated to
    ERROR, or the loud signal stops meaning 'the image is wrong'."""
    import builtins

    real_import = builtins.__import__

    class _Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("connection refused")

    def _fake_import(name, *args, **kwargs):
        if name == "langfuse":
            module = type(sys)("langfuse")
            module.Langfuse = _Boom
            return module
        return real_import(name, *args, **kwargs)

    import sys

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert obs._get_client() is None

    assert not [r for r in logs if r.levelno >= logging.ERROR]
    assert [r for r in logs if r.levelno == logging.WARNING]


def test_failure_never_raises(monkeypatch, _enabled):
    """Tracing must never break a child's tutoring turn."""
    import builtins

    real_import = builtins.__import__

    def _explode(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _explode)
    assert obs._get_client() is None  # no exception escapes


# ---------------------------------------------------------------------------
# tracing_status() — makes "is tracing actually live?" answerable
# ---------------------------------------------------------------------------


def test_status_reports_disabled_when_not_configured(monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", False, raising=False)
    assert obs.tracing_status()["state"] == "disabled"


def test_status_reports_misconfigured_when_enabled_without_keys(monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", True, raising=False)
    monkeypatch.setattr(system_config, "LANGFUSE_PUBLIC_KEY", "", raising=False)
    monkeypatch.setattr(system_config, "LANGFUSE_SECRET_KEY", "", raising=False)
    assert obs.tracing_status()["state"] == "misconfigured"


def test_status_reports_dependency_missing(monkeypatch, _enabled):
    """The state that was previously indistinguishable from 'nothing to report'."""
    import builtins

    real_import = builtins.__import__

    def _no_langfuse(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("No module named 'langfuse'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_langfuse)
    obs._get_client()
    assert obs.tracing_status()["state"] == "dependency_missing"


def test_status_never_leaks_credentials(monkeypatch, _enabled):
    """This is intended for /health; it must not echo keys."""
    status = obs.tracing_status()
    flat = str(status).lower()
    assert "pk-test" not in flat and "sk-test" not in flat
