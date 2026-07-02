"""
Tests for rate limiter with in-memory fallback.
"""

import time
import pytest
from unittest.mock import MagicMock, patch


class TestLocalRateLimiter:
    """Test the in-memory fallback rate limiter."""

    def test_allows_within_limit(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        allowed, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert allowed is True
        assert info['remaining'] == 4

    def test_blocks_when_limit_exceeded(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert allowed is False
        assert info['remaining'] == 0

    def test_different_identifiers_independent(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, _ = limiter.check_rate_limit("user2", 5, 60, "api")
        assert allowed is True

    def test_different_limit_types_independent(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, _ = limiter.check_rate_limit("user1", 5, 60, "auth")
        assert allowed is True

    def test_window_expires(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        # Use 1-second window
        for _ in range(3):
            limiter.check_rate_limit("user1", 3, 1, "api")
        allowed, _ = limiter.check_rate_limit("user1", 3, 1, "api")
        assert allowed is False
        time.sleep(1.1)
        allowed, _ = limiter.check_rate_limit("user1", 3, 1, "api")
        assert allowed is True

    def test_returns_retry_after(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        _, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert info['retry_after'] > 0

    def test_info_dict_keys(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        _, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert 'remaining' in info
        assert 'retry_after' in info
        assert 'reset_time' in info


class TestRateLimiterFallback:
    """Test that RateLimiter falls back to LocalRateLimiter when Redis is down."""

    def test_production_non_critical_uses_local_fallback(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", True):
            allowed, info = limiter.check_rate_limit(
                "user1", 100, 60, limit_type="chat"
            )

        # Should be allowed (first request) but via local limiter, not bypass
        assert allowed is True
        assert 'remaining' in info
        # Should NOT have the old bypass warning
        assert info.get('warning') != 'Rate limiting disabled - Redis not available'

    def test_production_critical_still_fails_closed(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", True):
            allowed, info = limiter.check_rate_limit(
                "user1", 10, 60, limit_type="auth"
            )

        # Auth should still fail closed
        assert allowed is False

    def test_development_uses_local_fallback(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", False):
            allowed, info = limiter.check_rate_limit(
                "user1", 100, 60, limit_type="api"
            )

        assert allowed is True
        assert 'remaining' in info


class TestFailClosedOnRedisError:
    """On a Redis error, non-critical endpoints must degrade to the in-memory
    limiter (still enforce a limit) — never allow unlimited. Critical endpoints
    still hard-deny."""

    class _BoomClient:
        def pipeline(self):
            raise ConnectionError("redis down")

    class _BoomCache:
        def __init__(self):
            self.enabled = True
            self._client = TestFailClosedOnRedisError._BoomClient()

    def test_noncritical_error_degrades_to_inmemory_not_open(self):
        from utils.rate_limiter import RateLimiter
        rl = RateLimiter(redis_cache=self._BoomCache())
        ident = "fail-closed-noncrit-unique-1"
        a1, _ = rl.check_rate_limit(ident, 2, 60, limit_type="api")
        a2, _ = rl.check_rate_limit(ident, 2, 60, limit_type="api")
        a3, _ = rl.check_rate_limit(ident, 2, 60, limit_type="api")
        assert a1 is True and a2 is True
        assert a3 is False  # would be True (unlimited) under the old fail-open

    def test_critical_error_still_denies(self):
        from utils.rate_limiter import RateLimiter
        rl = RateLimiter(redis_cache=self._BoomCache())
        allowed, _ = rl.check_rate_limit("crit-unique-1", 5, 60, limit_type="auth")
        assert allowed is False
