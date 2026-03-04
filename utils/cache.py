"""
Redis Caching Layer for snflwr.ai
Distributed caching with automatic invalidation and TTL management
Supports both standalone Redis and Redis Sentinel for high availability
"""

import os
import json
import hashlib
from typing import Any, Optional, Callable, List, Tuple
from datetime import timedelta
from functools import wraps
import redis
from redis.exceptions import RedisError, ConnectionError
from redis.sentinel import Sentinel

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

# Import metrics (lazy to avoid circular imports)
_metrics_available = False
try:
    from utils.metrics import (
        record_cache_operation,
        cache_connection_pool_size,
        redis_sentinel_failovers_total,
        redis_sentinel_slaves
    )
    _metrics_available = True
except ImportError:
    pass


class RedisCache:
    """
    Redis-based caching system with automatic serialization and TTL management.
    Supports both standalone Redis and Redis Sentinel for high availability.

    Supports a **degraded mode**: when Redis is configured (REDIS_ENABLED=true)
    but the connection fails, the cache marks itself as degraded rather than
    fully disabled.  Consumers should check ``is_degraded`` and fall back to
    in-memory alternatives.  The cache will periodically attempt to reconnect
    (default every 30 seconds) and automatically recover when Redis comes back.
    """

    # Seconds between automatic reconnection attempts in degraded mode
    RECONNECT_INTERVAL = int(os.getenv('REDIS_RECONNECT_INTERVAL', '30'))

    def __init__(
        self,
        host: str = None,
        port: int = None,
        db: int = 0,
        password: str = None,
        enabled: bool = True,
        default_ttl: int = 300,  # 5 minutes
        use_sentinel: bool = None,
        sentinel_hosts: List[Tuple[str, int]] = None,
        sentinel_master: str = None
    ):
        """
        Initialize Redis cache with optional Sentinel support

        Args:
            host: Redis host (default: from env or localhost)
            port: Redis port (default: from env or 6379)
            db: Redis database number
            password: Redis password (if required)
            enabled: Whether caching is enabled
            default_ttl: Default TTL in seconds
            use_sentinel: Use Redis Sentinel for HA (default: from env)
            sentinel_hosts: List of (host, port) tuples for Sentinel nodes
            sentinel_master: Name of the Sentinel master
        """
        self.enabled = enabled and os.getenv('REDIS_ENABLED', 'false').lower() == 'true'
        self.default_ttl = default_ttl

        # Degraded mode: Redis was configured but the connection failed.
        # Distinct from ``enabled=False`` which means Redis was never requested.
        self._degraded = False
        self._last_reconnect_attempt = 0.0  # epoch timestamp

        if not self.enabled:
            logger.warning("Redis caching is disabled - authentication rate limiting will be unavailable")
            self._client = None
            self._sentinel = None
            return

        # Sentinel configuration
        self.use_sentinel = use_sentinel if use_sentinel is not None else (
            os.getenv('REDIS_SENTINEL_ENABLED', 'false').lower() == 'true'
        )
        self.sentinel_master = sentinel_master or os.getenv('REDIS_SENTINEL_MASTER', 'mymaster')
        self.sentinel_hosts = sentinel_hosts or self._parse_sentinel_hosts()

        # Standard Redis configuration
        self.host = host or os.getenv('REDIS_HOST', 'localhost')
        self.port = port or int(os.getenv('REDIS_PORT', '6379'))
        self.db = db
        self.password = password or os.getenv('REDIS_PASSWORD', None)

        self._client: Optional[redis.Redis] = None
        self._sentinel: Optional[Sentinel] = None
        self._stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'deletes': 0,
            'errors': 0,
            'failovers': 0
        }

        self._initialize_connection()

    def _parse_sentinel_hosts(self) -> List[Tuple[str, int]]:
        """Parse Sentinel hosts from environment variable"""
        hosts_str = os.getenv('REDIS_SENTINEL_HOSTS', '')
        if not hosts_str:
            return []

        hosts = []
        for host_port in hosts_str.split(','):
            host_port = host_port.strip()
            if ':' in host_port:
                host, port = host_port.split(':')
                hosts.append((host.strip(), int(port.strip())))
            else:
                hosts.append((host_port, 26379))  # Default Sentinel port
        return hosts

    @property
    def is_degraded(self) -> bool:
        """True when Redis was configured but is currently unavailable.

        In degraded mode ``enabled`` is False (so callers that check
        ``if not self.enabled`` continue to work), but the cache will
        periodically attempt to reconnect.
        """
        return self._degraded

    def _initialize_connection(self):
        """Initialize Redis connection (standalone or Sentinel)"""
        try:
            if self.use_sentinel and self.sentinel_hosts:
                self._initialize_sentinel()
            else:
                self._initialize_standalone()
            # Connection succeeded — clear degraded flag if it was set
            if self._degraded:
                logger.info("Redis connection recovered — leaving degraded mode")
                self._degraded = False
        except (RedisError, ConnectionError, OSError) as e:
            logger.error(f"Redis connection failed: {e}")
            logger.error("Redis is required for authentication rate limiting and Celery tasks")
            logger.warning(
                "Entering DEGRADED MODE — cache operations will return defaults. "
                "The cache will attempt to reconnect every %ds.",
                self.RECONNECT_INTERVAL,
            )
            self._degraded = True
            self.enabled = False
            self._client = None

    def _initialize_standalone(self):
        """Initialize standalone Redis connection"""
        from resource_detection import get_resource_profile
        detected_max = get_resource_profile().redis_max_connections

        # Connection pool for better performance
        self.pool = redis.ConnectionPool(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            max_connections=detected_max,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )

        self._client = redis.Redis(connection_pool=self.pool)
        # Test connection
        self._client.ping()
        logger.info(f"✅ Redis cache connected: {self.host}:{self.port} (db: {self.db})")

    def _initialize_sentinel(self):
        """Initialize Redis Sentinel connection for high availability"""
        logger.info(f"Initializing Redis Sentinel: {self.sentinel_hosts}")

        # Create Sentinel connection
        self._sentinel = Sentinel(
            self.sentinel_hosts,
            socket_timeout=5,
            password=self.password,
            sentinel_kwargs={'password': self.password} if self.password else {}
        )

        # Get master connection
        self._client = self._sentinel.master_for(
            self.sentinel_master,
            socket_timeout=5,
            password=self.password,
            db=self.db,
            decode_responses=True
        )

        # Test connection
        self._client.ping()

        # Get master info
        master_info = self._sentinel.discover_master(self.sentinel_master)
        logger.info(
            f"✅ Redis Sentinel connected: master={self.sentinel_master} "
            f"at {master_info[0]}:{master_info[1]} (db: {self.db})"
        )
        logger.info(f"✅ Sentinel nodes: {len(self.sentinel_hosts)} configured")

    def _maybe_reconnect(self) -> bool:
        """Attempt to reconnect if in degraded mode and enough time has elapsed.

        Returns True if the cache is now available (either it wasn't degraded,
        or reconnection succeeded).
        """
        if not self._degraded:
            return self.enabled

        import time as _time
        now = _time.time()
        if now - self._last_reconnect_attempt < self.RECONNECT_INTERVAL:
            return False

        self._last_reconnect_attempt = now
        logger.info("Attempting Redis reconnection from degraded mode…")
        self._initialize_connection()
        if not self._degraded:
            # Reconnection succeeded
            self.enabled = True
            return True
        return False

    def get_master_info(self) -> Optional[dict]:
        """Get current master information (Sentinel mode only)"""
        if not self._sentinel:
            return {"mode": "standalone", "host": self.host, "port": self.port}

        try:
            master = self._sentinel.discover_master(self.sentinel_master)
            slaves = self._sentinel.discover_slaves(self.sentinel_master)
            return {
                "mode": "sentinel",
                "master": f"{master[0]}:{master[1]}",
                "slave_count": len(slaves),
                "slaves": [f"{s[0]}:{s[1]}" for s in slaves],
                "sentinel_master": self.sentinel_master
            }
        except (RedisError, ConnectionError, OSError) as e:
            logger.error(f"Failed to get master info: {e}")
            return None

    def _handle_connection_error(self, e: Exception):
        """Handle connection errors with automatic reconnection"""
        self._stats['errors'] += 1
        logger.error(f"Redis connection error: {e}")

        if self.use_sentinel and self._sentinel:
            # Sentinel will handle failover automatically
            self._stats['failovers'] += 1
            logger.warning("Redis master may have failed over. Sentinel will redirect.")
            if _metrics_available:
                redis_sentinel_failovers_total.inc()
            try:
                # Refresh master connection
                self._client = self._sentinel.master_for(
                    self.sentinel_master,
                    socket_timeout=5,
                    password=self.password,
                    db=self.db,
                    decode_responses=True
                )
                logger.info("Successfully reconnected to new master")
                # Update slave count metric after failover
                if _metrics_available:
                    try:
                        slaves = self._sentinel.discover_slaves(self.sentinel_master)
                        redis_sentinel_slaves.set(len(slaves))
                    except (RedisError, ConnectionError, OSError):
                        pass
            except (RedisError, ConnectionError, OSError) as reconnect_error:
                logger.error(f"Failed to reconnect: {reconnect_error}")

    def _make_key(self, key: str, namespace: str = "snflwr") -> str:
        """
        Generate namespaced cache key

        Args:
            key: Cache key
            namespace: Namespace prefix

        Returns:
            Namespaced key
        """
        return f"{namespace}:{key}"

    def _serialize(self, value: Any) -> str:
        """Serialize value to JSON string"""
        # Handle objects with to_dict() method - store type info for reconstruction
        if hasattr(value, 'to_dict') and callable(getattr(value, 'to_dict')):
            type_name = f"{value.__class__.__module__}.{value.__class__.__name__}"
            value = {
                '__type__': type_name,
                '__data__': value.to_dict()
            }
        # Handle lists of objects with to_dict()
        elif isinstance(value, list) and value and hasattr(value[0], 'to_dict'):
            type_name = f"{value[0].__class__.__module__}.{value[0].__class__.__name__}"
            value = {
                '__type__': f"List[{type_name}]",
                '__data__': [item.to_dict() if hasattr(item, 'to_dict') else item for item in value]
            }
        return json.dumps(value)

    def _deserialize(self, value: str) -> Any:
        """Deserialize JSON string to value"""
        try:
            data = json.loads(value)

            # Handle custom serialized objects with type information
            if isinstance(data, dict) and '__type__' in data and '__data__' in data:
                type_name = data['__type__']
                obj_data = data['__data__']

                # Reconstruct ChildProfile objects
                if 'ChildProfile' in type_name:
                    from core.profile_manager import ChildProfile
                    return ChildProfile(**obj_data)

                # Reconstruct lists of ChildProfile objects
                elif type_name.startswith('List[') and 'ChildProfile' in type_name:
                    from core.profile_manager import ChildProfile
                    return [ChildProfile(**item) for item in obj_data]

            return data
        except (json.JSONDecodeError, TypeError):
            return value

    def get(self, key: str, namespace: str = "snflwr") -> Optional[Any]:
        """
        Get value from cache

        Args:
            key: Cache key
            namespace: Namespace prefix

        Returns:
            Cached value or None if not found
        """
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return None

        import time
        start_time = time.time()

        try:
            cache_key = self._make_key(key, namespace)
            value = self._client.get(cache_key)
            duration = time.time() - start_time

            if value is not None:
                self._stats['hits'] += 1
                logger.debug(f"Cache HIT: {cache_key}")
                if _metrics_available:
                    record_cache_operation('get', 'hit', duration)
                return self._deserialize(value)
            else:
                self._stats['misses'] += 1
                logger.debug(f"Cache MISS: {cache_key}")
                if _metrics_available:
                    record_cache_operation('get', 'miss', duration)
                return None

        except RedisError as e:
            self._stats['errors'] += 1
            logger.error(f"Redis get error: {e}")
            if _metrics_available:
                record_cache_operation('get', 'error', time.time() - start_time)
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: int = None,
        namespace: str = "snflwr"
    ) -> bool:
        """
        Set value in cache

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (None = default_ttl)
            namespace: Namespace prefix

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return False

        import time
        start_time = time.time()

        try:
            cache_key = self._make_key(key, namespace)
            serialized_value = self._serialize(value)
            ttl = ttl or self.default_ttl

            self._client.setex(cache_key, ttl, serialized_value)
            self._stats['sets'] += 1
            logger.debug(f"Cache SET: {cache_key} (TTL: {ttl}s)")
            if _metrics_available:
                record_cache_operation('set', 'success', time.time() - start_time)
            return True

        except RedisError as e:
            self._stats['errors'] += 1
            logger.error(f"Redis set error: {e}")
            if _metrics_available:
                record_cache_operation('set', 'error', time.time() - start_time)
            return False

    def delete(self, key: str, namespace: str = "snflwr") -> bool:
        """
        Delete value from cache

        Args:
            key: Cache key
            namespace: Namespace prefix

        Returns:
            True if deleted, False otherwise
        """
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return False

        import time
        start_time = time.time()

        try:
            cache_key = self._make_key(key, namespace)
            result = self._client.delete(cache_key)
            self._stats['deletes'] += 1
            logger.debug(f"Cache DELETE: {cache_key}")
            if _metrics_available:
                record_cache_operation('delete', 'success', time.time() - start_time)
            return result > 0

        except RedisError as e:
            self._stats['errors'] += 1
            logger.error(f"Redis delete error: {e}")
            if _metrics_available:
                record_cache_operation('delete', 'error', time.time() - start_time)
            return False

    def delete_pattern(self, pattern: str, namespace: str = "snflwr") -> int:
        """
        Delete all keys matching pattern

        Args:
            pattern: Key pattern (e.g., "user:*")
            namespace: Namespace prefix

        Returns:
            Number of keys deleted
        """
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return 0

        try:
            cache_pattern = self._make_key(pattern, namespace)
            keys = self._client.keys(cache_pattern)

            if keys:
                deleted = self._client.delete(*keys)
                self._stats['deletes'] += deleted
                logger.info(f"Cache DELETE pattern: {cache_pattern} ({deleted} keys)")
                return deleted

            return 0

        except RedisError as e:
            self._stats['errors'] += 1
            logger.error(f"Redis delete pattern error: {e}")
            return 0

    def exists(self, key: str, namespace: str = "snflwr") -> bool:
        """Check if key exists in cache"""
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return False

        try:
            cache_key = self._make_key(key, namespace)
            return self._client.exists(cache_key) > 0
        except RedisError as e:
            logger.error(f"Redis exists error: {e}")
            return False

    def increment(self, key: str, amount: int = 1, namespace: str = "snflwr") -> Optional[int]:
        """
        Increment a counter

        Args:
            key: Counter key
            amount: Amount to increment
            namespace: Namespace prefix

        Returns:
            New counter value or None on error
        """
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return None

        try:
            cache_key = self._make_key(key, namespace)
            return self._client.incrby(cache_key, amount)
        except RedisError as e:
            logger.error(f"Redis increment error: {e}")
            return None

    def expire(self, key: str, ttl: int, namespace: str = "snflwr") -> bool:
        """Set expiration on existing key"""
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return False

        try:
            cache_key = self._make_key(key, namespace)
            return self._client.expire(cache_key, ttl)
        except RedisError as e:
            logger.error(f"Redis expire error: {e}")
            return False

    def get_stats(self) -> dict:
        """Get cache statistics including Sentinel and degraded-mode info"""
        stats = self._stats.copy()
        stats['degraded'] = self._degraded

        if self.enabled and self._client:
            try:
                info = self._client.info('stats')
                stats['redis_hits'] = info.get('keyspace_hits', 0)
                stats['redis_misses'] = info.get('keyspace_misses', 0)
                stats['total_connections'] = info.get('total_connections_received', 0)
            except RedisError:
                pass

        # Calculate hit rate
        total = stats['hits'] + stats['misses']
        stats['hit_rate'] = (stats['hits'] / total * 100) if total > 0 else 0

        # Add Sentinel info if available
        if self._sentinel:
            stats['mode'] = 'sentinel'
            stats['sentinel_master'] = self.sentinel_master
            master_info = self.get_master_info()
            if master_info:
                stats['master'] = master_info.get('master')
                stats['slave_count'] = master_info.get('slave_count', 0)
        else:
            stats['mode'] = 'standalone'
            stats['master'] = f"{self.host}:{self.port}"

        return stats

    def clear_all(self, namespace: str = "snflwr") -> int:
        """Clear all keys in namespace"""
        return self.delete_pattern("*", namespace)

    def health_check(self) -> bool:
        """Check if Redis is healthy"""
        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                return False

        try:
            return self._client.ping()
        except RedisError as e:
            # Try to handle connection error for Sentinel
            self._handle_connection_error(e)
            # Retry ping after reconnection attempt
            try:
                return self._client.ping() if self._client else False
            except RedisError:
                return False

    def health_check_detailed(self) -> dict:
        """Detailed health check with Sentinel and degraded-mode status"""
        result = {
            "healthy": False,
            "mode": "sentinel" if self._sentinel else "standalone",
            "enabled": self.enabled,
            "degraded": self._degraded,
        }

        if not self.enabled or not self._client:
            self._maybe_reconnect()
            if not self.enabled or not self._client:
                if self._degraded:
                    result["error"] = "Redis configured but unavailable (degraded mode)"
                else:
                    result["error"] = "Redis not enabled"
                return result

        try:
            # Basic ping
            if self._client.ping():
                result["healthy"] = True

            # Get connection info
            info = self._client.info('server')
            result["redis_version"] = info.get('redis_version', 'unknown')
            result["uptime_seconds"] = info.get('uptime_in_seconds', 0)

            # Sentinel-specific info
            if self._sentinel:
                master_info = self.get_master_info()
                if master_info:
                    result["master"] = master_info.get('master')
                    result["slave_count"] = master_info.get('slave_count', 0)
                    result["slaves"] = master_info.get('slaves', [])
                    result["sentinel_nodes"] = len(self.sentinel_hosts)
            else:
                result["host"] = f"{self.host}:{self.port}"

            result["stats"] = {
                "failovers": self._stats.get('failovers', 0),
                "errors": self._stats.get('errors', 0)
            }

        except RedisError as e:
            result["healthy"] = False
            result["error"] = str(e)

        return result


# Decorator for caching function results
def cached(
    ttl: int = 300,
    namespace: str = "snflwr",
    key_prefix: str = None,
    make_key: Callable = None
):
    """
    Decorator to cache function results

    Args:
        ttl: Time to live in seconds
        namespace: Cache namespace
        key_prefix: Prefix for cache key
        make_key: Custom function to generate cache key from args

    Usage:
        @cached(ttl=60, key_prefix="user")
        def get_user(user_id: str):
            # Expensive operation
            return fetch_user_from_db(user_id)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            if make_key:
                cache_key = make_key(*args, **kwargs)
            else:
                # Default key generation
                key_parts = [key_prefix or func.__name__]

                # Add positional args
                for arg in args:
                    if isinstance(arg, (str, int, float, bool)):
                        key_parts.append(str(arg))

                # Add keyword args (sorted for consistency)
                for k, v in sorted(kwargs.items()):
                    if isinstance(v, (str, int, float, bool)):
                        key_parts.append(f"{k}:{v}")

                cache_key = ":".join(key_parts)

            # Try to get from cache
            cached_value = cache.get(cache_key, namespace)
            if cached_value is not None:
                logger.debug(f"Cache hit for {func.__name__}: {cache_key}")
                return cached_value

            # Cache miss - execute function
            logger.debug(f"Cache miss for {func.__name__}: {cache_key}")
            result = func(*args, **kwargs)

            # Store in cache
            cache.set(cache_key, result, ttl, namespace)

            return result

        return wrapper
    return decorator


# Global cache instance
cache = RedisCache()


# Export public interface
__all__ = [
    'RedisCache',
    'cache',
    'cached'
]
