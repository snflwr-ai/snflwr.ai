"""
Test Suite for Enterprise Features
Tests Redis cache, rate limiter, and metrics integrations for horizontal scaling
"""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRedisCacheBasics:
    """Test Redis cache basic operations"""

    def test_cache_disabled_returns_gracefully(self):
        """Test cache operations return gracefully when disabled"""
        with patch.dict(os.environ, {'REDIS_ENABLED': 'false'}):
            # Force reimport to pick up new env
            import importlib
            import utils.cache
            importlib.reload(utils.cache)

            from utils.cache import RedisCache
            cache = RedisCache(enabled=False)

            assert cache.get("any_key") is None
            assert cache.set("any_key", "value") is False
            assert cache.delete("any_key") is False

    def test_cache_make_key(self):
        """Test cache key generation"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = True

        key = cache._make_key("user:123", "snflwr")
        assert key == "snflwr:user:123"

    def test_cache_make_key_custom_namespace(self):
        """Test cache key with custom namespace"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = True

        key = cache._make_key("session:abc", "auth")
        assert key == "auth:session:abc"


class TestRedisCacheSerialization:
    """Test Redis cache serialization/deserialization"""

    def test_serialize_string(self):
        """Test serialization of string"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        result = cache._serialize("hello")
        assert result == '"hello"'

    def test_serialize_dict(self):
        """Test serialization of dict"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        result = cache._serialize({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_serialize_list(self):
        """Test serialization of list"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        result = cache._serialize([1, 2, 3])
        assert result == '[1, 2, 3]'

    def test_deserialize_json(self):
        """Test deserialization of JSON"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        result = cache._deserialize('{"name": "test", "value": 123}')
        assert result == {"name": "test", "value": 123}

    def test_deserialize_string(self):
        """Test deserialization of string"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        result = cache._deserialize('"hello world"')
        assert result == "hello world"


class TestRedisCacheStats:
    """Test Redis cache statistics"""

    def test_hit_rate_calculation(self):
        """Test hit rate is calculated correctly"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = True
        cache._client = None
        cache._sentinel = None
        cache._degraded = False
        cache.host = 'localhost'
        cache.port = 6379
        cache._stats = {
            'hits': 80,
            'misses': 20,
            'sets': 100,
            'deletes': 10,
            'errors': 0,
            'failovers': 0
        }

        stats = cache.get_stats()

        assert stats['hit_rate'] == 80.0  # 80%

    def test_stats_zero_requests(self):
        """Test hit rate with zero requests"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = True
        cache._client = None
        cache._sentinel = None
        cache._degraded = False
        cache.host = 'localhost'
        cache.port = 6379
        cache._stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'deletes': 0,
            'errors': 0,
            'failovers': 0
        }

        stats = cache.get_stats()

        assert stats['hit_rate'] == 0


class TestRedisCacheSentinel:
    """Test Redis Sentinel support"""

    def test_parse_sentinel_hosts(self):
        """Test parsing sentinel hosts from environment"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        with patch.dict(os.environ, {
            'REDIS_SENTINEL_HOSTS': 'host1:26379,host2:26379,host3:26379'
        }):
            hosts = cache._parse_sentinel_hosts()

            assert len(hosts) == 3
            assert hosts[0] == ('host1', 26379)
            assert hosts[1] == ('host2', 26379)
            assert hosts[2] == ('host3', 26379)

    def test_parse_sentinel_hosts_empty(self):
        """Test parsing empty sentinel hosts"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)

        with patch.dict(os.environ, {'REDIS_SENTINEL_HOSTS': ''}, clear=False):
            hosts = cache._parse_sentinel_hosts()
            assert hosts == []

    def test_get_master_info_standalone(self):
        """Test master info returns standalone mode when not using sentinel"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._sentinel = None
        cache.host = 'localhost'
        cache.port = 6379

        info = cache.get_master_info()

        assert info['mode'] == 'standalone'
        assert info['host'] == 'localhost'
        assert info['port'] == 6379


@pytest.mark.skipif(
    not all(pytest.importorskip(mod, reason=f"{mod} not available") for mod in ["argon2"]),
    reason="Rate limiter tests require full application dependencies"
)
class TestRedisRateLimiter:
    """Test Redis-backed rate limiter"""

    def test_rate_limiter_limits_config(self):
        """Test rate limiter has expected limits configured"""
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("argon2", reason="argon2 not installed")
        from api.middleware.auth import RedisRateLimiter
        limiter = RedisRateLimiter()

        assert 'default' in limiter.limits
        assert 'auth' in limiter.limits
        assert 'api' in limiter.limits

        # Auth should be stricter than API
        auth_limit, _ = limiter.limits['auth']
        api_limit, _ = limiter.limits['api']
        assert auth_limit < api_limit


class TestRateLimiterFallback:
    """Test rate limiter in-memory fallback"""

    def test_fallback_rate_limiting(self):
        """Test in-memory fallback rate limiting works"""
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("argon2", reason="argon2 not installed")
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter()
        limiter._redis = None  # Force fallback mode

        # Use custom low limit for testing
        limiter.limits['test'] = (3, 60)

        # First 3 should succeed
        assert limiter.check_rate_limit("user1", "test") is True
        assert limiter.check_rate_limit("user1", "test") is True
        assert limiter.check_rate_limit("user1", "test") is True

        # 4th should fail
        assert limiter.check_rate_limit("user1", "test") is False

    def test_fallback_different_users(self):
        """Test fallback tracks users separately"""
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("argon2", reason="argon2 not installed")
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter()
        limiter._redis = None
        limiter.limits['test2'] = (2, 60)

        # User 1 hits limit
        limiter.check_rate_limit("user1", "test2")
        limiter.check_rate_limit("user1", "test2")
        assert limiter.check_rate_limit("user1", "test2") is False

        # User 2 should still be allowed
        assert limiter.check_rate_limit("user2", "test2") is True

    def test_get_remaining_fallback(self):
        """Test get_remaining works in fallback mode"""
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("argon2", reason="argon2 not installed")
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter()
        limiter._redis = None
        limiter.limits['test3'] = (10, 60)

        # Use 3 requests
        limiter.check_rate_limit("user1", "test3")
        limiter.check_rate_limit("user1", "test3")
        limiter.check_rate_limit("user1", "test3")

        remaining = limiter.get_remaining("user1", "test3")
        assert remaining == 7

    def test_reset_fallback(self):
        """Test reset works in fallback mode"""
        pytest.importorskip("fastapi", reason="fastapi not installed")
        pytest.importorskip("argon2", reason="argon2 not installed")
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter()
        limiter._redis = None
        limiter.limits['test4'] = (2, 60)

        # Hit limit
        limiter.check_rate_limit("user1", "test4")
        limiter.check_rate_limit("user1", "test4")
        assert limiter.check_rate_limit("user1", "test4") is False

        # Reset
        limiter.reset("user1", "test4")

        # Should be allowed again
        assert limiter.check_rate_limit("user1", "test4") is True


class TestCachedDecorator:
    """Test the @cached decorator"""

    def test_cached_decorator_uses_cache(self):
        """Test decorator attempts to use cache"""
        from utils.cache import cached

        call_count = 0

        @cached(ttl=60, key_prefix="test_cached")
        def my_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        # Call the function - should execute
        result = my_function(5)
        assert result == 10
        assert call_count == 1


class TestCacheHealthCheck:
    """Test cache health check functionality"""

    def test_health_check_disabled_cache(self):
        """Test health check returns False when cache is disabled"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = False
        cache._client = None
        cache._degraded = False
        cache._last_reconnect_attempt = 0.0

        assert cache.health_check() is False

    def test_health_check_detailed_disabled(self):
        """Test detailed health check when disabled"""
        from utils.cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache.enabled = False
        cache._client = None
        cache._sentinel = None
        cache._degraded = False
        cache._last_reconnect_attempt = 0.0

        result = cache.health_check_detailed()

        assert result['healthy'] is False
        assert result['enabled'] is False
