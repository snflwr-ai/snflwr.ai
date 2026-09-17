"""Tests for the hardware-driven serving plan.

Measured facts these encode:
  - RTX 3090 Ti (23 GB), Ollama as deployed, snflwr.ai-31b at num_ctx 16384:
    21.4 GB resident, requests SERIALIZE, ~5.7 messages/min, usable ceiling
    3 concurrent students (load test 2026-09-17).
  - Tutoring quality: only the 31b backbone passed the sealed bars (2026-09-17).
    e4b and 12b measured 4-13 wrong replies per 121 against a bar of 6, or
    acceptable correctness only by stonewalling 38 times. A box that cannot run
    a CERTIFIED backbone must not tutor with a smaller one.
  - vLLM serves different weight formats than Ollama's Q4_K_M GGUF, so the
    sealed result does NOT transfer to it until the parity re-run passes.
"""

import pytest

from core import serving_plan


def _plan(monkeypatch, *, vram=0.0, memory=32.0, engine_env=None, vllm=False,
          linux=True, nvidia=True, allow_unverified=None):
    monkeypatch.setattr(serving_plan, "_detect_vram_gb", lambda: vram)
    monkeypatch.setattr(serving_plan, "_detect_memory_gb", lambda: memory)
    monkeypatch.setattr(serving_plan, "_vllm_reachable", lambda: vllm)
    monkeypatch.setattr(serving_plan, "_is_linux", lambda: linux)
    monkeypatch.setattr(serving_plan, "_has_nvidia_gpu", lambda: nvidia)
    for name, value in (("INFERENCE_ENGINE", engine_env),
                        ("SNFLWR_ALLOW_UNVERIFIED_ENGINE", allow_unverified)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return serving_plan.compute_plan()


class TestEngineSelection:
    def test_gpu_linux_with_vllm_running_selects_vllm(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert plan.engine == "vllm"

    def test_vllm_not_reachable_falls_back_to_ollama(self, monkeypatch):
        plan = _plan(monkeypatch, vram=24.0, vllm=False)
        assert plan.engine == "ollama"
        assert "vllm" in plan.reason.lower()

    def test_non_linux_never_selects_vllm(self, monkeypatch):
        plan = _plan(monkeypatch, vram=48.0, vllm=True, linux=False)
        assert plan.engine == "ollama"

    def test_no_nvidia_gpu_never_selects_vllm(self, monkeypatch):
        plan = _plan(monkeypatch, vram=48.0, vllm=True, nvidia=False)
        assert plan.engine == "ollama"

    def test_env_override_forces_ollama(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True, engine_env="ollama")
        assert plan.engine == "ollama"

    def test_env_override_forces_vllm(self, monkeypatch):
        plan = _plan(monkeypatch, vram=24.0, vllm=False, engine_env="vllm",
                     allow_unverified="1")
        assert plan.engine == "vllm"


class TestVLLMFitsBeforeItIsChosen:
    """Measured 2026-09-17: every 4-bit build of the 31b backbone is ~19-21 GB, so
    vLLM OOMs at load on a 24 GB card even when the server is reachable."""

    def test_a_24gb_card_is_not_offered_vllm(self, monkeypatch):
        plan = _plan(monkeypatch, vram=24.0, vllm=True, allow_unverified="1")
        assert plan.engine == "ollama"
        assert "vram" in plan.reason.lower() or "gb" in plan.reason.lower()

    def test_a_48gb_card_is(self, monkeypatch):
        plan = _plan(monkeypatch, vram=48.0, vllm=True, allow_unverified="1")
        assert plan.engine == "vllm"

    def test_an_explicit_override_still_wins(self, monkeypatch):
        """An operator benchmarking on small hardware is allowed to try."""
        plan = _plan(monkeypatch, vram=24.0, vllm=True, engine_env="vllm",
                     allow_unverified="1")
        assert plan.engine == "vllm"


class TestQualityFloor:
    """Only a CERTIFIED backbone may tutor. Smaller ones are not a fallback."""

    def test_24gb_card_on_ollama_is_certified_31b(self, monkeypatch):
        plan = _plan(monkeypatch, vram=24.0)
        assert plan.engine == "ollama"
        assert plan.tutor_model == "snflwr.ai-31b"
        assert plan.quality_tier == "certified"
        assert plan.tutoring_enabled is True

    def test_small_card_cannot_tutor_even_though_e4b_would_fit(self, monkeypatch):
        plan = _plan(monkeypatch, vram=8.0)
        assert plan.quality_tier == "unsupported"
        assert plan.tutoring_enabled is False
        assert plan.tutor_model is None

    def test_cpu_only_box_cannot_tutor(self, monkeypatch):
        plan = _plan(monkeypatch, vram=0.0, memory=64.0)
        assert plan.quality_tier == "unsupported"
        assert plan.tutoring_enabled is False

    def test_vllm_is_unverified_until_the_parity_rerun_passes(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert plan.engine == "vllm"
        assert plan.quality_tier == "unverified"
        assert plan.tutoring_enabled is True  # explicitly allowed for benchmarking

    def test_unverified_engine_refuses_to_tutor_without_the_opt_in(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True)
        assert plan.engine == "vllm"
        assert plan.tutoring_enabled is False
        assert "unverified" in plan.reason.lower()


class TestContextAndConcurrency:
    def test_31b_keeps_the_sealed_context_window(self, monkeypatch):
        plan = _plan(monkeypatch, vram=24.0)
        assert plan.num_ctx == 16384

    def test_ollama_admits_one_request_at_a_time(self, monkeypatch):
        """Measured: throughput is flat from 1 to 8 concurrent requests."""
        plan = _plan(monkeypatch, vram=24.0)
        assert plan.max_concurrent_requests == 1

    def test_vllm_scales_slots_with_free_vram(self, monkeypatch):
        small = _plan(monkeypatch, vram=48.0, vllm=True, allow_unverified="1")
        large = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert large.max_concurrent_requests > small.max_concurrent_requests >= 1

    def test_vllm_engine_args_match_the_plan(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert plan.engine_args["--max-model-len"] == plan.num_ctx
        assert int(plan.engine_args["--max-num-seqs"]) == plan.max_concurrent_requests
        assert 0.0 < float(plan.engine_args["--gpu-memory-utilization"]) <= 0.95

    def test_speculative_decoding_is_off_until_measured(self, monkeypatch):
        plan = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert plan.speculative_draft_model is None


class TestOverrides:
    def test_operator_can_cap_concurrency(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_MAX_CONCURRENT", "3")
        plan = _plan(monkeypatch, vram=80.0, vllm=True, allow_unverified="1")
        assert plan.max_concurrent_requests == 3

    def test_a_bad_override_is_ignored_not_fatal(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_MAX_CONCURRENT", "not-a-number")
        plan = _plan(monkeypatch, vram=24.0)
        assert plan.max_concurrent_requests == 1


class TestPlanIsReportable:
    def test_summary_carries_the_facts_an_operator_needs(self, monkeypatch):
        plan = _plan(monkeypatch, vram=8.0)
        summary = plan.as_dict()
        for key in ("engine", "tutor_model", "quality_tier", "tutoring_enabled",
                    "num_ctx", "max_concurrent_requests", "reason"):
            assert key in summary

    def test_detection_failure_degrades_to_ollama_not_an_exception(self, monkeypatch):
        def boom():
            raise RuntimeError("nvidia-smi exploded")

        monkeypatch.setattr(serving_plan, "_detect_vram_gb", boom)
        monkeypatch.setattr(serving_plan, "_detect_memory_gb", lambda: 32.0)
        monkeypatch.setattr(serving_plan, "_vllm_reachable", lambda: False)
        monkeypatch.delenv("INFERENCE_ENGINE", raising=False)
        plan = serving_plan.compute_plan()
        assert plan.engine == "ollama"
        assert plan.tutoring_enabled is False


class TestCertifiedTable:
    def test_every_certified_entry_records_its_sealed_run(self):
        for entry in serving_plan.CERTIFIED_BACKBONES:
            assert entry.sealed_on, f"{entry.model} has no sealed-run date"
            assert entry.engine in ("ollama", "vllm")

    def test_smaller_backbones_are_not_certified(self):
        certified = {e.model for e in serving_plan.CERTIFIED_BACKBONES}
        assert "snflwr.ai" not in certified  # e4b
        assert "snflwr.ai-12b" not in certified
