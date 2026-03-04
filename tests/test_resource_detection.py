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
