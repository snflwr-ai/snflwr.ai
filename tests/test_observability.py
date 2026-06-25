"""Tests for utils/observability.py — metadata-only Langfuse wrapper.

These tests patch ``system_config`` attributes directly (the wrapper reads them
at call time) rather than ``setenv`` + ``importlib.reload(config)``. Reloading the
config module replaces the global ``system_config`` singleton and re-runs config
init — which pollutes the rest of the suite (unrelated tests that hold the
original ``system_config``). ``monkeypatch.setattr`` is isolated and auto-restored.
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from config import system_config
import utils.observability as obs


@pytest.fixture(autouse=True)
def _reset_obs_client():
    """Reset the wrapper's memoized client/init state between tests."""
    obs._client = None
    obs._init_failed = False
    yield
    obs._client = None
    obs._init_failed = False


def test_trace_signature_has_no_content_param():
    """Structural guarantee: the trace API cannot carry chat text."""
    params = set(inspect.signature(obs.trace_chat_turn).parameters)
    forbidden = {"text", "prompt", "response", "content", "message", "input", "output"}
    assert not (
        params & forbidden
    ), f"content-bearing params leaked: {params & forbidden}"


def test_age_band_buckets():
    assert obs.age_band(9) == "<13"
    assert obs.age_band(13) == "13-17"
    assert obs.age_band(17) == "13-17"
    assert obs.age_band(18) == "18+"
    assert obs.age_band(None) == "unknown"
    assert obs.age_band("oops") == "unknown"


def test_hash_profile_is_stable_and_not_raw(monkeypatch):
    monkeypatch.setattr(system_config, "LANGFUSE_HASH_SALT", "s" * 64)
    h1 = obs.hash_profile("prof_123")
    h2 = obs.hash_profile("prof_123")
    assert h1 == h2 and h1 != "prof_123" and len(h1) >= 16


def test_disabled_is_noop_no_sdk(monkeypatch):
    """With LANGFUSE_ENABLED false, trace_chat_turn does nothing and never builds a client."""
    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", False)
    with patch.object(obs, "_get_client") as gc:
        obs.trace_chat_turn(
            model="m",
            age_band="<13",
            profile_hash="h",
            blocked=False,
            safety={},
            latency_ms={"total": 1.0},
        )
        gc.assert_not_called()


def test_enabled_emits_metadata_only(monkeypatch):
    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(system_config, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(system_config, "LANGFUSE_SECRET_KEY", "sk")

    fake_trace = MagicMock()
    fake_client = MagicMock()
    fake_client.trace.return_value = fake_trace
    with patch.object(obs, "_get_client", return_value=fake_client):
        obs.trace_chat_turn(
            model="snflwr.ai",
            age_band="<13",
            profile_hash="h1",
            blocked=True,
            safety={"category": "self_harm", "severity": "major"},
            latency_ms={"input_check": 5.0, "total": 12.0},
            tokens={"input": 10, "output": 20},
        )

    fake_client.trace.assert_called_once()
    kwargs = fake_client.trace.call_args.kwargs
    # No content keys anywhere in the trace payload.
    blob = repr(kwargs).lower()
    for bad in ("input=", "output=", "prompt", "content", "messages"):
        assert bad not in blob, f"possible content leak via {bad}: {kwargs}"
    assert kwargs.get("user_id") == "h1"  # grouped by hashed profile
    fake_trace.generation.assert_called_once()
    # The generation payload must ALSO be content-free (it carries model + usage
    # counts + latency/safety metadata only — never prompt/response text).
    gen_blob = repr(fake_trace.generation.call_args.kwargs).lower()
    for bad in ("prompt", "content", "messages", "'text'", "response"):
        assert bad not in gen_blob, f"possible content leak in generation: {bad}"


def test_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(system_config, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(system_config, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(system_config, "LANGFUSE_SECRET_KEY", "sk")
    with patch.object(obs, "_get_client", side_effect=RuntimeError("boom")):
        # Must not raise.
        obs.trace_chat_turn(
            model="m",
            age_band="unknown",
            profile_hash="h",
            blocked=False,
            safety={},
            latency_ms={"total": 1.0},
        )
