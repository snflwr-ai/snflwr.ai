"""
Server Resource Detection and Auto-Configuration

Detects CPU cores, memory, and disk at startup and computes recommended values
for workers, connection pools, and concurrency settings. Every computed value
is overridable via the corresponding environment variable — if an env var is
set, it always takes priority over auto-detection.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

# Use stdlib logging directly — this module is imported by config.py which
# is loaded before the project logger (utils.logger) is available.  Using
# utils.logger here would create a circular import.
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# psutil is already a project dependency (used in api/routes/metrics.py).
# Fall back to stdlib if unavailable for some reason.
# ---------------------------------------------------------------------------
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# Raw hardware detection
# ---------------------------------------------------------------------------

def detect_cpu_count() -> int:
    """Return the number of usable CPU cores (minimum 1)."""
    if _HAS_PSUTIL:
        # Logical cores (includes hyper-threads)
        count = psutil.cpu_count(logical=True)
        if count:
            return max(count, 1)
    count = os.cpu_count()
    return max(count, 1) if count else 1


def detect_memory_bytes() -> int:
    """Return total physical memory in bytes, or 0 if unknown."""
    if _HAS_PSUTIL:
        try:
            return psutil.virtual_memory().total
        except Exception:
            pass
    return 0


def detect_memory_gb() -> float:
    """Return total physical memory in GiB (rounded to one decimal)."""
    mem = detect_memory_bytes()
    return round(mem / (1024 ** 3), 1) if mem else 0.0


def detect_disk_bytes(path: str = "/") -> int:
    """Return total disk space at *path* in bytes, or 0 if unknown."""
    if _HAS_PSUTIL:
        try:
            return psutil.disk_usage(path).total
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------------------
# Recommended-value computation
# ---------------------------------------------------------------------------

def recommend_api_workers(cpu_count: int) -> int:
    """
    Compute a recommended Uvicorn/Gunicorn worker count.

    Classic formula: ``2 * cpu_count + 1``, capped at 8 to avoid
    excessive memory use on large machines. Minimum 2 for production.
    """
    return max(2, min(2 * cpu_count + 1, 8))


def recommend_postgres_max_connections(cpu_count: int, memory_gb: float) -> int:
    """
    Recommend max PostgreSQL connection pool size.

    Each pgbouncer/direct connection costs ~5-10 MB, so we factor both
    CPU (for concurrency) and memory (for headroom).
    """
    by_cpu = cpu_count * 5
    by_mem = int(memory_gb * 3) if memory_gb > 0 else 20
    return max(5, min(by_cpu, by_mem, 100))


def recommend_postgres_min_connections(cpu_count: int) -> int:
    """Minimum idle connections — at least 1 per core, minimum 2."""
    return max(2, cpu_count)


def recommend_redis_max_connections(cpu_count: int) -> int:
    """Recommend Redis connection pool size. Lightweight, so more generous."""
    return max(10, min(cpu_count * 5, 50))


def recommend_celery_concurrency(cpu_count: int) -> int:
    """Recommend Celery worker concurrency (per-worker process count)."""
    return max(2, min(cpu_count * 2, 16))


def recommend_celery_prefetch(memory_gb: float) -> int:
    """
    Recommend Celery prefetch multiplier. Prefetched tasks consume memory,
    so scale down on constrained machines.
    """
    if memory_gb <= 1:
        return 1
    if memory_gb <= 4:
        return 2
    return 4


def recommend_num_predict(memory_gb: float) -> int:
    """
    Recommend Ollama num_predict (max tokens per response) based on available RAM.

    More RAM means a larger model is likely loaded, which can produce longer
    and more detailed responses without running out of context budget.
    """
    if memory_gb >= 32:
        return 16384
    if memory_gb >= 16:
        return 8192
    if memory_gb >= 8:
        return 4096
    if memory_gb >= 4:
        return 2048
    return 1024


def recommend_num_ctx(memory_gb: float) -> int:
    """
    Recommend Ollama context window size based on available RAM.

    Must be large enough to fit system prompt (~700 tokens) + conversation
    history + a full response (num_predict tokens). Scales with RAM since
    larger models loaded on bigger machines benefit from more context.
    """
    if memory_gb >= 32:
        return 32768
    if memory_gb >= 16:
        return 16384
    if memory_gb >= 8:
        return 8192
    if memory_gb >= 4:
        return 4096
    return 2048


# ---------------------------------------------------------------------------
# Aggregated resource profile
# ---------------------------------------------------------------------------

@dataclass
class ResourceProfile:
    """
    Snapshot of detected hardware and the recommended configuration values
    computed from it.  Env-var overrides are applied *after* construction —
    see :func:`detect_resources`.
    """

    # Raw hardware
    cpu_count: int = 1
    memory_bytes: int = 0
    memory_gb: float = 0.0
    disk_bytes: int = 0

    # Recommended values (only includes values that are actually consumed
    # by config.py, cache.py, or connection_pool.py)
    api_workers: int = 2
    postgres_max_connections: int = 20
    postgres_min_connections: int = 2
    redis_max_connections: int = 20
    num_predict: int = 4096
    num_ctx: int = 8192

    def summary_lines(self) -> list:
        """Return a list of human-readable summary strings."""
        mem_str = f"{self.memory_gb} GiB" if self.memory_gb else "unknown"
        disk_gb = round(self.disk_bytes / (1024 ** 3), 1) if self.disk_bytes else 0
        disk_str = f"{disk_gb} GiB" if disk_gb else "unknown"

        return [
            f"CPU cores: {self.cpu_count}",
            f"Memory: {mem_str}",
            f"Disk: {disk_str}",
            f"API workers: {self.api_workers}",
            f"Postgres pool: {self.postgres_min_connections}-{self.postgres_max_connections}",
            f"Redis pool: {self.redis_max_connections}",
            f"Ollama num_predict: {self.num_predict}",
            f"Ollama num_ctx: {self.num_ctx}",
        ]


def _env_int(name: str) -> Optional[int]:
    """Return an env var as int, or None if unset/empty."""
    val = os.getenv(name)
    if val is not None and val.strip():
        try:
            return int(val)
        except ValueError:
            logger.warning(f"Ignoring non-integer env var {name}={val!r}")
    return None


def detect_resources(data_dir: str = "/") -> ResourceProfile:
    """
    Detect hardware and build a :class:`ResourceProfile`.

    Every recommended field can be overridden by the corresponding env var.
    If the env var is set, the detected recommendation is ignored for that
    field (allowing admins to pin values explicitly).
    """
    cpus = detect_cpu_count()
    mem_bytes = detect_memory_bytes()
    mem_gb = detect_memory_gb()
    disk = detect_disk_bytes(data_dir)

    profile = ResourceProfile(
        cpu_count=cpus,
        memory_bytes=mem_bytes,
        memory_gb=mem_gb,
        disk_bytes=disk,
        api_workers=recommend_api_workers(cpus),
        postgres_max_connections=recommend_postgres_max_connections(cpus, mem_gb),
        postgres_min_connections=recommend_postgres_min_connections(cpus),
        redis_max_connections=recommend_redis_max_connections(cpus),
        num_predict=recommend_num_predict(mem_gb),
        num_ctx=recommend_num_ctx(mem_gb),
    )

    # Apply explicit env-var overrides (set by admin = always wins)
    overrides = {
        'API_WORKERS': 'api_workers',
        'POSTGRES_MAX_CONNECTIONS': 'postgres_max_connections',
        'POSTGRES_MIN_CONNECTIONS': 'postgres_min_connections',
        'REDIS_MAX_CONNECTIONS': 'redis_max_connections',
        'OLLAMA_NUM_PREDICT': 'num_predict',
        'OLLAMA_NUM_CTX': 'num_ctx',
    }
    for env_name, attr_name in overrides.items():
        env_val = _env_int(env_name)
        if env_val is not None and env_val >= 1:
            setattr(profile, attr_name, env_val)
            logger.debug(f"Resource override: {env_name}={env_val} (env var)")
        elif env_val is not None:
            logger.warning(
                f"Ignoring non-positive env var {env_name}={env_val}"
            )

    # Sanity: min connections must never exceed max connections
    if profile.postgres_min_connections > profile.postgres_max_connections:
        profile.postgres_min_connections = profile.postgres_max_connections

    return profile


# ---------------------------------------------------------------------------
# Module-level singleton — computed once on first import
# ---------------------------------------------------------------------------
_cached_profile: Optional[ResourceProfile] = None


def get_resource_profile() -> ResourceProfile:
    """
    Return the cached :class:`ResourceProfile` singleton.

    The first call performs hardware detection; subsequent calls return
    the cached result.
    """
    global _cached_profile
    if _cached_profile is None:
        _cached_profile = detect_resources()
    return _cached_profile
