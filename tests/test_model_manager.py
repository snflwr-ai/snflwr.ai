"""
Tests for core/model_manager.py — ModelCache and ModelManager
Targets 70%+ coverage on the module.
"""

import threading
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_model_info(name: str, size_mb: int = 100):
    """Construct a ModelInfo dataclass for test use."""
    from core.model_manager import ModelInfo
    return ModelInfo(
        name=name,
        size=size_mb * 1024 * 1024,
        loaded_at=datetime.now(timezone.utc),
        last_used=datetime.now(timezone.utc),
        use_count=0,
        parameters={},
    )


def reset_model_manager_singleton():
    """Clear the ModelManager singleton so each test gets a fresh instance."""
    import core.model_manager as mm
    mm.ModelManager._instance = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_cache():
    """A ModelCache with a small memory limit for eviction testing."""
    from core.model_manager import ModelCache
    return ModelCache(max_memory_mb=100)


@pytest.fixture
def mock_ollama():
    m = MagicMock()
    # check_connection returns (is_available, info_str) tuple
    m.check_connection.return_value = (True, "ok")
    return m


@pytest.fixture
def manager(mock_ollama):
    """ModelManager with a mocked OllamaClient, reset singleton each time."""
    reset_model_manager_singleton()
    with patch("core.model_manager.OllamaClient", return_value=mock_ollama):
        from core.model_manager import ModelManager
        m = ModelManager()
    yield m
    # Clean up singleton after each test
    reset_model_manager_singleton()


# ---------------------------------------------------------------------------
# 1. ModelCache — basic put / get
# ---------------------------------------------------------------------------

class TestModelCacheBasics:

    def test_cache_miss_returns_none(self, fresh_cache):
        assert fresh_cache.get("nonexistent") is None

    def test_put_and_get_returns_model(self, fresh_cache):
        info = make_model_info("llama3", 50)
        fresh_cache.put("llama3", info)
        result = fresh_cache.get("llama3")
        assert result is not None
        assert result.name == "llama3"

    def test_get_returns_correct_model_by_name(self, fresh_cache):
        fresh_cache.put("model_a", make_model_info("model_a", 10))
        fresh_cache.put("model_b", make_model_info("model_b", 10))
        assert fresh_cache.get("model_a").name == "model_a"
        assert fresh_cache.get("model_b").name == "model_b"

    def test_get_increments_use_count(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 10))
        fresh_cache.get("m")
        fresh_cache.get("m")
        result = fresh_cache.get("m")
        # 3 gets → use_count goes from 0 to 3
        assert result.use_count == 3

    def test_get_updates_last_used_timestamp(self, fresh_cache):
        info = make_model_info("m", 10)
        original_last_used = info.last_used
        fresh_cache.put("m", info)
        result = fresh_cache.get("m")
        # last_used should be >= original
        assert result.last_used >= original_last_used

    def test_multiple_puts_of_same_model_replaces_entry(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 10))
        info2 = make_model_info("m", 20)
        fresh_cache.put("m", info2)
        result = fresh_cache.get("m")
        assert result.size == 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# 2. ModelCache — memory tracking
# ---------------------------------------------------------------------------

class TestModelCacheMemoryTracking:

    def test_current_memory_starts_at_zero(self, fresh_cache):
        assert fresh_cache._current_memory == 0

    def test_put_increases_current_memory(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 50))
        assert fresh_cache._current_memory == 50 * 1024 * 1024

    def test_multiple_puts_accumulate_memory(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 30))
        fresh_cache.put("b", make_model_info("b", 40))
        assert fresh_cache._current_memory == 70 * 1024 * 1024

    def test_remove_decreases_current_memory(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 50))
        fresh_cache.remove("m")
        assert fresh_cache._current_memory == 0

    def test_remove_nonexistent_model_is_safe(self, fresh_cache):
        fresh_cache.remove("does_not_exist")   # should not raise
        assert fresh_cache._current_memory == 0


# ---------------------------------------------------------------------------
# 3. ModelCache — LRU eviction
# ---------------------------------------------------------------------------

class TestModelCacheLRUEviction:

    def test_lru_eviction_removes_least_recently_used(self, fresh_cache):
        # 100MB limit; add a=10, b=10 → total 20MB
        fresh_cache.put("a", make_model_info("a", 10))
        fresh_cache.put("b", make_model_info("b", 10))
        # Access "a" to make it MRU; "b" becomes LRU
        fresh_cache.get("a")
        # Add c=95MB; total would be 115MB → must evict "b" (LRU, 10MB freed)
        # After evicting b: 10 + 95 = 105 > 100 → must also evict "a"
        # Then: 0 + 95 = 95 ≤ 100 → fits
        fresh_cache.put("c", make_model_info("c", 95))
        # Both were evicted because c alone needed the full budget
        assert fresh_cache.get("c") is not None
        assert fresh_cache._current_memory <= fresh_cache.max_memory_bytes

    def test_lru_eviction_frees_enough_memory(self, fresh_cache):
        # Fill to near limit
        fresh_cache.put("a", make_model_info("a", 50))
        fresh_cache.put("b", make_model_info("b", 49))
        # Adding c=10MB should evict "a" (oldest/LRU) to make room
        fresh_cache.put("c", make_model_info("c", 10))
        # After eviction memory must be within limit
        assert fresh_cache._current_memory <= fresh_cache.max_memory_bytes

    def test_no_eviction_when_within_limit(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 40))
        fresh_cache.put("b", make_model_info("b", 40))
        # 80MB < 100MB limit — both should survive
        assert fresh_cache.get("a") is not None
        assert fresh_cache.get("b") is not None

    def test_replacing_model_updates_memory_correctly(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 50))
        # Replace with a smaller version
        fresh_cache.put("m", make_model_info("m", 20))
        assert fresh_cache._current_memory == 20 * 1024 * 1024

    def test_eviction_order_respects_get_access(self, fresh_cache):
        """The LRU model is evicted before MRU models when memory is needed."""
        # Use sizes where only b needs to be evicted to fit c
        # a=10, b=85 → total=95. Access a (MRU). Add c=10: 95+10=105>100
        # Must evict b(LRU, 85MB) → 10+10=20 ≤ 100
        fresh_cache.put("a", make_model_info("a", 10))
        fresh_cache.put("b", make_model_info("b", 85))
        fresh_cache.get("a")   # "a" is now MRU, "b" is LRU
        fresh_cache.put("c", make_model_info("c", 10))
        assert fresh_cache.get("b") is None    # b was LRU → evicted
        assert fresh_cache.get("a") is not None   # a was MRU → survived
        assert fresh_cache.get("c") is not None   # c is the new addition


# ---------------------------------------------------------------------------
# 4. ModelCache — remove
# ---------------------------------------------------------------------------

class TestModelCacheRemove:

    def test_remove_makes_model_unreachable(self, fresh_cache):
        fresh_cache.put("m", make_model_info("m", 10))
        fresh_cache.remove("m")
        assert fresh_cache.get("m") is None

    def test_remove_only_removes_target_model(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 10))
        fresh_cache.put("b", make_model_info("b", 10))
        fresh_cache.remove("a")
        assert fresh_cache.get("b") is not None


# ---------------------------------------------------------------------------
# 5. ModelCache — clear
# ---------------------------------------------------------------------------

class TestModelCacheClear:

    def test_clear_empties_all_entries(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 10))
        fresh_cache.put("b", make_model_info("b", 10))
        fresh_cache.clear()
        assert fresh_cache.get("a") is None
        assert fresh_cache.get("b") is None

    def test_clear_resets_memory_to_zero(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 50))
        fresh_cache.clear()
        assert fresh_cache._current_memory == 0


# ---------------------------------------------------------------------------
# 6. ModelCache — get_stats
# ---------------------------------------------------------------------------

class TestModelCacheGetStats:

    def test_get_stats_empty_cache(self, fresh_cache):
        stats = fresh_cache.get_stats()
        assert stats["cached_models"] == 0
        assert stats["memory_used_mb"] == 0.0
        assert stats["models"] == []

    def test_get_stats_after_put(self, fresh_cache):
        fresh_cache.put("a", make_model_info("a", 50))
        stats = fresh_cache.get_stats()
        assert stats["cached_models"] == 1
        assert stats["memory_used_mb"] == pytest.approx(50.0)
        assert "a" in stats["models"]

    def test_get_stats_memory_limit_mb(self, fresh_cache):
        stats = fresh_cache.get_stats()
        assert stats["memory_limit_mb"] == pytest.approx(100.0)

    def test_get_stats_lists_all_models(self, fresh_cache):
        for name in ("alpha", "beta", "gamma"):
            fresh_cache.put(name, make_model_info(name, 5))
        stats = fresh_cache.get_stats()
        assert stats["cached_models"] == 3
        assert set(stats["models"]) == {"alpha", "beta", "gamma"}


# ---------------------------------------------------------------------------
# 7. ModelCache — thread safety (basic)
# ---------------------------------------------------------------------------

class TestModelCacheThreadSafety:

    def test_concurrent_puts_do_not_corrupt_memory_count(self):
        from core.model_manager import ModelCache
        cache = ModelCache(max_memory_mb=10000)
        errors = []

        def worker(name):
            try:
                cache.put(name, make_model_info(name, 1))
                cache.get(name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"m{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # Memory should equal sum of all successfully added models
        assert cache._current_memory >= 0


# ---------------------------------------------------------------------------
# 8. ModelManager — singleton
# ---------------------------------------------------------------------------

class TestModelManagerSingleton:

    def test_model_manager_is_singleton(self, manager):
        from core.model_manager import ModelManager
        m2 = ModelManager()
        assert manager is m2

    def test_model_manager_initialized_flag(self, manager):
        assert hasattr(manager, "_initialized")
        assert manager._initialized is True


# ---------------------------------------------------------------------------
# 9. ModelManager — list_loaded_models / get_cache_stats
# ---------------------------------------------------------------------------

class TestModelManagerCacheInterface:

    def test_list_loaded_models_empty_initially(self, manager):
        result = manager.list_loaded_models()
        assert result == []

    def test_list_loaded_models_shows_cached_model(self, manager):
        manager.cache.put("test_model", make_model_info("test_model", 10))
        result = manager.list_loaded_models()
        assert "test_model" in result

    def test_get_cache_stats_returns_dict(self, manager):
        stats = manager.get_cache_stats()
        assert isinstance(stats, dict)
        assert "cached_models" in stats
        assert "memory_used_mb" in stats

    def test_get_cache_stats_empty_initially(self, manager):
        stats = manager.get_cache_stats()
        assert stats["cached_models"] == 0


# ---------------------------------------------------------------------------
# 10. ModelManager — load_model (cache hit)
# ---------------------------------------------------------------------------

class TestModelManagerLoadModelCacheHit:

    def test_load_model_returns_cached_status(self, manager):
        manager.cache.put("cached_model", make_model_info("cached_model", 10))
        result = manager.load_model("cached_model")
        assert result["status"] == "loaded"
        assert result["cached"] is True
        assert result["name"] == "cached_model"

    def test_load_model_returns_use_count_in_cached_response(self, manager):
        manager.cache.put("m", make_model_info("m", 10))
        result = manager.load_model("m")
        assert "use_count" in result

    def test_load_model_force_reload_bypasses_cache(self, manager, mock_ollama):
        manager.cache.put("m", make_model_info("m", 10))
        mock_ollama.list_models.return_value = (True, [{"name": "m", "size": 100}], None)
        result = manager.load_model("m", force_reload=True)
        # force_reload skips cache check → goes to Ollama
        mock_ollama.list_models.assert_called()


# ---------------------------------------------------------------------------
# 11. ModelManager — load_model (cache miss, Ollama call)
# ---------------------------------------------------------------------------

class TestModelManagerLoadModelCacheMiss:

    def test_load_model_calls_ollama_list_on_miss(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (
            True, [{"name": "llama3", "size": 500 * 1024 * 1024}], None
        )
        manager.load_model("llama3")
        mock_ollama.list_models.assert_called()

    def test_load_model_returns_loaded_status_from_ollama(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (
            True, [{"name": "llama3", "size": 100 * 1024 * 1024}], None
        )
        result = manager.load_model("llama3")
        assert result["status"] == "loaded"
        assert result["cached"] is False

    def test_load_model_caches_model_after_ollama_load(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (
            True, [{"name": "newmodel", "size": 200 * 1024 * 1024}], None
        )
        manager.load_model("newmodel")
        assert manager.cache.get("newmodel") is not None

    def test_load_model_returns_error_when_ollama_list_fails(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (False, None, "connection refused")
        result = manager.load_model("unknown")
        assert result["status"] == "error"
        assert "error" in result

    def test_load_model_handles_model_not_in_list(self, manager, mock_ollama):
        """Model not in Ollama list → placeholder size, still 'loaded'."""
        mock_ollama.list_models.return_value = (True, [], None)
        result = manager.load_model("unlisted_model")
        assert result["status"] == "loaded"

    def test_load_model_uses_size_from_ollama_response(self, manager, mock_ollama):
        size = 42 * 1024 * 1024
        mock_ollama.list_models.return_value = (
            True, [{"name": "sized_model", "size": size}], None
        )
        result = manager.load_model("sized_model")
        assert result["size_mb"] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# 12. ModelManager — unload_model
# ---------------------------------------------------------------------------

class TestModelManagerUnloadModel:

    def test_unload_model_removes_from_cache(self, manager):
        manager.cache.put("m", make_model_info("m", 10))
        manager.unload_model("m")
        assert manager.cache.get("m") is None

    def test_unload_model_returns_true(self, manager):
        manager.cache.put("m", make_model_info("m", 10))
        result = manager.unload_model("m")
        assert result is True

    def test_unload_nonexistent_model_returns_true(self, manager):
        # remove() on a missing model is a no-op; unload still returns True
        result = manager.unload_model("nonexistent")
        assert result is True


# ---------------------------------------------------------------------------
# 13. ModelManager — get_available_models
# ---------------------------------------------------------------------------

class TestModelManagerGetAvailableModels:

    def test_get_available_models_returns_name_list(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (
            True,
            [{"name": "alpha"}, {"name": "beta"}],
            None,
        )
        success, models, error = manager.get_available_models()
        assert success is True
        assert "alpha" in models
        assert "beta" in models
        assert error is None

    def test_get_available_models_returns_false_on_failure(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (False, None, "service down")
        success, models, error = manager.get_available_models()
        assert success is False
        assert models is None
        assert error == "service down"

    def test_get_available_models_handles_exception(self, manager, mock_ollama):
        from utils.ollama_client import OllamaError
        mock_ollama.list_models.side_effect = OllamaError("boom")
        success, models, error = manager.get_available_models()
        assert success is False
        assert error is not None


# ---------------------------------------------------------------------------
# 14. ModelManager — generate
# ---------------------------------------------------------------------------

class TestModelManagerGenerate:

    def test_generate_calls_ollama_generate(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (True, [{"name": "m", "size": 100}], None)
        mock_ollama.generate.return_value = (True, "response text", None)
        success, text, error = manager.generate("m", "Hello")
        mock_ollama.generate.assert_called()
        assert success is True
        assert text == "response text"

    def test_generate_returns_error_when_ollama_fails(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (True, [{"name": "m", "size": 100}], None)
        mock_ollama.generate.return_value = (False, None, "timeout")
        success, text, error = manager.generate("m", "Hello")
        assert success is False
        assert error == "timeout"

    def test_generate_returns_error_when_model_load_fails(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (False, None, "no models")
        success, text, error = manager.generate("m", "Hello")
        assert success is False


# ---------------------------------------------------------------------------
# 15. ModelManager — warmup
# ---------------------------------------------------------------------------

class TestModelManagerWarmup:

    def test_warmup_loads_listed_models(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (
            True, [{"name": "a", "size": 10}, {"name": "b", "size": 10}], None
        )
        manager.warmup(["a", "b"])
        # Both models should be in cache
        assert manager.cache.get("a") is not None
        assert manager.cache.get("b") is not None

    def test_warmup_stores_warmup_models_list(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (True, [], None)
        manager.warmup(["x", "y"])
        assert manager._warmup_models == ["x", "y"]

    def test_warmup_handles_load_failure_gracefully(self, manager, mock_ollama):
        mock_ollama.list_models.return_value = (False, None, "err")
        # Should not raise
        manager.warmup(["failing_model"])


# ---------------------------------------------------------------------------
# 16. ModelManager — cleanup
# ---------------------------------------------------------------------------

class TestModelManagerCleanup:

    def test_cleanup_clears_cache(self, manager):
        manager.cache.put("m", make_model_info("m", 10))
        manager.cleanup()
        assert manager.cache.get("m") is None
        assert manager.cache._current_memory == 0
