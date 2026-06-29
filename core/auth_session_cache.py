"""Redis-backed session cache mixin for AuthenticationManager.

Extracted verbatim from ``core/authentication.py`` (behavior-preserving
refactor). ``SessionCacheMixin`` holds the distributed session-cache plumbing
(Redis with an in-memory fallback). It is mixed into ``AuthenticationManager``
and relies on instance attributes set in that class's ``__init__``:
``self._redis``, ``self._session_lock``, ``self._fallback_sessions`` and
``self._session_ttl``. No public behavior changes — these methods resolve only
``self`` and module-level imports, none of which the test suite patches via
``core.authentication``.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from utils.logger import get_logger

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # type: ignore[misc,assignment]

logger = get_logger(__name__)


class SessionCacheMixin:
    """Distributed (Redis) session cache with an in-memory fallback."""

    # Declared here so the attribute always exists (and is typed) even when
    # ``_initialize_redis`` takes the fallback/except path and never assigns a
    # client — otherwise ``if self._redis`` would raise AttributeError.
    _redis: Optional[Any] = None

    def _initialize_redis(self):
        """Initialize Redis for distributed session caching"""
        try:
            from utils.cache import cache

            if cache.enabled and cache._client:
                self._redis = cache._client
                logger.info("[OK] Session cache using Redis (distributed mode)")
            else:
                logger.warning(
                    "[WARN] Session cache using in-memory fallback (single-instance only)"
                )
        except (ImportError, RedisError) as e:
            logger.warning(
                f"[WARN] Session cache Redis init failed, using fallback: {e}"
            )

    def _get_session_from_cache(self, session_token: str) -> Optional[dict]:
        """Get session from Redis or fallback cache, checking expiry"""
        session_data = None
        if self._redis:
            try:
                import json

                redis_key = f"snflwr:session:{session_token}"
                data = self._redis.get(redis_key)
                if data:
                    session_data = json.loads(data)
            except RedisError as e:
                logger.debug(f"Redis session get failed: {e}")
        else:
            with self._session_lock:
                session_data = self._fallback_sessions.get(session_token)

        # Check expiry if present in cached data
        if session_data and "expires_at" in session_data:
            try:
                expires_at = datetime.fromisoformat(session_data["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < datetime.now(timezone.utc):
                    # Expired - remove from cache and return None
                    self._delete_session_from_cache(session_token)
                    return None
            except ValueError as e:
                logger.debug(f"Failed to check cached session expiry: {e}")

        return session_data

    def _set_session_in_cache(self, session_token: str, session_data: dict):
        """Store session in Redis or fallback cache"""
        if self._redis:
            try:
                import json

                redis_key = f"snflwr:session:{session_token}"
                self._redis.setex(
                    redis_key, self._session_ttl, json.dumps(session_data)
                )
                # Maintain reverse index for O(1) bulk invalidation on password change
                user_key = f"snflwr:user_sessions:{session_data['parent_id']}"
                self._redis.sadd(user_key, session_token)
                self._redis.expire(user_key, self._session_ttl)
            except RedisError as e:
                logger.debug(f"Redis session set failed: {e}")
        else:
            with self._session_lock:
                self._fallback_sessions[session_token] = session_data

    def _delete_session_from_cache(self, session_token: str):
        """Remove session from Redis or fallback cache"""
        if self._redis:
            try:
                redis_key = f"snflwr:session:{session_token}"
                self._redis.delete(redis_key)
            except RedisError as e:
                logger.debug(f"Redis session delete failed: {e}")
        else:
            with self._session_lock:
                self._fallback_sessions.pop(session_token, None)

    def _delete_user_sessions_from_cache(self, user_id: str):
        """Remove all sessions for a user from cache.

        Uses reverse index snflwr:user_sessions:{user_id} for O(k) deletion
        where k = number of sessions for this user (typically 1-5).
        """
        if self._redis:
            try:
                user_key = f"snflwr:user_sessions:{user_id}"
                tokens = self._redis.smembers(user_key)
                if tokens:
                    session_keys = [
                        f"snflwr:session:{t.decode() if isinstance(t, bytes) else t}"
                        for t in tokens
                    ]
                    self._redis.delete(*session_keys)
                self._redis.delete(user_key)
            except RedisError as e:
                logger.debug(f"Redis user sessions delete failed: {e}")
        else:
            with self._session_lock:
                to_remove = [
                    sid
                    for sid, data in self._fallback_sessions.items()
                    if data.get("parent_id") == user_id
                ]
                for sid in to_remove:
                    del self._fallback_sessions[sid]
