"""
Distributed Rate Limiter using Redis
Token bucket and sliding window rate limiting with Redis backend

IMPORTANT: Redis is REQUIRED for production deployments.
Rate limiting is critical for security (preventing brute force attacks)
and must work correctly in multi-instance deployments.
"""

import os
import time
from typing import Tuple, Optional
from datetime import datetime, timedelta

from utils.cache import cache
from utils.logger import get_logger

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError

logger = get_logger(__name__)

# Check if we're in production
_ENVIRONMENT = os.getenv('ENVIRONMENT', 'development').lower()
_IS_PRODUCTION = _ENVIRONMENT in ('production', 'prod', 'staging')


class LocalRateLimiter:
    """
    In-memory sliding window rate limiter.

    Fallback for when Redis is unavailable. NOT distributed — each process
    instance has its own counters. Provides degraded-mode protection rather
    than no protection at all.
    """

    def __init__(self):
        self._windows: dict = {}  # {window_key: [timestamp, ...]}
        self._lock = __import__('threading').Lock()
        self._warned: set = set()  # Track which keys we've warned about

    def check_rate_limit(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int,
        limit_type: str = "api",
    ) -> Tuple[bool, dict]:
        current_time = time.time()
        window_key = f"{limit_type}:{identifier}"

        with self._lock:
            # Lazy cleanup: remove expired entries
            if window_key in self._windows:
                cutoff = current_time - window_seconds
                self._windows[window_key] = [
                    t for t in self._windows[window_key] if t > cutoff
                ]
            else:
                self._windows[window_key] = []

            request_count = len(self._windows[window_key])
            allowed = request_count < max_requests

            if allowed:
                self._windows[window_key].append(current_time)

            remaining = max(0, max_requests - request_count - (1 if allowed else 0))

            # Calculate retry_after
            if not allowed and self._windows[window_key]:
                oldest = min(self._windows[window_key])
                retry_after = max(0, int((oldest + window_seconds) - current_time))
                reset_time = oldest + window_seconds
            else:
                retry_after = 0
                reset_time = current_time + window_seconds

            # Periodic cleanup of stale keys
            if len(self._windows) > 1000:
                self._cleanup_stale(current_time, window_seconds)

        # Warn once per limit_type and record metric
        if limit_type not in self._warned:
            self._warned.add(limit_type)
            if _IS_PRODUCTION:
                logger.critical(
                    f"DEGRADED: In-memory rate limiting active for '{limit_type}' "
                    f"(Redis unavailable). Rate limits are per-process only — "
                    f"multi-worker deployments have reduced protection."
                )
            else:
                logger.warning(
                    f"Using in-memory rate limiting for '{limit_type}' (Redis unavailable)"
                )
            try:
                from utils.metrics import rate_limiter_requests_total
                rate_limiter_requests_total.labels(
                    result='fallback_activated'
                ).inc()
            except (ImportError, Exception):
                pass

        return allowed, {
            'remaining': remaining,
            'reset_time': datetime.fromtimestamp(reset_time).isoformat(),
            'retry_after': retry_after,
            'limit': max_requests,
            'window': window_seconds,
            'backend': 'local',
        }

    def _cleanup_stale(self, current_time: float, default_window: int = 120):
        """Remove window keys with no recent entries."""
        cutoff = current_time - (default_window * 2)
        stale = [k for k, v in self._windows.items() if not v or max(v) < cutoff]
        for k in stale:
            del self._windows[k]


# Module-level local fallback instance
_local_limiter = LocalRateLimiter()


class RateLimiter:
    """
    Distributed rate limiter using Redis
    Implements sliding window algorithm for accurate rate limiting
    """

    def __init__(self, redis_cache=None):
        """
        Initialize rate limiter

        Args:
            redis_cache: Redis cache instance (uses global if not provided)
        """
        self.cache = redis_cache or cache
        self.namespace = "rate_limit"

    def _get_window_key(self, identifier: str, limit_type: str) -> str:
        """Generate Redis key for rate limit window"""
        return f"{limit_type}:{identifier}"

    def check_rate_limit(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int,
        limit_type: str = "api",
        fail_closed: bool = None
    ) -> Tuple[bool, dict]:
        """
        Check if request is within rate limit using sliding window

        Args:
            identifier: Unique identifier (user_id, IP, etc.)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
            limit_type: Type of limit (api, auth, etc.)
            fail_closed: If True, deny requests on errors. If None, auto-detect based on limit_type.
                        Critical endpoints (auth, password_reset) default to fail-closed.

        Returns:
            Tuple of (allowed: bool, info: dict)
            info contains: remaining, reset_time, retry_after
        """
        # Define critical endpoints that require rate limiting for security
        critical_endpoints = {'auth', 'login', 'password_reset', 'register', 'admin', 'api_key'}

        # Auto-detect fail-closed for critical endpoints if not explicitly set
        if fail_closed is None:
            fail_closed = limit_type in critical_endpoints

        is_critical = limit_type in critical_endpoints

        if not self.cache.enabled:
            # Redis not available - rate limiting is compromised

            if _IS_PRODUCTION and is_critical:
                # CRITICAL: In production, rate limiting on auth endpoints is mandatory
                # This prevents brute force attacks on login, registration, password reset
                logger.critical(
                    f"SECURITY ALERT: Rate limiting unavailable for critical endpoint '{limit_type}'. "
                    f"Redis is REQUIRED in production for rate limiting. "
                    f"Denying request to prevent potential brute force attacks."
                )
                return False, {
                    'remaining': 0,
                    'reset_time': None,
                    'retry_after': 60,
                    'error': 'Rate limiting service unavailable - request denied for security'
                }

            # Fall back to in-memory rate limiting (non-distributed but still protective)
            return _local_limiter.check_rate_limit(identifier, max_requests, window_seconds, limit_type)

        current_time = time.time()
        window_key = self._get_window_key(identifier, limit_type)

        try:
            # Get current request count
            pipe = self.cache._client.pipeline()

            # Remove old requests outside window
            window_start = current_time - window_seconds
            pipe.zremrangebyscore(window_key, '-inf', window_start)

            # Count requests in current window
            pipe.zcard(window_key)

            # Add current request with timestamp as score
            pipe.zadd(window_key, {str(current_time): current_time})

            # Set expiration on the key
            pipe.expire(window_key, window_seconds + 10)

            # Execute pipeline
            results = pipe.execute()
            request_count = results[1]  # Count before adding current request

            # Check if limit exceeded
            allowed = request_count < max_requests
            remaining = max(0, max_requests - request_count - 1)

            # Calculate reset time
            if request_count > 0:
                # Get oldest request in window
                oldest_requests = self.cache._client.zrange(
                    window_key,
                    0, 0, withscores=True
                )
                if oldest_requests:
                    oldest_time = oldest_requests[0][1]
                    reset_time = oldest_time + window_seconds
                    retry_after = max(0, int(reset_time - current_time))
                else:
                    reset_time = current_time + window_seconds
                    retry_after = window_seconds
            else:
                reset_time = current_time + window_seconds
                retry_after = 0 if allowed else window_seconds

            info = {
                'remaining': remaining,
                'reset_time': datetime.fromtimestamp(reset_time).isoformat(),
                'retry_after': retry_after,
                'limit': max_requests,
                'window': window_seconds
            }

            if not allowed:
                logger.warning(
                    f"Rate limit exceeded for {identifier} ({limit_type}): "
                    f"{request_count}/{max_requests} requests in {window_seconds}s"
                )

            return allowed, info

        except (RedisError, ConnectionError, OSError) as e:
            logger.error(f"Rate limiter error for {identifier} ({limit_type}): {e}")

            # Fail-closed for critical endpoints (deny on error)
            # Fail-open for general API (allow on error for better UX)
            if fail_closed:
                logger.warning(
                    f"Rate limiter failing CLOSED for critical endpoint {limit_type}. "
                    f"Denying request due to error: {e}"
                )
                return False, {
                    'remaining': 0,
                    'reset_time': None,
                    'retry_after': window_seconds,
                    'error': 'Rate limiting temporarily unavailable'
                }
            else:
                logger.info(
                    f"Rate limiter failing OPEN for non-critical endpoint {limit_type}. "
                    f"Allowing request despite error: {e}"
                )
                return True, {
                    'remaining': max_requests,
                    'reset_time': None,
                    'retry_after': 0,
                    'error': str(e)
                }

    def reset_limit(self, identifier: str, limit_type: str = "api") -> bool:
        """
        Reset rate limit for identifier

        Args:
            identifier: Unique identifier
            limit_type: Type of limit

        Returns:
            True if reset successful
        """
        window_key = self._get_window_key(identifier, limit_type)
        return self.cache.delete(window_key, self.namespace)

    def get_current_usage(
        self,
        identifier: str,
        limit_type: str = "api",
        window_seconds: int = 60
    ) -> dict:
        """
        Get current rate limit usage

        Args:
            identifier: Unique identifier
            limit_type: Type of limit
            window_seconds: Time window in seconds

        Returns:
            Dictionary with usage statistics
        """
        if not self.cache.enabled:
            return {'requests': 0, 'window_start': None}

        window_key = self._get_window_key(identifier, limit_type)
        current_time = time.time()
        window_start = current_time - window_seconds

        try:
            # Remove old requests
            self.cache._client.zremrangebyscore(
                window_key,
                '-inf',
                window_start
            )

            # Count requests
            request_count = self.cache._client.zcard(
                window_key
            )

            return {
                'requests': request_count,
                'window_start': datetime.fromtimestamp(window_start).isoformat(),
                'window_end': datetime.fromtimestamp(current_time).isoformat()
            }

        except (RedisError, ConnectionError, OSError) as e:
            logger.error(f"Error getting rate limit usage: {e}")
            return {'requests': 0, 'error': str(e)}


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter for burst handling
    Allows bursts up to bucket capacity while maintaining average rate
    """

    def __init__(self, redis_cache=None):
        """Initialize token bucket rate limiter"""
        self.cache = redis_cache or cache
        self.namespace = "token_bucket"

    def check_rate_limit(
        self,
        identifier: str,
        capacity: int,
        refill_rate: float,
        tokens_needed: int = 1
    ) -> Tuple[bool, dict]:
        """
        Check if request is allowed using token bucket algorithm

        Args:
            identifier: Unique identifier
            capacity: Maximum tokens in bucket
            refill_rate: Tokens added per second
            tokens_needed: Tokens needed for this request

        Returns:
            Tuple of (allowed: bool, info: dict)
        """
        if not self.cache.enabled:
            # Fall back to local sliding window (not token bucket, but still protective)
            return _local_limiter.check_rate_limit(identifier, capacity, 60, "token_bucket")

        current_time = time.time()
        bucket_key = f"bucket:{identifier}"

        try:
            # Get bucket state
            bucket_data = self.cache.get(bucket_key, self.namespace)

            if bucket_data:
                last_tokens = bucket_data['tokens']
                last_time = bucket_data['timestamp']
            else:
                # Initialize bucket
                last_tokens = capacity
                last_time = current_time

            # Calculate tokens to add based on time elapsed
            time_elapsed = current_time - last_time
            tokens_to_add = time_elapsed * refill_rate

            # Update token count (capped at capacity)
            current_tokens = min(capacity, last_tokens + tokens_to_add)

            # Check if enough tokens
            allowed = current_tokens >= tokens_needed

            if allowed:
                # Consume tokens
                new_tokens = current_tokens - tokens_needed
            else:
                # Don't consume tokens
                new_tokens = current_tokens

            # Save bucket state
            bucket_state = {
                'tokens': new_tokens,
                'timestamp': current_time
            }
            self.cache.set(bucket_key, bucket_state, ttl=3600, namespace=self.namespace)

            info = {
                'tokens': new_tokens,
                'capacity': capacity,
                'allowed': allowed,
                'refill_rate': refill_rate
            }

            if not allowed:
                logger.warning(
                    f"Token bucket limit exceeded for {identifier}: "
                    f"{current_tokens:.2f}/{capacity} tokens (needed: {tokens_needed})"
                )

            return allowed, info

        except (RedisError, ConnectionError, OSError) as e:
            logger.error(f"Token bucket error: {e}")
            # On error, allow request
            return True, {'tokens': capacity, 'capacity': capacity, 'error': str(e)}


# Predefined rate limit configurations
RATE_LIMITS = {
    'auth': {
        'max_requests': 10,
        'window_seconds': 60,
        'description': 'Authentication attempts (login, register)'
    },
    'api': {
        'max_requests': 1000,
        'window_seconds': 60,
        'description': 'General API requests'
    },
    'chat': {
        'max_requests': 100,
        'window_seconds': 60,
        'description': 'Chat message submissions'
    },
    'password_reset': {
        'max_requests': 3,
        'window_seconds': 3600,
        'description': 'Password reset requests'
    },
    'email_verification': {
        'max_requests': 5,
        'window_seconds': 3600,
        'description': 'Email verification requests'
    }
}


# Global rate limiter instances
rate_limiter = RateLimiter()
token_bucket_limiter = TokenBucketRateLimiter()


# Convenience functions
def check_rate_limit(
    identifier: str,
    limit_type: str = 'api'
) -> Tuple[bool, dict]:
    """
    Check rate limit using predefined configuration

    Args:
        identifier: Unique identifier (user_id, IP, etc.)
        limit_type: Type of limit from RATE_LIMITS

    Returns:
        Tuple of (allowed: bool, info: dict)
    """
    config = RATE_LIMITS.get(limit_type, RATE_LIMITS['api'])

    return rate_limiter.check_rate_limit(
        identifier=identifier,
        max_requests=config['max_requests'],
        window_seconds=config['window_seconds'],
        limit_type=limit_type
    )


def reset_rate_limit(identifier: str, limit_type: str = 'api') -> bool:
    """Reset rate limit for identifier"""
    return rate_limiter.reset_limit(identifier, limit_type)


# Export public interface
__all__ = [
    'LocalRateLimiter',
    'RateLimiter',
    'TokenBucketRateLimiter',
    'rate_limiter',
    'token_bucket_limiter',
    'check_rate_limit',
    'reset_rate_limit',
    'RATE_LIMITS'
]
