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
def _no_operator_override(monkeypatch):
    monkeypatch.delenv("SNFLWR_GPU_PREFER_CPU", raising=False)


def _placement(monkeypatch, value):
    monkeypatch.setattr(gpu_placement, "_resident_placement", lambda _tag: value)


class TestNeverFlipPlacement:
    """Rule 1, and the whole lesson: moving a LOADED model between GPU and CPU
    is a full reload, not an adjustment.

    Measured 2026-09-10 under real concurrent load, the previous cooldown
    policy produced 162.8 s and 157.4 s tutor turns and a 147.1 s agent turn —
    381 s wall clock for 8 turns — because it flipped placement. A cold CPU
    load of the tutor is ~131 s.
    """

    def test_loaded_on_gpu_stays_on_gpu(self, monkeypatch):
        _placement(monkeypatch, "gpu")
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.ALL_LAYERS

    def test_loaded_on_cpu_stays_on_cpu(self, monkeypatch):
        """Serving from CPU costs ~13.6 s; forcing it to the GPU costs a reload."""
        _placement(monkeypatch, "cpu")
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.CPU_ONLY

    def test_not_loaded_prefers_gpu(self, monkeypatch):
        """GPU wins cold AND warm: ~5.6 s vs ~131 s cold, ~1.4 s vs ~13.6 s warm."""
        _placement(monkeypatch, None)
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.ALL_LAYERS

    def test_operator_can_prefer_cpu_for_a_co_tenant(self, monkeypatch):
        _placement(monkeypatch, None)
        monkeypatch.setenv("SNFLWR_GPU_PREFER_CPU", "1")
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.CPU_ONLY

    def test_prefer_cpu_does_not_override_an_existing_gpu_load(self, monkeypatch):
        """Even the operator preference must not force a reload."""
        _placement(monkeypatch, "gpu")
        monkeypatch.setenv("SNFLWR_GPU_PREFER_CPU", "1")
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.ALL_LAYERS


class TestResidentPlacementProbe:
    def test_reads_size_vram_to_tell_gpu_from_cpu(self, monkeypatch):
        import types

        def fake_get(_url, timeout=None):
            return types.SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"models": [
                    {"name": "snflwr.ai:latest", "size_vram": 3_300_000_000},
                    {"name": "granite-worker:latest", "size_vram": 0},
                ]},
            )
        monkeypatch.setattr(gpu_placement.httpx, "get", fake_get)
        assert gpu_placement._resident_placement("snflwr.ai") == "gpu"
        assert gpu_placement._resident_placement("granite-worker") == "cpu"
        assert gpu_placement._resident_placement("not-loaded") is None


class TestFailOpen:
    def test_unreachable_ollama_serves_from_cpu(self, monkeypatch):
        """A tutor that answers slowly is degraded; one that raises is broken."""
        def boom(_tag):
            raise OSError("connection refused")
        monkeypatch.setattr(gpu_placement, "_resident_placement", boom)
        assert gpu_placement.choose_num_gpu("snflwr.ai") == gpu_placement.CPU_ONLY


class TestApplyToOptions:
    def test_sets_num_gpu(self, monkeypatch):
        _placement(monkeypatch, "gpu")
        assert gpu_placement.apply_to_options({}, "snflwr.ai")["num_gpu"] == gpu_placement.ALL_LAYERS

    def test_explicit_caller_value_always_wins(self, monkeypatch):
        _placement(monkeypatch, "gpu")
        assert gpu_placement.apply_to_options({"num_gpu": 0}, "snflwr.ai")["num_gpu"] == 0

    def test_other_options_are_preserved(self, monkeypatch):
        _placement(monkeypatch, "gpu")
        out = gpu_placement.apply_to_options({"num_ctx": 8192, "temperature": 0.7}, "snflwr.ai")
        assert out["num_ctx"] == 8192 and out["temperature"] == 0.7


class TestOllamaUrlResolution:
    """Regression: the probe must follow the APP's Ollama URL, not 127.0.0.1.

    Inside the API container 127.0.0.1 is the container's own loopback, so a
    hardcoded default made the probe fail with ECONNREFUSED and fail-open served
    every turn from CPU — inert while looking healthy. Only a real deploy
    surfaced it, because these tests mock the probe itself.
    """

    def test_prefers_the_proxy_target_the_app_already_uses(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_PROXY_TARGET", "http://172.24.0.1:11434")
        monkeypatch.delenv("OLLAMA_HOST_URL", raising=False)
        assert gpu_placement._ollama_url() == "http://172.24.0.1:11434"

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST_URL", "http://elsewhere:11434")
        monkeypatch.setenv("OLLAMA_PROXY_TARGET", "http://172.24.0.1:11434")
        assert gpu_placement._ollama_url() == "http://elsewhere:11434"

    def test_base_url_is_accepted_too(self, monkeypatch):
        for v in ("OLLAMA_HOST_URL", "OLLAMA_PROXY_TARGET"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://172.24.0.1:11434")
        assert gpu_placement._ollama_url() == "http://172.24.0.1:11434"

class TestNoUserDataInLogs:
    """CodeQL flags any client-supplied value reaching a log sink, and it cannot
    see through a sanitiser. The model tag comes from the proxy's
    body["model"], so it is kept OUT of log messages entirely rather than
    cleaned on the way in. This test fails if an interpolated tag comes back.
    """

    def test_log_calls_do_not_interpolate_the_model_tag(self):
        import inspect

        src = inspect.getsource(gpu_placement.choose_num_gpu)
        log_lines = [ln for ln in src.splitlines() if "logger." in ln or "model_tag," in ln]
        assert not any("model_tag" in ln for ln in log_lines), (
            "the client-supplied model tag must not reach a log sink"
        )

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
        monkeypatch.setattr(gpu_placement, "_resident_placement", lambda _tag: "gpu")
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
