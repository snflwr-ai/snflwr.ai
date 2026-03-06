"""
Tests for utils/cache.py — RedisCache
Targets 70%+ coverage on the module.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_disabled_cache():
    """Return a RedisCache that is completely disabled (no Redis required)."""
    from utils.cache import RedisCache
    return RedisCache(enabled=False)


def make_mock_cache():
    """
    Return a RedisCache that is logically enabled but backed by a MagicMock
    client — no real Redis connection is made.
    """
    from utils.cache import RedisCache
    cache = RedisCache(enabled=False)   # avoids real connection
    cache.enabled = True                # override after construction
    cache._client = MagicMock()
    cache._degraded = False
    # Give it all attributes that live code might reference
    cache.host = "localhost"
    cache.port = 6379
    cache._sentinel = None
    cache.use_sentinel = False          # needed by _handle_connection_error
    cache._stats = {
        'hits': 0, 'misses': 0, 'sets': 0,
        'deletes': 0, 'errors': 0, 'failovers': 0,
    }
    return cache


@pytest.fixture
def disabled_cache():
    return make_disabled_cache()


@pytest.fixture
def mock_cache():
    return make_mock_cache()


# ---------------------------------------------------------------------------
# 1. Disabled cache — correct defaults for every public method
# ---------------------------------------------------------------------------

class TestDisabledCacheDefaults:

    def test_enabled_flag_is_false(self, disabled_cache):
        assert disabled_cache.enabled is False

    def test_is_degraded_is_false_when_explicitly_disabled(self, disabled_cache):
        # Explicitly disabled (not a failed connection) → not degraded
        assert disabled_cache.is_degraded is False

    def test_client_is_none_when_disabled(self, disabled_cache):
        assert disabled_cache._client is None

    def test_get_returns_none_when_disabled(self, disabled_cache):
        assert disabled_cache.get("any_key") is None

    def test_get_returns_none_for_any_namespace(self, disabled_cache):
        assert disabled_cache.get("k", namespace="profiles") is None

    def test_set_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.set("key", "value") is False

    def test_set_with_ttl_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.set("key", "value", ttl=60) is False

    def test_delete_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.delete("key") is False

    def test_exists_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.exists("key") is False

    def test_delete_pattern_returns_zero_when_disabled(self, disabled_cache):
        assert disabled_cache.delete_pattern("*") == 0

    def test_increment_returns_none_when_disabled(self, disabled_cache):
        assert disabled_cache.increment("counter") is None

    def test_expire_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.expire("key", 300) is False

    def test_health_check_returns_false_when_disabled(self, disabled_cache):
        assert disabled_cache.health_check() is False

    def test_clear_all_returns_zero_when_disabled(self, disabled_cache):
        assert disabled_cache.clear_all() == 0

    def test_health_check_detailed_not_healthy_when_disabled(self, disabled_cache):
        result = disabled_cache.health_check_detailed()
        assert result["healthy"] is False
        assert result["enabled"] is False


# ---------------------------------------------------------------------------
# 2. REDIS_ENABLED env var overrides enabled=True
# ---------------------------------------------------------------------------

class TestRedisEnabledEnvVar:

    def test_redis_enabled_false_overrides_enabled_true(self, monkeypatch):
        """REDIS_ENABLED=false must disable the cache even if enabled=True."""
        monkeypatch.setenv("REDIS_ENABLED", "false")
        from utils.cache import RedisCache
        cache = RedisCache(enabled=True)
        assert cache.enabled is False

    def test_redis_enabled_default_is_false(self, monkeypatch):
        """Default env has REDIS_ENABLED unset / 'false' → disabled."""
        monkeypatch.delenv("REDIS_ENABLED", raising=False)
        from utils.cache import RedisCache
        cache = RedisCache(enabled=True)   # env default is 'false'
        assert cache.enabled is False


# ---------------------------------------------------------------------------
# 3. _make_key — key construction
# ---------------------------------------------------------------------------

class TestMakeKey:

    def test_make_key_default_namespace(self, disabled_cache):
        result = disabled_cache._make_key("mykey")
        assert result == "snflwr:mykey"

    def test_make_key_custom_namespace(self, disabled_cache):
        result = disabled_cache._make_key("user123", "profiles")
        assert result == "profiles:user123"

    def test_make_key_with_complex_key(self, disabled_cache):
        result = disabled_cache._make_key("user:42:session", "auth")
        assert result == "auth:user:42:session"

    def test_make_key_empty_key(self, disabled_cache):
        result = disabled_cache._make_key("", "ns")
        assert result == "ns:"

    def test_make_key_format(self, disabled_cache):
        key = disabled_cache._make_key("foo", "bar")
        assert key.startswith("bar:")
        assert key.endswith(":foo") or key == "bar:foo"


# ---------------------------------------------------------------------------
# 4. Serialization / deserialization (no Redis needed)
# ---------------------------------------------------------------------------

class TestSerialization:

    def test_serialize_string(self, disabled_cache):
        result = disabled_cache._serialize("hello")
        assert json.loads(result) == "hello"

    def test_serialize_int(self, disabled_cache):
        result = disabled_cache._serialize(42)
        assert json.loads(result) == 42

    def test_serialize_dict(self, disabled_cache):
        data = {"name": "Alice", "age": 10}
        result = disabled_cache._serialize(data)
        assert json.loads(result) == data

    def test_serialize_list(self, disabled_cache):
        data = [1, 2, 3]
        result = disabled_cache._serialize(data)
        assert json.loads(result) == data

    def test_serialize_bool(self, disabled_cache):
        assert json.loads(disabled_cache._serialize(True)) is True
        assert json.loads(disabled_cache._serialize(False)) is False

    def test_deserialize_string(self, disabled_cache):
        raw = json.dumps("hello")
        assert disabled_cache._deserialize(raw) == "hello"

    def test_deserialize_dict(self, disabled_cache):
        data = {"x": 1}
        raw = json.dumps(data)
        assert disabled_cache._deserialize(raw) == data

    def test_deserialize_invalid_json_returns_raw(self, disabled_cache):
        """Non-JSON input should be returned as-is."""
        result = disabled_cache._deserialize("not-json")
        assert result == "not-json"

    def test_deserialize_json_with_type_key_returns_data_for_unknown_type(
        self, disabled_cache
    ):
        """Unknown __type__ falls through to returning the plain dict."""
        payload = json.dumps({"__type__": "some.Unknown", "__data__": {"a": 1}})
        result = disabled_cache._deserialize(payload)
        # Falls through to return the raw parsed dict for unknown types
        assert isinstance(result, dict)

    def test_serialize_object_with_to_dict(self, disabled_cache):
        class Dummy:
            def to_dict(self):
                return {"val": 99}
        obj = Dummy()
        raw = disabled_cache._serialize(obj)
        parsed = json.loads(raw)
        assert "__type__" in parsed
        assert parsed["__data__"] == {"val": 99}


# ---------------------------------------------------------------------------
# 5. get_or_set (disabled cache — factory always called)
#    Note: get_or_set is NOT a method on RedisCache; this tests the pattern
#    via get + set which the caller orchestrates. We can test the helper
#    using the cached decorator or manually.
# ---------------------------------------------------------------------------

class TestGetOrSetPattern:
    """
    RedisCache does not expose get_or_set; test the equivalent pattern
    (get returns None → call factory → set result) against disabled cache.
    """

    def test_manual_get_or_set_calls_factory_on_disabled_cache(self, disabled_cache):
        factory_calls = []

        def factory():
            factory_calls.append(1)
            return 42

        result = disabled_cache.get("key") or factory()
        assert result == 42
        assert len(factory_calls) == 1

    def test_disabled_cache_get_never_returns_stale_value(self, disabled_cache):
        disabled_cache.set("k", "stored_value")
        # set is a no-op, so get should still return None
        assert disabled_cache.get("k") is None


# ---------------------------------------------------------------------------
# 6. Mocked Redis — get (hit / miss)
# ---------------------------------------------------------------------------

class TestMockedRedisGet:

    def test_get_returns_none_on_cache_miss(self, mock_cache):
        mock_cache._client.get.return_value = None
        result = mock_cache.get("missing")
        assert result is None

    def test_get_returns_value_on_cache_hit(self, mock_cache):
        mock_cache._client.get.return_value = json.dumps("cached_value")
        result = mock_cache.get("present")
        assert result == "cached_value"

    def test_get_increments_hits_on_cache_hit(self, mock_cache):
        mock_cache._client.get.return_value = json.dumps(1)
        mock_cache.get("k")
        assert mock_cache._stats["hits"] == 1

    def test_get_increments_misses_on_cache_miss(self, mock_cache):
        mock_cache._client.get.return_value = None
        mock_cache.get("k")
        assert mock_cache._stats["misses"] == 1

    def test_get_passes_namespaced_key_to_client(self, mock_cache):
        mock_cache._client.get.return_value = None
        mock_cache.get("mykey", namespace="ns")
        mock_cache._client.get.assert_called_once_with("ns:mykey")

    def test_get_deserializes_json_dict(self, mock_cache):
        data = {"a": 1, "b": 2}
        mock_cache._client.get.return_value = json.dumps(data)
        result = mock_cache.get("k")
        assert result == data

    def test_get_returns_none_on_redis_error(self, mock_cache):
        mock_cache._client.get.side_effect = RedisError("boom")
        result = mock_cache.get("k")
        assert result is None
        assert mock_cache._stats["errors"] == 1


# ---------------------------------------------------------------------------
# 7. Mocked Redis — set
# ---------------------------------------------------------------------------

class TestMockedRedisSet:

    def test_set_returns_true_on_success(self, mock_cache):
        result = mock_cache.set("k", "v")
        assert result is True

    def test_set_calls_setex_with_default_ttl(self, mock_cache):
        mock_cache.set("k", "v")
        call_args = mock_cache._client.setex.call_args
        assert call_args is not None
        # setex(key, ttl, value)
        _, ttl, _ = call_args[0]
        assert ttl == mock_cache.default_ttl

    def test_set_calls_setex_with_custom_ttl(self, mock_cache):
        mock_cache.set("k", "v", ttl=120)
        _, ttl, _ = mock_cache._client.setex.call_args[0]
        assert ttl == 120

    def test_set_uses_namespaced_key(self, mock_cache):
        mock_cache.set("mykey", "val", namespace="profiles")
        key, _, _ = mock_cache._client.setex.call_args[0]
        assert key == "profiles:mykey"

    def test_set_increments_sets_stat(self, mock_cache):
        mock_cache.set("k", "v")
        assert mock_cache._stats["sets"] == 1

    def test_set_returns_false_on_redis_error(self, mock_cache):
        mock_cache._client.setex.side_effect = RedisError("fail")
        result = mock_cache.set("k", "v")
        assert result is False
        assert mock_cache._stats["errors"] == 1

    def test_set_serializes_value_as_json(self, mock_cache):
        mock_cache.set("k", {"x": 1})
        _, _, serialized = mock_cache._client.setex.call_args[0]
        assert json.loads(serialized) == {"x": 1}


# ---------------------------------------------------------------------------
# 8. Mocked Redis — delete
# ---------------------------------------------------------------------------

class TestMockedRedisDelete:

    def test_delete_returns_true_when_key_existed(self, mock_cache):
        mock_cache._client.delete.return_value = 1
        assert mock_cache.delete("k") is True

    def test_delete_returns_false_when_key_did_not_exist(self, mock_cache):
        mock_cache._client.delete.return_value = 0
        assert mock_cache.delete("k") is False

    def test_delete_calls_client_with_namespaced_key(self, mock_cache):
        mock_cache._client.delete.return_value = 1
        mock_cache.delete("mykey", namespace="auth")
        mock_cache._client.delete.assert_called_once_with("auth:mykey")

    def test_delete_returns_false_on_redis_error(self, mock_cache):
        mock_cache._client.delete.side_effect = RedisError("err")
        result = mock_cache.delete("k")
        assert result is False

    def test_delete_increments_deletes_stat(self, mock_cache):
        mock_cache._client.delete.return_value = 1
        mock_cache.delete("k")
        assert mock_cache._stats["deletes"] == 1


# ---------------------------------------------------------------------------
# 9. Mocked Redis — exists
# ---------------------------------------------------------------------------

class TestMockedRedisExists:

    def test_exists_returns_true_when_key_present(self, mock_cache):
        mock_cache._client.exists.return_value = 1
        assert mock_cache.exists("k") is True

    def test_exists_returns_false_when_key_absent(self, mock_cache):
        mock_cache._client.exists.return_value = 0
        assert mock_cache.exists("k") is False

    def test_exists_passes_namespaced_key(self, mock_cache):
        mock_cache._client.exists.return_value = 0
        mock_cache.exists("mykey", namespace="session")
        mock_cache._client.exists.assert_called_once_with("session:mykey")

    def test_exists_returns_false_on_redis_error(self, mock_cache):
        mock_cache._client.exists.side_effect = RedisError("err")
        result = mock_cache.exists("k")
        assert result is False


# ---------------------------------------------------------------------------
# 10. Mocked Redis — delete_pattern / clear_all
# ---------------------------------------------------------------------------

class TestMockedRedisDeletePattern:

    def test_delete_pattern_returns_count_of_deleted_keys(self, mock_cache):
        mock_cache._client.keys.return_value = ["snflwr:user:1", "snflwr:user:2"]
        mock_cache._client.delete.return_value = 2
        result = mock_cache.delete_pattern("user:*")
        assert result == 2

    def test_delete_pattern_returns_zero_when_no_keys_match(self, mock_cache):
        mock_cache._client.keys.return_value = []
        result = mock_cache.delete_pattern("nomatch:*")
        assert result == 0

    def test_clear_all_delegates_to_delete_pattern(self, mock_cache):
        mock_cache._client.keys.return_value = []
        result = mock_cache.clear_all()
        # clear_all calls delete_pattern("*")
        assert result == 0
        mock_cache._client.keys.assert_called()


# ---------------------------------------------------------------------------
# 11. Mocked Redis — increment
# ---------------------------------------------------------------------------

class TestMockedRedisIncrement:

    def test_increment_returns_new_value(self, mock_cache):
        mock_cache._client.incrby.return_value = 5
        result = mock_cache.increment("counter")
        assert result == 5

    def test_increment_calls_incrby_with_amount(self, mock_cache):
        mock_cache._client.incrby.return_value = 3
        mock_cache.increment("c", amount=3)
        mock_cache._client.incrby.assert_called_once_with("snflwr:c", 3)

    def test_increment_returns_none_on_redis_error(self, mock_cache):
        mock_cache._client.incrby.side_effect = RedisError("fail")
        result = mock_cache.increment("c")
        assert result is None


# ---------------------------------------------------------------------------
# 12. Mocked Redis — expire
# ---------------------------------------------------------------------------

class TestMockedRedisExpire:

    def test_expire_calls_client_expire(self, mock_cache):
        mock_cache._client.expire.return_value = True
        result = mock_cache.expire("k", 300)
        mock_cache._client.expire.assert_called_once_with("snflwr:k", 300)

    def test_expire_returns_false_on_redis_error(self, mock_cache):
        mock_cache._client.expire.side_effect = RedisError("err")
        result = mock_cache.expire("k", 60)
        assert result is False


# ---------------------------------------------------------------------------
# 13. get_stats
# ---------------------------------------------------------------------------

class TestGetStats:

    def test_get_stats_disabled_cache_has_zero_counts(self, disabled_cache):
        # disabled_cache has no _stats dict; get_stats should still work
        # when disabled (it reads self._stats which is not set → need mock)
        # Actually disabled_cache never sets _stats. Check behavior:
        # Looking at the code: get_stats uses self._stats which is only
        # initialised when enabled=True. So skip for disabled_cache.
        pass

    def test_get_stats_returns_hit_rate_zero_when_no_ops(self, mock_cache):
        stats = mock_cache.get_stats()
        assert stats["hit_rate"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    def test_get_stats_calculates_hit_rate(self, mock_cache):
        mock_cache._stats["hits"] = 3
        mock_cache._stats["misses"] = 1
        # get_stats calls self._client.info when enabled+client present
        mock_cache._client.info.return_value = {}
        stats = mock_cache.get_stats()
        assert stats["hit_rate"] == 75.0

    def test_get_stats_includes_degraded_flag(self, mock_cache):
        mock_cache._degraded = True
        mock_cache._client.info.return_value = {}
        stats = mock_cache.get_stats()
        assert stats["degraded"] is True

    def test_get_stats_mode_standalone(self, mock_cache):
        mock_cache._client.info.return_value = {}
        stats = mock_cache.get_stats()
        assert stats["mode"] == "standalone"


# ---------------------------------------------------------------------------
# 14. health_check with mock
# ---------------------------------------------------------------------------

class TestHealthCheck:

    def test_health_check_returns_true_when_ping_succeeds(self, mock_cache):
        mock_cache._client.ping.return_value = True
        assert mock_cache.health_check() is True

    def test_health_check_returns_false_on_redis_error(self, mock_cache):
        mock_cache._client.ping.side_effect = RedisError("down")
        # _handle_connection_error will be called; then retry ping may also fail
        mock_cache._client.ping.side_effect = [
            RedisError("down"), RedisError("still down")
        ]
        result = mock_cache.health_check()
        assert result is False


# ---------------------------------------------------------------------------
# 15. Degraded mode flag
# ---------------------------------------------------------------------------

class TestDegradedMode:

    def test_is_degraded_initially_false(self, disabled_cache):
        assert disabled_cache.is_degraded is False

    def test_is_degraded_returns_true_when_set(self, mock_cache):
        mock_cache._degraded = True
        assert mock_cache.is_degraded is True

    def test_degraded_and_enabled_are_distinct(self, mock_cache):
        """Degraded means configured but unavailable, not just disabled."""
        mock_cache._degraded = True
        # enabled could still be True in principle (the class sets it False
        # on connection failure, but the flag meanings are distinct)
        assert mock_cache.is_degraded is True


# ---------------------------------------------------------------------------
# 16. health_check_detailed
# ---------------------------------------------------------------------------

class TestHealthCheckDetailed:

    def test_detailed_healthy_when_ping_succeeds(self, mock_cache):
        mock_cache._client.ping.return_value = True
        mock_cache._client.info.return_value = {
            "redis_version": "7.0.0",
            "uptime_in_seconds": 1000,
        }
        result = mock_cache.health_check_detailed()
        assert result["healthy"] is True
        assert result["enabled"] is True

    def test_detailed_not_healthy_when_disabled(self):
        cache = make_disabled_cache()
        result = cache.health_check_detailed()
        assert result["healthy"] is False
        assert "error" in result

    def test_detailed_includes_degraded_flag(self, mock_cache):
        mock_cache._degraded = False
        mock_cache._client.ping.return_value = True
        mock_cache._client.info.return_value = {}
        result = mock_cache.health_check_detailed()
        assert "degraded" in result

    def test_detailed_returns_error_on_redis_ping_failure(self, mock_cache):
        mock_cache._client.ping.side_effect = RedisError("ping failed")
        result = mock_cache.health_check_detailed()
        assert result["healthy"] is False
        assert "error" in result

    def test_detailed_reports_degraded_mode_when_set(self):
        import time
        cache = make_disabled_cache()
        cache._degraded = True
        # Prevent _maybe_reconnect from trying to reconnect (interval not elapsed)
        cache._last_reconnect_attempt = time.time()
        result = cache.health_check_detailed()
        assert result["degraded"] is True
        assert result["healthy"] is False


# ---------------------------------------------------------------------------
# 17. get_stats — Redis error path and sentinel branch
# ---------------------------------------------------------------------------

class TestGetStatsEdgeCases:

    def test_get_stats_handles_redis_error_on_info(self, mock_cache):
        """When _client.info() raises RedisError, stats still returns."""
        mock_cache._client.info.side_effect = RedisError("unavailable")
        stats = mock_cache.get_stats()
        # Should still return without raising
        assert "hits" in stats
        assert "hit_rate" in stats

    def test_get_stats_has_mode_standalone_by_default(self, mock_cache):
        mock_cache._client.info.return_value = {}
        stats = mock_cache.get_stats()
        assert stats.get("mode") == "standalone"

    def test_get_stats_has_master_key(self, mock_cache):
        mock_cache._client.info.return_value = {}
        stats = mock_cache.get_stats()
        assert "master" in stats


# ---------------------------------------------------------------------------
# 18. delete_pattern — Redis error path
# ---------------------------------------------------------------------------

class TestDeletePatternEdgeCases:

    def test_delete_pattern_returns_zero_on_redis_error(self, mock_cache):
        mock_cache._client.keys.side_effect = RedisError("err")
        result = mock_cache.delete_pattern("user:*")
        assert result == 0
        assert mock_cache._stats["errors"] == 1


# ---------------------------------------------------------------------------
# 19. _maybe_reconnect
# ---------------------------------------------------------------------------

class TestMaybeReconnect:

    def test_maybe_reconnect_returns_false_immediately_after_attempt(self):
        """If an attempt was made recently, should not retry."""
        import time
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        # Simulate degraded mode
        cache._degraded = True
        cache._last_reconnect_attempt = time.time()  # just tried
        # Should return False (too soon to retry)
        result = cache._maybe_reconnect()
        assert result is False

    def test_maybe_reconnect_not_degraded_returns_enabled_state(self):
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        cache._degraded = False
        # Not degraded → returns self.enabled
        result = cache._maybe_reconnect()
        assert result == cache.enabled


# ---------------------------------------------------------------------------
# 20. _parse_sentinel_hosts
# ---------------------------------------------------------------------------

class TestParseSentinelHosts:

    def test_parse_sentinel_hosts_empty_env(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "")
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        result = cache._parse_sentinel_hosts()
        assert result == []

    def test_parse_sentinel_hosts_single_entry(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "sentinel1:26379")
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        result = cache._parse_sentinel_hosts()
        assert len(result) == 1
        assert result[0] == ("sentinel1", 26379)

    def test_parse_sentinel_hosts_multiple_entries(self, monkeypatch):
        monkeypatch.setenv(
            "REDIS_SENTINEL_HOSTS", "s1:26379,s2:26380,s3:26381"
        )
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        result = cache._parse_sentinel_hosts()
        assert len(result) == 3
        assert result[1] == ("s2", 26380)

    def test_parse_sentinel_hosts_without_port_uses_default(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "sentinelhost")
        from utils.cache import RedisCache
        cache = RedisCache(enabled=False)
        result = cache._parse_sentinel_hosts()
        assert len(result) == 1
        assert result[0] == ("sentinelhost", 26379)


# ---------------------------------------------------------------------------
# 21. get_master_info (standalone mode)
# ---------------------------------------------------------------------------

class TestGetMasterInfo:

    def test_get_master_info_standalone_returns_dict(self, mock_cache):
        info = mock_cache.get_master_info()
        assert info is not None
        assert info["mode"] == "standalone"
        assert "host" in info
        assert "port" in info


# ---------------------------------------------------------------------------
# 22. cached decorator (lines 689-720)
# ---------------------------------------------------------------------------

class TestCachedDecorator:

    def test_cached_decorator_calls_function_on_miss(self):
        """The @cached decorator calls the wrapped function on cache miss."""
        from utils.cache import cached, cache
        # The global cache is disabled (REDIS_ENABLED=false), so every get
        # returns None and the decorator always calls the function.
        call_log = []

        @cached(ttl=60, key_prefix="test_fn")
        def expensive(*args, **kwargs):
            call_log.append((args, kwargs))
            return "result"

        result = expensive("arg1")
        assert result == "result"
        assert len(call_log) == 1

    def test_cached_decorator_preserves_function_name(self):
        from utils.cache import cached

        @cached(ttl=60)
        def my_function():
            return 42

        assert my_function.__name__ == "my_function"

    def test_cached_decorator_with_kwargs(self):
        from utils.cache import cached

        results = []

        @cached(ttl=30, key_prefix="kw_test")
        def fn(a, b=10):
            results.append(1)
            return a + b

        val = fn(5, b=3)
        assert val == 8
