"""Tests for per-request GPU placement and its thrash damper.

Measured facts these encode (2026-09-10, 23 GB card):
  tutor 4.47 GB, brain 18.4-21.0 GB -> cannot co-reside at any context setting
  swap cost ~5.2 s either way, warm ~0.49 s
  CPU turn ~14.4 s, GPU-with-swap ~7.5 s, GPU-resident ~2.2 s
"""
import importlib

import pytest

from core import gpu_placement


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never touch the real timestamp file, and start every test with no history."""
    monkeypatch.setattr(gpu_placement, "STATE_PATH", tmp_path / "last-swap")
    monkeypatch.setattr(gpu_placement, "SWAP_COOLDOWN_S", 60.0)


def _resident(monkeypatch, value):
    monkeypatch.setattr(gpu_placement, "_resident_on_gpu", lambda _tag: value)


class TestPlacementRules:
    def test_already_resident_uses_gpu_without_touching_the_cooldown(self, monkeypatch):
        """A resident model costs nothing to keep using — no swap to damp."""
        _resident(monkeypatch, True)
        gpu_placement._record_swap(1000.0)  # mid-cooldown
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=1001.0) == gpu_placement.ALL_LAYERS

    def test_cold_and_outside_cooldown_claims_the_gpu(self, monkeypatch):
        _resident(monkeypatch, False)
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=5000.0) == gpu_placement.ALL_LAYERS

    def test_cold_and_inside_cooldown_falls_back_to_cpu(self, monkeypatch):
        """The damper. Serving from CPU here is exactly today's behaviour."""
        _resident(monkeypatch, False)
        gpu_placement.choose_num_gpu("snflwr.ai", now=5000.0)      # claims, starts cooldown
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=5030.0) == gpu_placement.CPU_ONLY

    def test_cooldown_expires(self, monkeypatch):
        _resident(monkeypatch, False)
        gpu_placement.choose_num_gpu("snflwr.ai", now=5000.0)
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=5061.0) == gpu_placement.ALL_LAYERS

    def test_swap_rate_is_bounded_however_traffic_interleaves(self, monkeypatch):
        """THE guarantee: at most one tutor-induced swap per cooldown window.

        Without this bound, alternating traffic makes BOTH services pay ~5.2 s
        per turn — the tutor and IronClaw's brain evicting each other.
        """
        _resident(monkeypatch, False)
        claims = sum(
            1 for t in range(5000, 5300)  # 300 turns, one per second
            if gpu_placement.choose_num_gpu("snflwr.ai", now=float(t)) == gpu_placement.ALL_LAYERS
        )
        assert claims == 5, f"300s at a 60s cooldown should allow 5 swaps, got {claims}"


class TestFailOpen:
    def test_unreachable_ollama_serves_from_cpu(self, monkeypatch):
        """A tutor that answers slowly is degraded; one that raises is broken."""
        def boom(_tag):
            raise OSError("connection refused")
        monkeypatch.setattr(gpu_placement, "_resident_on_gpu", boom)
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=1.8e9) == gpu_placement.CPU_ONLY

    def test_unwritable_state_still_returns_a_placement(self, monkeypatch, tmp_path):
        """Losing the timestamp weakens the damper; it must not fail the turn.

        NOW must be a realistic epoch value: with no state file `_last_swap_at`
        returns 0.0, so a toy `now` like 1.0 sits INSIDE the cooldown window and
        the call correctly returns CPU. That is right behaviour and a wrong test.
        """
        _resident(monkeypatch, False)
        monkeypatch.setattr(gpu_placement, "STATE_PATH", tmp_path / "nope" / "x")
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=1.8e9) == gpu_placement.ALL_LAYERS

    def test_corrupt_state_is_treated_as_never_swapped(self, monkeypatch):
        _resident(monkeypatch, False)
        gpu_placement.STATE_PATH.write_text("not-a-timestamp")
        assert gpu_placement.choose_num_gpu("snflwr.ai", now=1.8e9) == gpu_placement.ALL_LAYERS


class TestApplyToOptions:
    def test_sets_num_gpu(self, monkeypatch):
        _resident(monkeypatch, True)
        assert gpu_placement.apply_to_options({}, "snflwr.ai")["num_gpu"] == gpu_placement.ALL_LAYERS

    def test_explicit_caller_value_always_wins(self, monkeypatch):
        """An operator or test pinning placement must not be overridden."""
        _resident(monkeypatch, True)
        assert gpu_placement.apply_to_options({"num_gpu": 0}, "snflwr.ai")["num_gpu"] == 0

    def test_other_options_are_preserved(self, monkeypatch):
        _resident(monkeypatch, True)
        out = gpu_placement.apply_to_options({"num_ctx": 8192, "temperature": 0.7}, "snflwr.ai")
        assert out["num_ctx"] == 8192 and out["temperature"] == 0.7


class TestTransportInjection:
    """Every outbound proxy path must inject placement.

    There are THREE: _forward_request, and two streaming helpers that build
    their own requests. The student turn goes through
    _stream_chunks_from_ollama — during development that one was the LAST to be
    wired, so it gets an explicit test rather than trusting the others.
    """

    def _t(self):
        return importlib.import_module("api.routes.ollama_proxy.transport")

    def test_injects_into_chat_bodies(self, monkeypatch):
        t = self._t()
        monkeypatch.setattr(gpu_placement, "_resident_on_gpu", lambda _tag: True)
        import json
        out = t._inject_gpu_placement("/api/chat", json.dumps(
            {"model": "snflwr.ai", "messages": []}).encode())
        assert json.loads(out)["options"]["num_gpu"] == gpu_placement.ALL_LAYERS

    def test_leaves_non_inference_paths_untouched(self):
        t = self._t()
        body = b'{"model":"snflwr.ai"}'
        assert t._inject_gpu_placement("/api/tags", body) is body

    def test_malformed_body_is_passed_through_unchanged(self):
        """Fail-open: never fail a turn over placement."""
        t = self._t()
        body = b"not json at all"
        assert t._inject_gpu_placement("/api/chat", body) is body

    def test_body_without_a_model_is_untouched(self):
        t = self._t()
        body = b'{"messages":[]}'
        assert t._inject_gpu_placement("/api/chat", body) is body
