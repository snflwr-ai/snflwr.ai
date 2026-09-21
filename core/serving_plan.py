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
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Optional

import httpx

from core.endpoint_url import validate_endpoint

logger = logging.getLogger(__name__)


# Sealed-run provenance. A backbone is certified for a SPECIFIC engine and
# context window; anything else is unverified until it has its own sealed run.
@dataclass(frozen=True)
class CertifiedBackbone:
    model: str
    engine: str
    # What `ollama create` builds this wrapper FROM. Recorded here because the
    # install paths used to ask resource_detection's ladder instead, and the two
    # disagreed: the ladder reserves card space for a classifier that is
    # CPU-pinned, so on the very box the sealed run was measured on it picked
    # gemma4:e4b -- and every deploy rebuilt an UNCERTIFIED tutor while the plan
    # served the certified one that had been built by hand. One source of truth
    # for "what is the tutor and what is it made of" (2026-09-20).
    base: str
    num_ctx: int
    vram_gb: float  # resident weights + KV at num_ctx, measured
    sealed_on: str  # ISO date of the sealed run that certified it
    note: str = ""
    # The largest window a tutoring run has VALIDATED for this backbone. A box
    # with room to spare may serve up to this and no further: a bigger window
    # changes the KV layout and gives the model more room to run past the guided
    # shape, which is a quality question, and quality questions get measured.
    max_validated_num_ctx: int = 0
    # Fingerprint of the SYSTEM PROMPT the sealed run actually measured.
    #
    # Everything else here certifies a NAME. The tutor's pedagogy, its homework
    # integrity rules and its safety posture all live in the system prompt baked
    # into that named model, so a rebuild with a different prompt keeps the name,
    # keeps this entry, and keeps reporting "certified" while serving something
    # nobody measured.
    #
    # That is not hypothetical: the reveal confirm collapsed to 0/20 recall in
    # production when an unrelated PR added a paragraph to the Modelfile, and
    # nothing noticed, because no check anywhere knew what the prompt was
    # supposed to be. `sha256(system.strip())[:12]`.
    system_sha256: str = ""

    @property
    def validated_ceiling(self) -> int:
        return max(self.num_ctx, self.max_validated_num_ctx)


CERTIFIED_BACKBONES: tuple[CertifiedBackbone, ...] = (
    CertifiedBackbone(
        model="snflwr.ai-31b",
        engine="ollama",
        base="gemma4:31b",
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
        # The prompt the 2026-09-17 sealed run measured. Changing the tutor's
        # system prompt REQUIRES changing this line, and changing this line is a
        # claim that the bars were re-measured against the new prompt.
        system_sha256="43a76481684d",
    ),
)


def fingerprint_system_prompt(system: str) -> str:
    """Stable short fingerprint of a tutor system prompt.

    Whitespace-stripped at the ends only: Ollama round-trips the prompt through
    a Modelfile, which can add or drop a trailing newline, and a certificate
    that trips on a newline would be turned off within a week.
    """
    import hashlib

    return hashlib.sha256((system or "").strip().encode("utf-8")).hexdigest()[:12]


def certified_prompt_for(model: str) -> str:
    """The fingerprint the sealed run measured for ``model``, or "" if unknown."""
    for entry in CERTIFIED_BACKBONES:
        if entry.model == model:
            return entry.system_sha256
    return ""


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
    # Remote mode only. False means we could not ASK the tutor server what it
    # serves (unreachable, bad credential, unreadable answer) -- which is not the
    # same as being told something uncertified, and must not be treated the same
    # way. See `get_plan`.
    remote_reachable: bool = True

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


def _remote_base_url() -> str:
    return os.getenv("INFERENCE_REMOTE_URL", "").strip().rstrip("/")


def _remote_token() -> str:
    """The bearer credential for the tutor server. Never logged, never returned
    anywhere it could be rendered -- callers test truthiness only."""
    return os.getenv("INFERENCE_REMOTE_TOKEN", "").strip()


def _fetch_remote_plan(base_url: str, token: str) -> Optional[dict]:
    """What the remote SAYS it serves, or None if we could not ask.

    Unverified on purpose: `_remote_plan()` judges it against the certified
    table. This function only performs the fetch.

    Synchronous because `compute_plan()` runs at startup outside an event loop.
    `RemoteDriver.fetch_plan` is the async twin and parses the same payload; the
    two must stay in step.

    KNOWN LIMIT -- the certification check happens when the plan is computed, not
    per turn. A remote reconfigured to an uncertified backbone afterwards keeps
    receiving turns until something refreshes the plan. That is the same shape as
    a safety classifier silently following a backbone swap, and the fix is a
    periodic re-verify; it is not built yet, and is recorded here rather than
    left for someone to discover.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.get(f"{base_url}/api/inference/plan", headers=headers, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - unreachable is a state, not an error
        logger.warning("remote tutor server unreachable (%s)", exc)
        return None
    if resp.status_code in (401, 403):
        logger.warning(
            "remote tutor server rejected our credential (%s); "
            "check INFERENCE_REMOTE_TOKEN is set and matches the server",
            resp.status_code,
        )
        return None
    if resp.status_code >= 400:
        logger.warning("remote plan endpoint returned %s", resp.status_code)
        return None
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("remote plan is not JSON (%s)", exc)
        return None
    return payload if isinstance(payload, dict) else None


def _remote_plan(base_url: str) -> ServingPlan:
    """The plan for a box that offloads inference to a snflwr tutor server.

    LOCAL HARDWARE IS NOT CONSULTED. A thin client has no GPU, so asking this
    card about a remote card yields VRAM 0 and switches tutoring off -- the same
    failure shape as the API container that could not see nvidia-smi. The remote
    tells us what it serves; we decide whether to accept it.
    """
    token = _remote_token()
    # Two different "no": `unusable` means we could not ASK (nothing to trust),
    # `refused` means we asked and the answer was not certified. They must be
    # distinguishable, because a network blip should not read as a downgraded
    # server, and a downgraded server must not be excused as a network blip.
    unusable = ServingPlan(
        engine="remote",
        remote_reachable=False,
        tutor_model=None,
        quality_tier="unsupported",
        tutoring_enabled=False,
        num_ctx=0,
        max_concurrent_requests=1,
        reason=(
            f"remote tutor server {base_url} did not answer with a usable plan"
            + ("" if token else "; INFERENCE_REMOTE_TOKEN is not set")
        ),
    )

    def refused(reason: str) -> ServingPlan:
        """We reached a verdict about this remote. Not a connectivity problem."""
        return replace(unusable, reason=reason, remote_reachable=True)

    try:
        validate_endpoint(base_url, require_tls_offbox=True)
    except ValueError as exc:
        return refused(f"remote tutor server rejected: {exc}")

    advertised = _fetch_remote_plan(base_url, token)
    if advertised is None:
        return unusable

    try:
        engine = str(advertised["engine"])
        model = str(advertised["model"])
        num_ctx = int(advertised["num_ctx"])
    except (KeyError, TypeError, ValueError) as exc:
        return refused(f"remote plan is not readable: {exc}")
    slots = max(1, int(advertised.get("max_concurrent", 1) or 1))

    # THE QUALITY FLOOR, ENFORCED ON THIS SIDE. A server that has been
    # misconfigured or quietly downgraded must not be able to hand a child a
    # weaker tutor than the one that passed the sealed run, and the client is
    # the party with an interest in that answer being true. Same rule as local
    # mode: the triple (engine, model, context) is what is certified, not a name.
    match = next(
        (e for e in CERTIFIED_BACKBONES if e.engine == engine and e.model == model),
        None,
    )
    if match is None:
        return refused(
            f"remote serves {model} on {engine}, which has no sealed tutoring "
            "run -- refusing to tutor through it"
        )
    if num_ctx > match.validated_ceiling:
        return refused(
            f"remote serves {model} at num_ctx {num_ctx}, above the validated "
            f"ceiling {match.validated_ceiling} -- refusing to tutor through it"
        )

    return ServingPlan(
        engine="remote",
        tutor_model=model,
        quality_tier="certified",
        tutoring_enabled=True,
        num_ctx=num_ctx,
        # Capacity belongs to the server. Serializing here would cap a 64-slot
        # remote at one turn at a time.
        max_concurrent_requests=slots,
        reason=(
            f"remote tutor server {base_url}; {model} on {engine} at num_ctx "
            f"{num_ctx}, certified {match.sealed_on}"
        ),
    )


def compute_plan() -> ServingPlan:
    """Detect hardware and decide what this deployment serves."""
    # Remote mode first, and it short-circuits: see `_remote_plan`.
    remote_url = _remote_base_url()
    if remote_url:
        return _remote_plan(remote_url)

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
_PLAN_AT: float = 0.0

# How long a REMOTE plan may be trusted before we ask the tutor server again.
# Local plans are not re-checked: they describe this box's own hardware, which
# does not change underneath a running process. A remote plan describes ANOTHER
# machine's configuration, and that can be changed by someone else at any time --
# without this, a server reconfigured to an uncertified backbone would keep
# receiving children's turns until something happened to restart us.
# Cost: one plan fetch (a few hundred bytes, 5 s timeout) on one turn every five
# minutes. Against a p90 of 22.5 s that is affordable; against the alternative --
# a silently downgraded tutor -- it is cheap.
REMOTE_PLAN_TTL_S = 300.0


def _remote_ttl_s() -> float:
    override = os.getenv("INFERENCE_REMOTE_PLAN_TTL_S")
    if override:
        try:
            return max(0.0, float(override))
        except ValueError:
            logger.warning("Ignoring non-numeric INFERENCE_REMOTE_PLAN_TTL_S")
    return REMOTE_PLAN_TTL_S


def get_plan(refresh: bool = False) -> ServingPlan:
    """Process-wide plan, computed once; remote plans re-verified on a TTL."""
    global _PLAN, _PLAN_AT
    if _PLAN is None or refresh:
        _PLAN = compute_plan()
        _PLAN_AT = time.monotonic()
        logger.info("serving plan: %s", _PLAN.summary_line())
        return _PLAN

    if _PLAN.engine != "remote":
        return _PLAN
    ttl = _remote_ttl_s()
    if time.monotonic() - _PLAN_AT < ttl:
        return _PLAN

    fresh = compute_plan()
    _PLAN_AT = time.monotonic()
    if not fresh.remote_reachable and _PLAN.remote_reachable:
        # We could not ASK. That is not evidence the remote changed, and taking
        # the tutor down on a blip would show a child "not available on this
        # computer" for what is really "try again in a moment". Keep the last
        # verified plan; if the remote is genuinely down, the turn itself fails
        # on the engine-unreachable path, which says the right thing.
        logger.warning(
            "remote plan re-verify failed (%s); keeping the last verified plan",
            fresh.reason,
        )
        return _PLAN
    if fresh.summary_line() != _PLAN.summary_line():
        logger.warning(
            "remote serving plan CHANGED: %s -> %s",
            _PLAN.summary_line(),
            fresh.summary_line(),
        )
    _PLAN = fresh
    return _PLAN
