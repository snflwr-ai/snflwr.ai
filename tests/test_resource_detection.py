"""
Tests for resource_detection.py

Verifies hardware detection, recommendation formulas, env-var overrides,
min/max connection clamping, and negative-value rejection.
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from resource_detection import (
    detect_cpu_count,
    detect_memory_bytes,
    detect_memory_gb,
    detect_disk_bytes,
    recommend_api_workers,
    recommend_postgres_max_connections,
    recommend_postgres_min_connections,
    recommend_redis_max_connections,
    recommend_celery_concurrency,
    recommend_celery_prefetch,
    recommend_num_predict,
    recommend_num_ctx,
    detect_resources,
    ResourceProfile,
)


# ---------------------------------------------------------------------------
# Hardware detection functions
# ---------------------------------------------------------------------------

class TestDetectCpuCount:
    """Tests for detect_cpu_count()."""

    @patch("resource_detection._HAS_PSUTIL", True)
    @patch("resource_detection.psutil")
    def test_uses_psutil_when_available(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 8
        assert detect_cpu_count() == 8

    @patch("resource_detection._HAS_PSUTIL", True)
    @patch("resource_detection.psutil")
    def test_clamps_to_minimum_1(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 0
        # 0 cores is invalid, should fall through to os.cpu_count
        with patch("os.cpu_count", return_value=1):
            assert detect_cpu_count() >= 1

    @patch("resource_detection._HAS_PSUTIL", False)
    def test_falls_back_to_os_cpu_count(self):
        with patch("os.cpu_count", return_value=4):
            assert detect_cpu_count() == 4

    @patch("resource_detection._HAS_PSUTIL", False)
    def test_returns_1_when_os_cpu_count_is_none(self):
        with patch("os.cpu_count", return_value=None):
            assert detect_cpu_count() == 1


class TestDetectMemory:
    """Tests for detect_memory_bytes() and detect_memory_gb()."""

    @patch("resource_detection._HAS_PSUTIL", True)
    @patch("resource_detection.psutil")
    def test_detect_memory_bytes(self, mock_psutil):
        mock_vm = MagicMock()
        mock_vm.total = 8 * 1024 ** 3  # 8 GiB
        mock_psutil.virtual_memory.return_value = mock_vm
        assert detect_memory_bytes() == 8 * 1024 ** 3

    @patch("resource_detection._HAS_PSUTIL", True)
    @patch("resource_detection.psutil")
    def test_detect_memory_gb(self, mock_psutil):
        mock_vm = MagicMock()
        mock_vm.total = 16 * 1024 ** 3
        mock_psutil.virtual_memory.return_value = mock_vm
        assert detect_memory_gb() == 16.0

    @patch("resource_detection._HAS_PSUTIL", False)
    def test_returns_0_without_psutil(self):
        assert detect_memory_bytes() == 0
        assert detect_memory_gb() == 0.0


class TestDetectDisk:
    """Tests for detect_disk_bytes()."""

    @patch("resource_detection._HAS_PSUTIL", True)
    @patch("resource_detection.psutil")
    def test_detect_disk_bytes(self, mock_psutil):
        mock_disk = MagicMock()
        mock_disk.total = 500 * 1024 ** 3
        mock_psutil.disk_usage.return_value = mock_disk
        assert detect_disk_bytes("/") == 500 * 1024 ** 3

    @patch("resource_detection._HAS_PSUTIL", False)
    def test_returns_0_without_psutil(self):
        assert detect_disk_bytes("/") == 0


# ---------------------------------------------------------------------------
# Recommendation functions
# ---------------------------------------------------------------------------

class TestRecommendApiWorkers:
    """Tests for recommend_api_workers()."""

    def test_1_cpu(self):
        # 2*1+1=3, but min is 2, so 3
        assert recommend_api_workers(1) == 3

    def test_2_cpus(self):
        assert recommend_api_workers(2) == 5

    def test_4_cpus(self):
        # 2*4+1=9, but capped at 8
        assert recommend_api_workers(4) == 8

    def test_64_cpus(self):
        # capped at 8
        assert recommend_api_workers(64) == 8

    def test_minimum_2(self):
        assert recommend_api_workers(0) >= 2


class TestRecommendPostgresConnections:
    """Tests for Postgres pool sizing."""

    def test_min_connections_scales_with_cpu(self):
        assert recommend_postgres_min_connections(4) == 4
        assert recommend_postgres_min_connections(1) == 2  # minimum 2

    def test_max_connections_balanced(self):
        # 4 cpus, 8 GiB: by_cpu=20, by_mem=24 -> min(20,24,100)=20
        result = recommend_postgres_max_connections(4, 8.0)
        assert result == 20

    def test_max_connections_memory_constrained(self):
        # 8 cpus, 2 GiB: by_cpu=40, by_mem=6 -> min(40,6,100)=6
        result = recommend_postgres_max_connections(8, 2.0)
        assert result == 6

    def test_max_connections_capped_at_100(self):
        result = recommend_postgres_max_connections(64, 512.0)
        assert result <= 100

    def test_max_connections_minimum_5(self):
        result = recommend_postgres_max_connections(1, 0.5)
        assert result >= 5


class TestRecommendRedisConnections:
    """Tests for recommend_redis_max_connections()."""

    def test_scales_with_cpu(self):
        assert recommend_redis_max_connections(4) == 20

    def test_minimum_10(self):
        assert recommend_redis_max_connections(1) == 10

    def test_capped_at_50(self):
        assert recommend_redis_max_connections(64) == 50


class TestRecommendCelery:
    """Tests for Celery concurrency and prefetch (helper functions)."""

    def test_concurrency_scales_with_cpu(self):
        assert recommend_celery_concurrency(4) == 8

    def test_concurrency_minimum_2(self):
        assert recommend_celery_concurrency(1) == 2

    def test_concurrency_capped_at_16(self):
        assert recommend_celery_concurrency(64) == 16

    def test_prefetch_low_memory(self):
        assert recommend_celery_prefetch(0.5) == 1

    def test_prefetch_medium_memory(self):
        assert recommend_celery_prefetch(2.0) == 2

    def test_prefetch_high_memory(self):
        assert recommend_celery_prefetch(16.0) == 4


class TestRecommendNumPredict:
    """Tests for recommend_num_predict() tier boundaries."""

    def test_below_4gb(self):
        assert recommend_num_predict(0) == 1024
        assert recommend_num_predict(3.9) == 1024

    def test_at_4gb(self):
        assert recommend_num_predict(4.0) == 2048

    def test_below_8gb(self):
        assert recommend_num_predict(7.9) == 2048

    def test_at_8gb(self):
        assert recommend_num_predict(8.0) == 4096

    def test_below_16gb(self):
        assert recommend_num_predict(15.9) == 4096

    def test_at_16gb(self):
        assert recommend_num_predict(16.0) == 8192

    def test_below_32gb(self):
        assert recommend_num_predict(31.9) == 8192

    def test_at_32gb(self):
        assert recommend_num_predict(32.0) == 16384


class TestRecommendNumCtx:
    """Tests for recommend_num_ctx() tier boundaries."""

    def test_below_4gb(self):
        assert recommend_num_ctx(0) == 2048
        assert recommend_num_ctx(3.9) == 2048

    def test_at_4gb(self):
        assert recommend_num_ctx(4.0) == 4096

    def test_below_8gb(self):
        assert recommend_num_ctx(7.9) == 4096

    def test_at_8gb(self):
        assert recommend_num_ctx(8.0) == 8192

    def test_below_16gb(self):
        assert recommend_num_ctx(15.9) == 8192

    def test_at_16gb(self):
        assert recommend_num_ctx(16.0) == 16384

    def test_below_32gb(self):
        assert recommend_num_ctx(31.9) == 16384

    def test_at_32gb(self):
        assert recommend_num_ctx(32.0) == 32768


# ---------------------------------------------------------------------------
# ResourceProfile
# ---------------------------------------------------------------------------

class TestResourceProfile:
    """Tests for the ResourceProfile dataclass."""

    def test_summary_lines_returns_list(self):
        profile = ResourceProfile(cpu_count=4, memory_gb=8.0)
        lines = profile.summary_lines()
        assert isinstance(lines, list)
        assert len(lines) > 0
        assert any("CPU cores: 4" in line for line in lines)

    def test_default_values(self):
        profile = ResourceProfile()
        assert profile.cpu_count == 1
        assert profile.api_workers == 2
        assert profile.postgres_max_connections == 20

    def test_no_celery_fields(self):
        """Celery values should not be in the profile (not wired in yet)."""
        profile = ResourceProfile()
        assert not hasattr(profile, 'celery_concurrency')
        assert not hasattr(profile, 'celery_prefetch_multiplier')

    def test_summary_lines_no_celery(self):
        """Summary output should not mention Celery."""
        profile = ResourceProfile(cpu_count=4, memory_gb=8.0)
        joined = "\n".join(profile.summary_lines())
        assert "Celery" not in joined


# ---------------------------------------------------------------------------
# detect_resources() with env-var overrides
# ---------------------------------------------------------------------------

class TestDetectResources:
    """Tests for detect_resources() integration and env-var overrides."""

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_builds_profile_from_detected_hardware(self, *_mocks):
        profile = detect_resources()
        assert profile.cpu_count == 4
        assert profile.memory_gb == 8.0
        assert profile.api_workers == recommend_api_workers(4)

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_env_var_overrides_detected_value(self, *_mocks):
        with patch.dict(os.environ, {"API_WORKERS": "16"}):
            profile = detect_resources()
            assert profile.api_workers == 16

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_invalid_env_var_ignored(self, *_mocks):
        with patch.dict(os.environ, {"API_WORKERS": "not_a_number"}):
            profile = detect_resources()
            # Should fall back to auto-detected value
            assert profile.api_workers == recommend_api_workers(4)

    @patch("resource_detection.detect_cpu_count", return_value=2)
    @patch("resource_detection.detect_memory_bytes", return_value=4 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=4.0)
    @patch("resource_detection.detect_disk_bytes", return_value=50 * 1024**3)
    def test_redis_pool_override(self, *_mocks):
        with patch.dict(os.environ, {"REDIS_MAX_CONNECTIONS": "42"}):
            profile = detect_resources()
            assert profile.redis_max_connections == 42

    @patch("resource_detection.detect_cpu_count", return_value=2)
    @patch("resource_detection.detect_memory_bytes", return_value=4 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=4.0)
    @patch("resource_detection.detect_disk_bytes", return_value=50 * 1024**3)
    def test_postgres_pool_override(self, *_mocks):
        with patch.dict(os.environ, {
            "POSTGRES_MIN_CONNECTIONS": "5",
            "POSTGRES_MAX_CONNECTIONS": "50",
        }):
            profile = detect_resources()
            assert profile.postgres_min_connections == 5
            assert profile.postgres_max_connections == 50

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_ollama_num_predict_env_override(self, *_mocks):
        with patch.dict(os.environ, {"OLLAMA_NUM_PREDICT": "512"}):
            profile = detect_resources()
            assert profile.num_predict == 512

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_ollama_num_ctx_env_override(self, *_mocks):
        with patch.dict(os.environ, {"OLLAMA_NUM_CTX": "2048"}):
            profile = detect_resources()
            assert profile.num_ctx == 2048


# ---------------------------------------------------------------------------
# Bug fix: postgres min > max clamping
# ---------------------------------------------------------------------------

class TestPostgresMinMaxClamping:
    """Verify that min connections never exceeds max connections."""

    @patch("resource_detection.detect_cpu_count", return_value=8)
    @patch("resource_detection.detect_memory_bytes", return_value=2 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=2.0)
    @patch("resource_detection.detect_disk_bytes", return_value=50 * 1024**3)
    def test_min_clamped_to_max_on_low_memory_many_cores(self, *_mocks):
        """8 CPUs + 2 GiB: min would be 8, max would be 6 → min must clamp."""
        profile = detect_resources()
        assert profile.postgres_min_connections <= profile.postgres_max_connections

    @patch("resource_detection.detect_cpu_count", return_value=16)
    @patch("resource_detection.detect_memory_bytes", return_value=1 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=1.0)
    @patch("resource_detection.detect_disk_bytes", return_value=50 * 1024**3)
    def test_extreme_imbalance(self, *_mocks):
        """16 CPUs + 1 GiB: min=16 but max=min(80,3,100)=5 → must clamp."""
        profile = detect_resources()
        assert profile.postgres_min_connections <= profile.postgres_max_connections
        # max should be at least 5 (floor)
        assert profile.postgres_max_connections >= 5


# ---------------------------------------------------------------------------
# Bug fix: negative / zero env-var overrides rejected
# ---------------------------------------------------------------------------

class TestNegativeEnvVarRejection:
    """Verify that zero or negative env-var values are rejected."""

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_zero_workers_rejected(self, *_mocks):
        with patch.dict(os.environ, {"API_WORKERS": "0"}):
            profile = detect_resources()
            # Should ignore 0 and use auto-detected value
            assert profile.api_workers == recommend_api_workers(4)

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_negative_redis_pool_rejected(self, *_mocks):
        with patch.dict(os.environ, {"REDIS_MAX_CONNECTIONS": "-5"}):
            profile = detect_resources()
            # Should ignore -5 and use auto-detected value
            assert profile.redis_max_connections == recommend_redis_max_connections(4)

    @patch("resource_detection.detect_cpu_count", return_value=4)
    @patch("resource_detection.detect_memory_bytes", return_value=8 * 1024**3)
    @patch("resource_detection.detect_memory_gb", return_value=8.0)
    @patch("resource_detection.detect_disk_bytes", return_value=100 * 1024**3)
    def test_negative_postgres_rejected(self, *_mocks):
        with patch.dict(os.environ, {"POSTGRES_MAX_CONNECTIONS": "-1"}):
            profile = detect_resources()
            assert profile.postgres_max_connections == recommend_postgres_max_connections(4, 8.0)


# ---------------------------------------------------------------------------
# Backbone selection — gemma4 everywhere, size adapts to the hardware
#
# The product decision is that gemma4 is the brain on every deployment, so the
# tutoring behaviour is the same everywhere and only capacity changes. The old
# deploy.sh ladder swapped to qwen3.5 families below 16GB, which meant a small
# machine ran a DIFFERENT tutor — different persona adherence, different
# pedagogy, and none of the compliance work validated against it.
#
# gemma4 publishes no quant-suffixed tags (gemma4:e4b-q4_K_M and friends all
# 404 in the registry, checked 2026-09-09). It ships pre-quantized at Q4_K_M and
# varies by SIZE VARIANT instead, so "quantize for the hardware" means selecting
# the largest variant that fits:
#
# Each variant carries TWO sizes, because what must fit differs by path and for
# the MatFormer variants the two differ ~3x (measured 2026-09-10):
#
#                  resident VRAM   RAM footprint
#     gemma4:e4b       3.3 GB          9.5 GB     <- best measured quality
#     gemma4:12b       7.9 GB          7.6 GB     <- only fits the CPU 13.6-15.5 band
#
# gemma4:e2b was REMOVED 2026-09-10: 70.0 overall vs e4b's 79.0 and 74.9 vs 81.9
# on homework integrity over 3 repeats — ~4x the harness's run-to-run noise. It
# was the silent floor every small box fell back to, so the worst-case
# deployment was the weakest at withholding homework answers. Under-spec boxes
# now get None and an explicit unsupported-hardware error, never a quiet
# downgrade.
#
# Selection is by the memory the model will actually live in — VRAM when there
# is a usable GPU, otherwise RAM — minus a reserve that ALSO differs by path
# (the safety classifier is CPU-pinned, so it costs RAM, never VRAM).
# ---------------------------------------------------------------------------

from resource_detection import (  # noqa: E402
    GEMMA4_VARIANTS,
    minimum_requirements_gb,
    recommend_base_model,
    unsupported_hardware_message,
)


def test_every_variant_is_gemma4():
    """The whole point: one family everywhere."""
    assert GEMMA4_VARIANTS, "ladder must not be empty"
    for tag, vram_gb, ram_gb in GEMMA4_VARIANTS:
        assert tag.startswith("gemma4:"), f"{tag} is not gemma4"
        assert vram_gb > 0 and ram_gb > 0


def test_no_qwen_anywhere_in_the_ladder():
    """Regression guard: qwen was removed from the product 2026-09-10.

    A small box used to be handed a qwen3.5 tier — a DIFFERENT tutor that none
    of the persona, pedagogy or S9051B compliance work was measured against.
    """
    for tag, _vram, _ram in GEMMA4_VARIANTS:
        assert "qwen" not in tag.lower()


def test_removed_e2b_is_not_selectable_at_any_size():
    """e2b measured 9 points below e4b overall and 7 below on homework
    integrity. It must not come back as a silent floor."""
    for ram in (1, 4, 8, 12, 16, 32, 64, 512):
        for vram in (0, 2, 6, 12, 24, 80):
            assert recommend_base_model(memory_gb=ram, vram_gb=vram) != "gemma4:e2b"


def test_ladder_is_ordered_by_preference():
    """First that fits wins, so the best-measured variant must come first.

    NOT ordered by size any more: on a GPU e4b is both better AND smaller
    (3.3 vs 7.9 GB resident), so a size ordering would be actively wrong.
    """
    assert GEMMA4_VARIANTS[0][0] == "gemma4:e4b"


def test_big_gpu_box_gets_the_validated_default():
    """A 24GB card: e4b, which is the variant all the tutoring and compliance
    work was actually measured on."""
    assert recommend_base_model(memory_gb=64, vram_gb=24) == "gemma4:e4b"


def test_midsize_box_steps_down_rather_than_changing_family():
    """Not enough for e4b — step down inside gemma4, never to another family.

    The step-down now lives on the RAM path only. On a GPU e4b is the SMALLEST
    variant (3.3 GB resident), so it fits anything 12b would, and there is
    nothing to step down to. On RAM e4b needs 15.5 GB and 12b only 13.6 GB, so
    a 14 GB CPU box is the real step-down case.
    """
    picked = recommend_base_model(memory_gb=14, vram_gb=0)
    assert picked == "gemma4:12b"
    assert picked.startswith("gemma4:")


def test_cpu_pinned_guard_is_not_charged_to_vram():
    """Regression guard for the reserve split (2026-09-10).

    The guard's ~4.9 GB comes out of RAM, never VRAM. Reunify the reserves and
    the GPU cases under-select.
    """
    assert recommend_base_model(memory_gb=32, vram_gb=14) == "gemma4:e4b"
    assert recommend_base_model(memory_gb=32, vram_gb=6) == "gemma4:e4b"
    # RAM path: the guard really IS resident alongside, so 12 GB is too small.
    assert recommend_base_model(memory_gb=12, vram_gb=0) is None


def test_vram_is_sized_on_RESIDENT_not_manifest_size():
    """e4b's manifest total is 9.6 GB but only 3.3 GB is resident on a GPU.

    Sizing from the manifest told a 12 GB card it could not run e4b, which it
    runs comfortably. If someone restores manifest sizes, this fails.
    """
    assert recommend_base_model(memory_gb=64, vram_gb=9) == "gemma4:e4b"
    assert recommend_base_model(memory_gb=64, vram_gb=6) == "gemma4:e4b"


def test_small_gpu_still_gets_the_best_variant():
    """A 9 GB card runs e4b — it only needs 3.3 GB resident + 2.5 headroom."""
    assert recommend_base_model(memory_gb=16, vram_gb=9) == "gemma4:e4b"


def test_cpu_only_box_selects_on_ram():
    """No GPU: the model lives in RAM, so RAM is what decides."""
    assert recommend_base_model(memory_gb=64, vram_gb=0) == "gemma4:e4b"
    assert recommend_base_model(memory_gb=14, vram_gb=0) == "gemma4:12b"


def test_under_spec_machine_is_REFUSED_not_downgraded():
    """The product decision (2026-09-10): a box that cannot run a measured-safe
    backbone gets an explicit refusal, NOT a quietly weaker tutor.

    This test is the inverse of the one it replaces
    (`test_tiny_machine_still_gets_a_working_model`), which asserted a Pi-class
    box got e2b "rather than no tutor at all". That floor is exactly what we
    removed: e2b was measurably worse at withholding homework answers, and
    shipping it silently to the smallest deployments was the wrong trade for a
    children's product.
    """
    assert recommend_base_model(memory_gb=4, vram_gb=0) is None
    assert recommend_base_model(memory_gb=1, vram_gb=0) is None
    assert recommend_base_model(memory_gb=12, vram_gb=0) is None
    assert recommend_base_model(memory_gb=64, vram_gb=5) is None


def test_unsupported_message_names_the_actual_requirement():
    """Operators must be told the number, not just 'unsupported'."""
    min_vram, min_ram = minimum_requirements_gb()
    assert (min_vram, min_ram) == (5.8, 13.6)
    msg = unsupported_hardware_message(memory_gb=4.0, vram_gb=0.0)
    assert str(min_vram) in msg and str(min_ram) in msg
    assert "unsupported hardware" in msg.lower()


def test_gpu_is_preferred_over_ram_when_present():
    """A big-RAM box with a card sizes for the CARD, because that is where the
    model will actually be loaded. A 5 GB card cannot host any variant, so it
    is refused even with 128 GB of RAM."""
    assert recommend_base_model(memory_gb=128, vram_gb=9) == "gemma4:e4b"
    assert recommend_base_model(memory_gb=128, vram_gb=5) is None


def test_reserve_accounts_for_the_safety_classifier_on_the_ram_path():
    """llama-guard is CPU-pinned, so it must fit in RAM alongside the tutor.
    With the reserve removed a 12 GB box would take e4b; with it, it must not —
    the classifier being evicted is what makes the pipeline fail closed and
    block children on harmless questions."""
    generous = recommend_base_model(memory_gb=12, vram_gb=0, reserve_gb=0.0)
    realistic = recommend_base_model(memory_gb=12, vram_gb=0)
    assert generous == "gemma4:e4b"   # 9.5 GB footprint alone fits in 12 GB
    assert realistic is None          # ...but not once the guard needs its 4.9


def test_selection_is_deterministic():
    assert recommend_base_model(memory_gb=32, vram_gb=16) == recommend_base_model(
        memory_gb=32, vram_gb=16
    )


# ---------------------------------------------------------------------------
# GPU placement — gemma4:e4b ships `PARAMETER num_gpu 0` in its OWN manifest,
# and every model built `FROM gemma4:e4b` inherits it silently.
#
# Found 2026-09-09 on a box with a free 23GB card: the tutor was running 100%
# on CPU, which is roughly 20x slower and accounts for the 47-197s replies
# measured earlier and blamed on other things. Nothing in this repo set that
# pin and nothing in this repo overrode it.
#
# The override cannot simply be a hardcoded `num_gpu 99`. That forces every
# layer onto the card, so a machine with a small GPU — which the variant ladder
# still serves, because gemma4:e2b is the floor no matter how little VRAM there
# is — would try to load 7.2GB into 4GB and fail to start at all. A tutor that
# is slow beats a tutor that will not load.
# ---------------------------------------------------------------------------


class TestRecommendNumGpu:
    def test_no_gpu_means_cpu(self):
        from resource_detection import recommend_num_gpu

        assert recommend_num_gpu("gemma4:e4b", vram_gb=0.0) == 0

    def test_ample_vram_offloads_all_layers(self):
        from resource_detection import recommend_num_gpu

        assert recommend_num_gpu("gemma4:e4b", vram_gb=23.0) > 0

    def test_gpu_too_small_for_the_variant_stays_on_cpu(self):
        """The failure this guards: a card too small for the variant's RESIDENT
        size. 12b needs 7.9 GB resident, so a 4 GB card must stay on CPU.

        Was written against gemma4:e2b, removed from the product 2026-09-10.
        """
        from resource_detection import recommend_num_gpu

        assert recommend_num_gpu("gemma4:12b", vram_gb=4.0) == 0

    def test_removed_variant_is_never_offloaded(self):
        """An unknown/removed tag returns 0 rather than guessing a size."""
        from resource_detection import recommend_num_gpu

        assert recommend_num_gpu("gemma4:e2b", vram_gb=24.0) == 0
        assert recommend_num_gpu("qwen3.5:9b", vram_gb=24.0) == 0

    def test_headroom_is_required_not_just_a_bare_fit(self):
        """A variant that only fits with nothing left over must not be offloaded:
        the KV cache and the runtime still need room, and a card packed to the
        last byte evicts the safety classifier mid-request."""
        from resource_detection import recommend_num_gpu

        # e4b is 3.3 GB RESIDENT (not its 9.6 GB manifest total), so a bare fit
        # is ~3.3 GB and the real bar is 3.3 + 2.5 headroom = 5.8 GB.
        assert recommend_num_gpu("gemma4:e4b", vram_gb=4.0) == 0
        assert recommend_num_gpu("gemma4:e4b", vram_gb=6.0) == 99

    def test_unknown_tag_is_not_assumed_to_fit(self):
        from resource_detection import recommend_num_gpu

        assert recommend_num_gpu("some-other-model:latest", vram_gb=4.0) == 0

    def test_the_measured_box(self):
        """23GB card, e4b selected — the configuration that was silently on CPU."""
        from resource_detection import recommend_base_model, recommend_num_gpu

        tag = recommend_base_model(memory_gb=64.0, vram_gb=23.0)
        assert tag == "gemma4:e4b"
        assert recommend_num_gpu(tag, vram_gb=23.0) > 0


# ---------------------------------------------------------------------------
# ENTRY-POINT CONSISTENCY — the guard that was missing on 2026-09-10.
#
# The ladder had drifted into SEVEN hardcoded copies (deploy.sh,
# start_snflwr.sh, start_snflwr.ps1, START_SNFLWR.bat, installer/,
# enterprise/build.sh, and two embedded in scripts/build_usb_image.py). Two of
# them selected NO gemma at all, so Windows installs ran a different model
# family — a different tutor, with none of the persona / pedagogy / S9051B
# compliance work measured against it.
#
# ~4,172 tests passed the whole time. Every one of them tested the ladder's
# CONTENTS; none asserted that the entry points actually USE it. These do.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that choose a backbone at install/start time and must delegate.
_DELEGATING_ENTRY_POINTS = [
    "deploy.sh",
    "start_snflwr.sh",
    "start_snflwr.ps1",
    "START_SNFLWR.bat",
    "installer/ollama_setup.py",
]

# Files that build the user-facing snflwr.ai wrapper. gemma4 ships
# `PARAMETER num_gpu 0` in its own manifest and `FROM` inherits it, so each of
# these MUST override placement or it silently ships a CPU-pinned tutor
# (~20x slower) onto a machine with an idle GPU.
_WRAPPER_BUILDERS = [
    "deploy.sh",
    "start_snflwr.sh",
    "start_snflwr.ps1",
    "installer/ollama_setup.py",
]


@pytest.mark.parametrize("rel", _DELEGATING_ENTRY_POINTS)
def test_entry_point_delegates_to_the_shared_ladder(rel):
    """No entry point may carry its own hardcoded backbone ladder."""
    text = (_REPO_ROOT / rel).read_text(errors="ignore")
    assert "recommend_base_model" in text, (
        f"{rel} selects a backbone without calling "
        f"resource_detection.recommend_base_model. That is how seven divergent "
        f"ladders happened; add the call rather than a local tier list."
    )


@pytest.mark.parametrize("rel", _WRAPPER_BUILDERS)
def test_wrapper_builder_sets_gpu_placement(rel):
    """Every path that builds snflwr.ai must compute num_gpu."""
    text = (_REPO_ROOT / rel).read_text(errors="ignore")
    assert "recommend_num_gpu" in text, (
        f"{rel} builds the snflwr.ai wrapper but never sets num_gpu. gemma4 "
        f"ships `PARAMETER num_gpu 0` and FROM inherits it, so this ships a "
        f"CPU-pinned tutor onto machines with a perfectly good GPU."
    )


def test_no_entry_point_hardcodes_a_rival_model_family():
    """Guards the specific regression: a non-gemma tier reappearing.

    `scripts/build_usb_image.py` is exempt from DELEGATING (it emits a
    standalone script for a machine that will not have resource_detection.py),
    but it is NOT exempt from being gemma-only.
    """
    for rel in _DELEGATING_ENTRY_POINTS + ["scripts/build_usb_image.py", "enterprise/build.sh"]:
        text = (_REPO_ROOT / rel).read_text(errors="ignore").lower()
        for family in ("qwen", "llama3:", "mistral:", "phi3:"):
            assert family not in text, f"{rel} references a non-gemma backbone family: {family}"


# ---------------------------------------------------------------------------
# CONTEXT SIZING — the KV cache lives where the MODEL lives (fixed 2026-09-10).
#
# recommend_num_ctx keyed on RAM alone, so a box with plenty of RAM and a small
# card was handed a context its VRAM could not back. Third instance of the same
# dual-budget mistake: the CPU-pinned guard charged against VRAM, and variant
# sizes read from manifest totals instead of resident VRAM.
#
# Measured cost (gemma4:e4b, q4_0 cache, via /api/ps on 2026-09-10):
#   4096 -> 3.20 GB   16384 -> 3.34 GB   32768 -> 3.44 GB  => ~0.0086 GB/1k
# ---------------------------------------------------------------------------

from resource_detection import (  # noqa: E402
    KV_GB_PER_1K_TOKENS,
    recommend_num_ctx,
)


def test_context_sizes_on_vram_when_the_model_is_on_gpu():
    """A tiny card must not inherit a big-RAM context it cannot back."""
    generous_ram_tiny_card = recommend_num_ctx(256.0, vram_gb=4.0, model_tag="gemma4:e4b")
    generous_ram_no_card = recommend_num_ctx(256.0, vram_gb=0.0)
    assert generous_ram_tiny_card < generous_ram_no_card, (
        "with only 4 GB of VRAM the KV cache has nowhere to live; sizing must "
        "come off the card, not off 256 GB of RAM the cache will never touch"
    )


def test_cpu_path_still_sizes_on_ram():
    """No GPU: model and KV cache are both in RAM, so RAM decides."""
    assert recommend_num_ctx(64.0, vram_gb=0.0) == 32768
    assert recommend_num_ctx(8.0, vram_gb=0.0) == 8192
    assert recommend_num_ctx(2.0, vram_gb=0.0) == 2048


def test_supported_gpu_hardware_still_gets_full_context():
    """The fix must not silently shrink context on hardware we support.

    KV is cheap for e4b (~0.33 GB at 32k), so every card at or above the 5.8 GB
    floor should still get the full window. If this starts failing, either the
    measured constant moved or a denser backbone entered the ladder.
    """
    for vram in (6.0, 8.0, 12.0, 23.0):
        assert recommend_num_ctx(64.0, vram_gb=vram, model_tag="gemma4:e4b") == 32768


def test_kv_constant_is_the_measured_one():
    """Pinned so a future edit re-measures rather than guesses.

    Rounded UP from the measured 0.0086: under-estimating means the runner
    fails to start, which is worse than a slightly smaller window.
    """
    assert KV_GB_PER_1K_TOKENS == 0.010


def test_unknown_model_tag_assumes_the_smallest_weights():
    """An unknown tag must not crash or silently assume a huge model."""
    assert recommend_num_ctx(64.0, vram_gb=23.0, model_tag="not-a-real-model") == 32768
