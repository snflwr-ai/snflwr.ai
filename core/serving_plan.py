"""Hardware-driven serving plan: which engine, which backbone, how much concurrency.

WHY THIS EXISTS
---------------
Two measured facts drive every rule here.

1. **Quality has a floor, and it is the backbone.** Sealed run 2026-09-17: the
   31b tutor passed every homework bar (clear 6/131, borderline 12, wrong 4,
   stonewall 13, p90 22.7 s). The smaller backbones do not and cannot be tuned
   into it — across five configurations e4b measured 4-13 wrong replies per 121
   against a bar of 6, and the one configuration that fixed correctness
   stonewalled 38 times; 12b measured 5 and 13 wrong. So a box too small for a
   certified backbone must say "no tutor here", never quietly serve a worse one.

2. **Ollama serializes; vLLM batches.** Load test 2026-09-17 on a 23 GB card:
   throughput was flat at ~5.7 messages/min from 1 to 8 concurrent requests, and
   the usable ceiling was 3 students (p90 29.0 s). At 20 students two thirds of
   replies degraded into the canned withholding fallback because the reveal
   confirm timed out while queued. Admission control belongs in the app for the
   Ollama path, and in the engine for vLLM.

**The sealed result belongs to a (engine, model, context) TRIPLE, not to a model
name.** vLLM serves different weight formats than Ollama's Q4_K_M GGUF, so a
vLLM deployment is `unverified` until the parity re-run passes on it; it refuses
to tutor unless an operator explicitly opts in for benchmarking.

FAIL-OPEN, NEVER FAIL-DANGEROUS: any detection error yields the conservative
plan (Ollama, tutoring disabled), which is a visible, honest degradation rather
than an unmeasured tutor talking to a child.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# Sealed-run provenance. A backbone is certified for a SPECIFIC engine and
# context window; anything else is unverified until it has its own sealed run.
@dataclass(frozen=True)
class CertifiedBackbone:
    model: str
    engine: str
    num_ctx: int
    vram_gb: float  # resident weights + KV at num_ctx, measured
    sealed_on: str  # ISO date of the sealed run that certified it
    note: str = ""
    # The largest window a tutoring run has VALIDATED for this backbone. A box
    # with room to spare may serve up to this and no further: a bigger window
    # changes the KV layout and gives the model more room to run past the guided
    # shape, which is a quality question, and quality questions get measured.
    max_validated_num_ctx: int = 0

    @property
    def validated_ceiling(self) -> int:
        return max(self.num_ctx, self.max_validated_num_ctx)


CERTIFIED_BACKBONES: tuple[CertifiedBackbone, ...] = (
    CertifiedBackbone(
        model="snflwr.ai-31b",
        engine="ollama",
        num_ctx=16384,
        # 18.7 GiB of weights+KV reported by /api/ps, 21.0 GiB of card actually
        # occupied once the CUDA context is counted (nvidia-smi, 2026-09-17).
        # The larger figure is the one a fit check has to respect.
        vram_gb=21.0,
        sealed_on="2026-09-17",
        note="sealed set S, 131 homework probes: all bars passed",
        # Raised only by a validation run at the larger window. 24576 was
        # validated 2026-09-18 on the 121-prompt dev set against the 16384
        # baseline, same config but the window: clear reveals 4 -> 3, borderline
        # 11 -> 11, wrong content 3 -> 0 (hand AND the certified W2 judge),
        # stonewall 10 -> 8, p90 23.2s -> 22.5s. Single-turn only; what the
        # larger window actually buys is multi-turn room, which that run did not
        # measure. Costs ~0.6 GiB more card (21.6 GiB resident at 24576).
        max_validated_num_ctx=24576,
    ),
)

# Reserve on top of the backbone's measured footprint. Small on purpose: the
# 21.0 GiB above is the whole process, CUDA context included, so this covers
# fragmentation only. Sized against the real deployment (22.5 GiB usable on a
# 23,028 MiB card): too large a reserve would declare the box that produced the
# sealed result "unsupported".
GPU_RESERVE_GB = 1.0

# vLLM slot sizing. KV per concurrent sequence at 16k context for a 31b-class
# model, measured conservatively; the plan prefers under-committing because an
# over-committed engine OOMs at load, not at startup.
_VLLM_KV_PER_SLOT_GB = 2.5
_VLLM_WEIGHTS_GB = 20.0  # 4-bit 31b-class weights as served by vLLM
_VLLM_RUNTIME_GB = 2.0
_VLLM_MAX_SLOTS = 64
# Weights + runtime + one usable slot. Below this vLLM loads and then OOMs:
# measured 2026-09-17 on a 23 GB card, 60 MB free when it died.
_VLLM_MIN_VRAM_GB = _VLLM_WEIGHTS_GB + _VLLM_RUNTIME_GB + _VLLM_KV_PER_SLOT_GB
_VLLM_GPU_MEMORY_UTILIZATION = 0.90

_DEFAULT_VLLM_URL = "http://vllm:8000"


@dataclass(frozen=True)
class ServingPlan:
    """What this deployment will actually run, and why."""

    engine: str  # "ollama" | "vllm"
    tutor_model: Optional[str]
    quality_tier: str  # "certified" | "unverified" | "unsupported"
    tutoring_enabled: bool
    num_ctx: int
    max_concurrent_requests: int
    reason: str
    engine_args: dict = field(default_factory=dict)
    speculative_draft_model: Optional[str] = None
    vram_gb: float = 0.0
    memory_gb: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    def summary_line(self) -> str:
        model = self.tutor_model or "none"
        return (
            f"engine={self.engine} model={model} tier={self.quality_tier} "
            f"tutoring={'on' if self.tutoring_enabled else 'off'} "
            f"num_ctx={self.num_ctx} slots={self.max_concurrent_requests} "
            f"({self.reason})"
        )


# --------------------------------------------------------------------------
# Detection seams. Each is a module-level function so tests can substitute it
# and so a failure in one probe cannot take the whole plan down.
# --------------------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def _has_nvidia_gpu() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:  # noqa: BLE001 - a probe that raises means "no GPU we can use"
        return False


def _configured_tutor_model() -> str:
    """The model this deployment is set up to serve, from the same config the app uses."""
    try:
        from config import system_config

        return (
            os.getenv("OLLAMA_DEFAULT_MODEL")
            or getattr(system_config, "OLLAMA_DEFAULT_MODEL", "")
            or ""
        ).strip()
    except Exception:  # noqa: BLE001
        return (os.getenv("OLLAMA_DEFAULT_MODEL") or "").strip()


def _detect_vram_gb() -> float:
    """Total VRAM of the largest visible GPU, or 0.0.

    Returns 0.0 inside the API container, which has no nvidia-smi and no GPU
    device: the card belongs to the ollama container. That is why a VRAM of 0
    must NOT by itself mean "this box cannot tutor" -- see compute_plan.
    """
    env = os.getenv("INFERENCE_VRAM_GB")
    if env:
        try:
            return float(env)
        except ValueError:
            logger.warning("Ignoring non-numeric INFERENCE_VRAM_GB=%r", env)
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode != 0:
            return 0.0
        values = [
            float(line.strip()) for line in out.stdout.splitlines() if line.strip()
        ]
        return round(max(values) / 1024.0, 1) if values else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _detect_memory_gb() -> float:
    try:
        from resource_detection import detect_memory_gb

        return float(detect_memory_gb())
    except Exception:  # noqa: BLE001
        return 0.0


def _vllm_base_url() -> str:
    return os.getenv("VLLM_BASE_URL", _DEFAULT_VLLM_URL).rstrip("/")


def _vllm_reachable() -> bool:
    """True when a vLLM server answers /v1/models."""
    try:
        resp = httpx.get(f"{_vllm_base_url()}/v1/models", timeout=3.0)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 - unreachable is a normal state, not an error
        return False


def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r", name, raw)
        return None
    return value if value >= 1 else None


def _installed_num_ctx() -> Optional[int]:
    """The window the installer probed on this machine, if it wrote one."""
    return _env_int("INFERENCE_NUM_CTX")


def _serving_num_ctx(entry: "CertifiedBackbone") -> int:
    """What this deployment serves: the probed window, capped at the validated one.

    The cap is the whole point. A 48 GB card could hold far more context than any
    tutoring run has measured, and serving an unmeasured window would be the same
    mistake as serving an unmeasured model.

    A box that never ran the probe gets the SEALED window, not the validated
    ceiling: `entry.vram_gb` is the footprint measured at `entry.num_ctx`, so
    that is the only window this deployment can promise without probing.
    """
    from core.context_probe import choose

    return choose(_installed_num_ctx(), entry.validated_ceiling, entry.num_ctx)


def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Plan computation
# --------------------------------------------------------------------------


def _certified_for(engine: str, vram_gb: float) -> Optional[CertifiedBackbone]:
    """Largest certified backbone for this engine that fits, or None."""
    candidates = [
        entry
        for entry in CERTIFIED_BACKBONES
        if entry.engine == engine and vram_gb >= entry.vram_gb + GPU_RESERVE_GB
    ]
    return max(candidates, key=lambda e: e.vram_gb) if candidates else None


def _vllm_slots(vram_gb: float) -> int:
    free = vram_gb - _VLLM_WEIGHTS_GB - _VLLM_RUNTIME_GB
    if free <= 0:
        return 1
    return max(1, min(_VLLM_MAX_SLOTS, int(free // _VLLM_KV_PER_SLOT_GB)))


def _choose_engine(vram_gb: float) -> tuple[str, str]:
    """(engine, reason). Env override wins; otherwise vLLM only where it can run."""
    forced = (os.getenv("INFERENCE_ENGINE") or "auto").strip().lower()
    if forced in ("ollama", "vllm"):
        return forced, f"INFERENCE_ENGINE={forced}"
    if not _is_linux():
        return "ollama", "vllm needs Linux; falling back to ollama"
    if not _has_nvidia_gpu() or vram_gb <= 0:
        return "ollama", "no usable NVIDIA GPU for vllm; falling back to ollama"
    if vram_gb < _VLLM_MIN_VRAM_GB:
        # A reachable vLLM on too small a card is still a dead end: it cannot hold
        # the weights AND a working KV cache. Every community 4-bit build of this
        # backbone measured 19-21 GB (it is multimodal; parts stay unquantised).
        return "ollama", (
            f"vllm needs >= {_VLLM_MIN_VRAM_GB:.0f} GB VRAM for this backbone, "
            f"found {vram_gb:.1f} GB; falling back to ollama"
        )
    if not _vllm_reachable():
        return (
            "ollama",
            f"vllm not reachable at {_vllm_base_url()}; falling back to ollama",
        )
    return "vllm", "vllm reachable on a supported GPU"


def compute_plan() -> ServingPlan:
    """Detect hardware and decide what this deployment serves."""
    try:
        vram_gb = float(_detect_vram_gb())
    except (
        Exception
    ) as exc:  # noqa: BLE001 - see module docstring: degrade, never raise
        logger.warning("VRAM detection failed (%s); assuming no GPU", exc)
        vram_gb = 0.0
    try:
        memory_gb = float(_detect_memory_gb())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory detection failed (%s)", exc)
        memory_gb = 0.0

    engine, engine_reason = _choose_engine(vram_gb)

    if engine == "ollama":
        # THE FLOOR MUST JUDGE WHAT WILL ACTUALLY BE SERVED. The proxy pins every
        # student turn to OLLAMA_DEFAULT_MODEL, so checking "does a certified
        # model fit this card" would pass a box that is configured to serve an
        # uncertified one -- which is exactly the deployment this product had
        # (e4b configured, 31b fitting) when the floor was written.
        configured = _configured_tutor_model()
        if configured:
            match = next(
                (
                    e
                    for e in CERTIFIED_BACKBONES
                    if e.engine == "ollama" and e.model == configured
                ),
                None,
            )
            if match is None:
                return ServingPlan(
                    engine="ollama",
                    tutor_model=None,
                    quality_tier="unsupported",
                    tutoring_enabled=False,
                    num_ctx=0,
                    max_concurrent_requests=1,
                    reason=(
                        f"{engine_reason}; the configured model {configured} has no "
                        "sealed tutoring run -- set OLLAMA_DEFAULT_MODEL to a "
                        "certified backbone"
                    ),
                    vram_gb=vram_gb,
                    memory_gb=memory_gb,
                )
            if 0 < vram_gb < match.vram_gb + GPU_RESERVE_GB:
                return ServingPlan(
                    engine="ollama",
                    tutor_model=None,
                    quality_tier="unsupported",
                    tutoring_enabled=False,
                    num_ctx=0,
                    max_concurrent_requests=1,
                    reason=(
                        f"{engine_reason}; {match.model} needs "
                        f"{match.vram_gb + GPU_RESERVE_GB:.1f} GB VRAM, "
                        f"found {vram_gb:.1f} GB"
                    ),
                    vram_gb=vram_gb,
                    memory_gb=memory_gb,
                )
            slots = _env_int("INFERENCE_MAX_CONCURRENT") or 1
            seen = (
                f"{vram_gb:.1f} GB VRAM"
                if vram_gb > 0
                else "GPU not visible from this container"
            )
            return ServingPlan(
                engine="ollama",
                tutor_model=match.model,
                quality_tier="certified",
                tutoring_enabled=True,
                num_ctx=_serving_num_ctx(match),
                max_concurrent_requests=slots,
                reason=(
                    f"{engine_reason}; serving the configured {match.model} "
                    f"(certified {match.sealed_on}; {seen})"
                ),
                vram_gb=vram_gb,
                memory_gb=memory_gb,
            )

        # Nothing configured: fall back to the largest certified backbone the
        # card can hold, which is what a fresh install gets.
        certified = _certified_for("ollama", vram_gb)
        if certified is None:
            return ServingPlan(
                engine="ollama",
                tutor_model=None,
                quality_tier="unsupported",
                tutoring_enabled=False,
                num_ctx=0,
                max_concurrent_requests=1,
                reason=(
                    f"{engine_reason}; no model configured and no certified backbone "
                    f"fits {vram_gb:.1f} GB VRAM (needs "
                    f"{CERTIFIED_BACKBONES[0].vram_gb + GPU_RESERVE_GB:.1f} GB) -- "
                    "tutoring disabled rather than served by an uncertified model"
                ),
                vram_gb=vram_gb,
                memory_gb=memory_gb,
            )
        slots = _env_int("INFERENCE_MAX_CONCURRENT") or 1
        return ServingPlan(
            engine="ollama",
            tutor_model=certified.model,
            quality_tier="certified",
            tutoring_enabled=True,
            num_ctx=_serving_num_ctx(certified),
            max_concurrent_requests=slots,
            reason=f"{engine_reason}; {certified.model} certified {certified.sealed_on}",
            vram_gb=vram_gb,
            memory_gb=memory_gb,
        )

    # vLLM path
    certified = _certified_for("vllm", vram_gb)
    slots = _env_int("INFERENCE_MAX_CONCURRENT") or _vllm_slots(vram_gb)
    num_ctx = certified.num_ctx if certified else CERTIFIED_BACKBONES[0].num_ctx
    model = certified.model if certified else os.getenv("VLLM_MODEL", "snflwr-31b")
    engine_args = {
        "--max-model-len": num_ctx,
        "--max-num-seqs": slots,
        "--gpu-memory-utilization": _VLLM_GPU_MEMORY_UTILIZATION,
    }
    if certified is not None:
        return ServingPlan(
            engine="vllm",
            tutor_model=certified.model,
            quality_tier="certified",
            tutoring_enabled=True,
            num_ctx=num_ctx,
            max_concurrent_requests=slots,
            reason=f"{engine_reason}; {certified.model} certified {certified.sealed_on}",
            engine_args=engine_args,
            vram_gb=vram_gb,
            memory_gb=memory_gb,
        )

    allowed = _truthy("SNFLWR_ALLOW_UNVERIFIED_ENGINE")
    return ServingPlan(
        engine="vllm",
        tutor_model=model,
        quality_tier="unverified",
        tutoring_enabled=allowed,
        num_ctx=num_ctx,
        max_concurrent_requests=slots,
        reason=(
            f"{engine_reason}; no sealed run exists for this engine and weight "
            "format, so quality is unverified"
            + (
                " — enabled for benchmarking by SNFLWR_ALLOW_UNVERIFIED_ENGINE"
                if allowed
                else " — tutoring disabled until the parity re-run passes"
            )
        ),
        engine_args=engine_args,
        vram_gb=vram_gb,
        memory_gb=memory_gb,
    )


_PLAN: Optional[ServingPlan] = None


def get_plan(refresh: bool = False) -> ServingPlan:
    """Process-wide plan, computed once unless explicitly refreshed."""
    global _PLAN
    if _PLAN is None or refresh:
        _PLAN = compute_plan()
        logger.info("serving plan: %s", _PLAN.summary_line())
    return _PLAN
