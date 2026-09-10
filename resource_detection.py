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


# ---------------------------------------------------------------------------
# Tutor backbone selection
#
# gemma4 is the brain on EVERY deployment. Keeping one family everywhere means
# the tutoring behaviour, the persona adherence and the S9051B compliance work
# all transfer between machines; the previous ladder swapped to another model
# family below 16GB, which quietly gave a small machine a different tutor that
# none of that work had been measured against.
#
# gemma4 publishes no quant-suffixed tags — gemma4:e4b-q4_K_M and friends all
# return 404 from the registry (checked 2026-09-09). It ships pre-quantized at
# Q4_K_M and varies by SIZE VARIANT, so adapting to hardware means picking the
# largest variant that fits rather than re-quantizing.
#
# Each entry is (tag, resident_vram_gb, ram_footprint_gb). TWO sizes, because
# what has to fit differs by where the model runs, and for the MatFormer
# variants the two differ ~3x. Measured 2026-09-10 via /api/ps size_vram and
# `ollama ps`, NOT taken from the manifest: e4b's manifest total is 9.6 GB but
# only 3.3 GB is resident on a GPU (active params; the rest stays off-card).
# Sizing a card from the manifest told a 12 GB box it could not run e4b, which
# it runs comfortably. Ordered by PREFERENCE, not size — first that fits wins.
#
# gemma4:e2b was REMOVED 2026-09-10. Measured over 3 repeats it scored 70.0
# overall vs e4b's 79.0 and 74.9 vs 81.9 on homework integrity — a gap ~4x the
# harness's own run-to-run noise. It was the silent floor every small box fell
# back to, so the product's worst-case deployment was its weakest at withholding
# homework answers. A children's tutor that cannot tutor safely should not ship;
# boxes below the floor now get an explicit unsupported-hardware error.
GEMMA4_VARIANTS = [
    ("gemma4:e4b", 3.3, 9.5),  # best measured quality AND smallest on a GPU
    ("gemma4:12b", 7.9, 7.6),  # only earns a slot on the CPU path (13.6-15.5 GB RAM)
]

# Headroom that must remain free after the tutor is loaded:
#   ~5.5 GB  llama-guard3-cpu, the safety classifier, which has to be resident too
#   ~0.5 GB  runtime overhead
#
# ⚠️ COUPLED TO safety.pipeline.classifier.GUARD_NUM_CTX. Measured 2026-09-10:
# the guard goes resident at 5.46 GB with num_ctx 8192, but 8.05 GB when it
# inherits the server-wide OLLAMA_CONTEXT_LENGTH of 65536 — which it DID until
# that constant was pinned, making this reserve an under-estimate and letting a
# box be told it was supported when the guard would not fit. Raising
# GUARD_NUM_CTX raises the guard's footprint and must raise this number too.
# Sized from a real failure: when the classifier could not stay resident it
# returned empty verdicts, the pipeline failed closed, and children asking
# "what is photosynthesis" were blocked and their parents alerted.
DEFAULT_MODEL_RESERVE_GB = 6.0

# Free VRAM that must remain after the tutor's weights are resident. Smaller
# than DEFAULT_MODEL_RESERVE_GB because the safety classifier is CPU-pinned by
# design (see the llama-guard3-cpu note in the GPU watchdog); what is reserved
# here is the KV cache at the context sizes this product uses, plus runtime.
GPU_HEADROOM_GB = 2.5


def recommend_base_model(
    memory_gb: float,
    vram_gb: float = 0.0,
    reserve_gb: float | None = None,
) -> str:
    """Best gemma4 variant that fits the hardware, or None if none does.

    Selects on VRAM when a usable GPU is present, because that is where the
    model will actually live; otherwise on RAM, and compares against the size
    that matters for that path (resident VRAM vs RAM footprint).

    RETURNS None WHEN NOTHING FITS. It used to return the smallest variant as a
    floor, so an under-spec box silently got a weaker tutor; since e2b measured
    9 points below e4b and 7 below on homework integrity, that floor was
    shipping a tutor we had measured as unsafe at its own job. Callers MUST
    handle None by refusing to install and telling the operator the requirement
    (see `minimum_requirements_gb`), never by substituting a smaller model.

    The reserve DIFFERS BY BUDGET, because the safety classifier is CPU-pinned
    (`llama-guard3-cpu`, `PARAMETER num_gpu 0`). Its ~4.9 GB comes out of RAM,
    never VRAM. Charging the VRAM budget for a classifier that is not on the
    card under-selects the backbone: a 14 GB card was handed `12b` when `e4b`
    fits, and a 12 GB card `e2b` when `12b` fits. Passing `reserve_gb`
    explicitly still overrides both. (2026-09-10)
    """
    on_gpu = bool(vram_gb and vram_gb > 0)
    budget = vram_gb if on_gpu else memory_gb
    if reserve_gb is None:
        reserve_gb = GPU_HEADROOM_GB if on_gpu else DEFAULT_MODEL_RESERVE_GB
    usable = budget - reserve_gb
    for tag, vram_size, ram_size in GEMMA4_VARIANTS:
        if (vram_size if on_gpu else ram_size) <= usable:
            return tag
    return None


def minimum_requirements_gb() -> tuple[float, float]:
    """(min VRAM, min RAM) for ANY supported backbone, incl. the reserves.

    Single source of truth for the unsupported-hardware message, so deploy.sh,
    start_snflwr.sh and the installer cannot drift from the ladder the way the
    five hardcoded ladders did before 2026-09-10.
    """
    min_vram = min(v for _tag, v, _r in GEMMA4_VARIANTS) + GPU_HEADROOM_GB
    min_ram = min(r for _tag, _v, r in GEMMA4_VARIANTS) + DEFAULT_MODEL_RESERVE_GB
    return round(min_vram, 1), round(min_ram, 1)


def unsupported_hardware_message(memory_gb: float, vram_gb: float = 0.0) -> str:
    """One line explaining why this box cannot run a supported backbone."""
    min_vram, min_ram = minimum_requirements_gb()
    return (
        f"unsupported hardware: detected {memory_gb:.1f}GB RAM / {vram_gb:.1f}GB VRAM; "
        f"snflwr.ai needs at least {min_vram}GB VRAM (GPU) or {min_ram}GB RAM (CPU). "
        "Smaller backbones were removed because they measured materially worse at "
        "withholding homework answers, which is not something to ship quietly to children."
    )


# Layers to offload when the GPU is usable. 99 is the conventional "all of
# them" sentinel — gemma4:e4b has far fewer.
_ALL_LAYERS = 99

def recommend_num_gpu(
    model_tag: str,
    vram_gb: float = 0.0,
    headroom_gb: float = GPU_HEADROOM_GB,
) -> int:
    """Layers to offload to the GPU for `model_tag`, or 0 to stay on CPU.

    Exists because gemma4:e4b ships `PARAMETER num_gpu 0` in its own manifest.
    Anything built `FROM gemma4:e4b` inherits that pin silently, so the tutor
    ran entirely on CPU on a box with a free 23GB card until 2026-09-09 — about
    20x slower, and invisible unless you happen to read `ollama ps`.

    Returns 0 rather than guessing whenever the model may not fit: an unknown
    tag, no GPU, or a card without room for the weights plus `headroom_gb`.
    The bad outcome being avoided is not slowness, it is a runner that fails to
    start at all, which on a small-GPU machine would mean no tutor rather than a
    slow one.
    """
    if not vram_gb or vram_gb <= 0:
        return 0
    sizes = {tag: vram for tag, vram, _ram in GEMMA4_VARIANTS}
    size = sizes.get(model_tag)
    if size is None:
        return 0
    return _ALL_LAYERS if vram_gb >= size + headroom_gb else 0


# Measured KV-cache cost, GB per 1000 tokens of context, for the tutor backbone
# on a GPU with OLLAMA_KV_CACHE_TYPE=q4_0 (2026-09-10, gemma4:e4b via /api/ps):
#
#     num_ctx  4096 -> 3.20 GB resident
#     num_ctx 16384 -> 3.34 GB
#     num_ctx 32768 -> 3.44 GB     => ~0.0086 GB per 1k tokens
#
# Rounded UP, because under-estimating means the runner fails to start. This is
# cheap for e4b (elastic, few KV heads); it is NOT a universal constant — the
# 17 GB brain on the same box costs ~0.04 GB/1k, roughly 4.6x more. If a denser
# backbone is ever added to GEMMA4_VARIANTS, re-measure rather than reuse this.
KV_GB_PER_1K_TOKENS = 0.010

# Room for the runner itself (weights + KV are counted separately).
_GPU_RUNTIME_GB = 1.0

_CTX_LADDER = (32768, 16384, 8192, 4096, 2048)


def _detect_vram_gb_safe() -> float:
    """Total VRAM in GB via nvidia-smi, or 0.0 when there is no usable GPU.

    Fail-safe by design: any error means "no GPU", which routes sizing down the
    RAM path rather than promising VRAM that may not exist.
    """
    try:
        import subprocess  # noqa: PLC0415 - keep module import-light

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.strip().splitlines()[0]) / 1024.0
    except Exception:  # noqa: BLE001 - absence of a GPU is normal, not an error
        pass
    return 0.0


def recommend_num_ctx(memory_gb: float, vram_gb: float = 0.0, model_tag: str | None = None) -> int:
    """Context window sized to the memory the KV CACHE will actually occupy.

    Must fit the system prompt (~700 tokens) + conversation history + a full
    response (num_predict tokens).

    Sizes on VRAM when the tutor will live on a GPU, because THAT is where the
    KV cache goes. Keying this on RAM alone (as it did until 2026-09-10) hands a
    box with 64 GB RAM and a small card a 32k context it has no VRAM to back —
    the same dual-budget mistake that had the CPU-pinned safety classifier
    charged against VRAM and the variant sizes read from manifest totals.

    Non-binding for gemma4:e4b at present: 32k costs it only ~0.3 GB, well
    inside GPU_HEADROOM_GB. It binds on a small card, on a denser backbone, or
    if the ladder ever reaches gemma4's full 131k.
    """
    if vram_gb and vram_gb > 0:
        weights = 0.0
        if model_tag:
            weights = {tag: v for tag, v, _r in GEMMA4_VARIANTS}.get(model_tag, 0.0)
        if not weights:
            weights = min(v for _t, v, _r in GEMMA4_VARIANTS)
        budget_gb = vram_gb - weights - _GPU_RUNTIME_GB
        for ctx in _CTX_LADDER:
            if ctx / 1000.0 * KV_GB_PER_1K_TOKENS <= budget_gb:
                return ctx
        return _CTX_LADDER[-1]

    # CPU path: the whole model and its KV cache live in RAM.
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
        num_ctx=recommend_num_ctx(mem_gb, vram_gb=_detect_vram_gb_safe()),
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
