"""Tests for utils/observability.py — metadata-only Langfuse wrapper."""
import inspect
from unittest.mock import MagicMock, patch


def test_trace_signature_has_no_content_param():
    """Structural guarantee: the trace API cannot carry chat text."""
    import utils.observability as obs
    params = set(inspect.signature(obs.trace_chat_turn).parameters)
    forbidden = {"text", "prompt", "response", "content", "message", "input", "output"}
    assert not (params & forbidden), f"content-bearing params leaked: {params & forbidden}"


def test_age_band_buckets():
    import utils.observability as obs
    assert obs.age_band(9) == "<13"
    assert obs.age_band(13) == "13-17"
    assert obs.age_band(17) == "13-17"
    assert obs.age_band(18) == "18+"
    assert obs.age_band(None) == "unknown"
    assert obs.age_band("oops") == "unknown"


def test_hash_profile_is_stable_and_not_raw(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HASH_SALT", "s" * 64)
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    h1 = obs.hash_profile("prof_123")
    h2 = obs.hash_profile("prof_123")
    assert h1 == h2 and h1 != "prof_123" and len(h1) >= 16


def test_disabled_is_noop_no_sdk(monkeypatch):
    """With LANGFUSE_ENABLED false, trace_chat_turn does nothing and never builds a client."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    with patch.object(obs, "_get_client") as gc:
        obs.trace_chat_turn(model="m", age_band="<13", profile_hash="h",
                            blocked=False, safety={}, latency_ms={"total": 1.0})
        gc.assert_not_called()


def test_enabled_emits_metadata_only(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)

    fake_trace = MagicMock()
    fake_client = MagicMock()
    fake_client.trace.return_value = fake_trace
    with patch.object(obs, "_get_client", return_value=fake_client):
        obs.trace_chat_turn(model="snflwr.ai", age_band="<13", profile_hash="h1",
                            blocked=True, safety={"category": "self_harm", "severity": "major"},
                            latency_ms={"input_check": 5.0, "total": 12.0},
                            tokens={"input": 10, "output": 20})

    fake_client.trace.assert_called_once()
    kwargs = fake_client.trace.call_args.kwargs
    # No content keys anywhere in the trace payload.
    blob = repr(kwargs).lower()
    for bad in ("input=", "output=", "prompt", "content", "messages"):
        assert bad not in blob, f"possible content leak via {bad}: {kwargs}"
    assert kwargs.get("user_id") == "h1"          # grouped by hashed profile
    fake_trace.generation.assert_called_once()


def test_exception_is_swallowed(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    with patch.object(obs, "_get_client", side_effect=RuntimeError("boom")):
        # Must not raise.
        obs.trace_chat_turn(model="m", age_band="unknown", profile_hash="h",
                            blocked=False, safety={}, latency_ms={"total": 1.0})
