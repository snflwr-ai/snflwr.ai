"""Per-request GPU placement for the tutor, with a thrash damper.

WHY THIS EXISTS
---------------
The tutor and IronClaw's agent brain cannot co-reside on this class of box.
Measured 2026-09-10 on a 23 GB card: the brain needs 18.4-21.0 GB depending on
its context window and the tutor needs 4.47 GB, so even at the brain's smallest
setting the headroom is 4.07 GB — short. Context reduction was measured and
ruled out as a fix; the weights are the wall.

Ollama's scheduler already arbitrates correctly: asked for the tutor while the
brain holds the card, it evicts the brain, loads the tutor, and answers. No
orchestrator is required for correctness. Measured swap cost is a flat ~5.2 s in
either direction, against ~0.49 s warm.

That makes GPU placement worth having even in the worst case:

    tutor on CPU (today)          13.6 s generate + ~0.8 s guards ~ 14.4 s/turn
    tutor on GPU, swap needed     ~4.7 s swap + ~2 s generate     ~  7.5 s/turn
    tutor on GPU, resident        ~1.4 s + guards                 ~  2.2 s/turn

THE DAMPER
----------
Left alone, alternating traffic makes both services pay ~5.2 s per turn. A
cross-service lock would be the obvious fix, but a lock only damps thrash if
BOTH claimants honour it, and only snflwr is ours to change. So this is
deliberately ONE-SIDED: the tutor stops fighting back for a cooldown after each
swap it causes. IronClaw evicts whenever it likes; we simply do not immediately
re-take the card.

The bound that matters: **at most one tutor-induced swap per cooldown window**,
regardless of how traffic interleaves. During a cooldown the tutor runs on CPU
at ~14.4 s/turn — exactly today's behaviour, so the floor never gets worse.

FAIL-OPEN. Any error returns CPU placement, which is current production
behaviour. A tutor that answers slowly is a degraded tutor; a tutor that raises
is a child staring at an error.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Ollama's "offload every layer" sentinel. The real layer count is smaller.
ALL_LAYERS = 999
CPU_ONLY = 0

# The state file and swap cooldown that used to live here are gone: the policy
# is no longer time-based. See choose_num_gpu for why the cooldown made
# concurrent load dramatically worse rather than better.


def _ollama_url() -> str:
    """Where Ollama actually is, from the SAME config the rest of the app uses.

    Not a 127.0.0.1 default: inside the API container that is the CONTAINER's
    loopback, not the host, so the probe got ECONNREFUSED and fail-open served
    every turn from CPU — the feature silently inert while looking healthy.
    Found on the first real deploy; the unit tests mocked this call, so nothing
    below integration level could have caught it.

    OLLAMA_PROXY_TARGET / OLLAMA_BASE_URL are what the proxy and client already
    use, so placement follows the app rather than keeping its own opinion.
    """
    for env in ("OLLAMA_HOST_URL", "OLLAMA_PROXY_TARGET", "OLLAMA_BASE_URL"):
        val = os.getenv(env, "").strip()
        if val:
            return val
    try:
        from config import system_config  # noqa: PLC0415 - avoid an import cycle

        target = getattr(system_config, "OLLAMA_PROXY_TARGET", "") or ""
        if target:
            return str(target)
    except Exception:  # noqa: BLE001 - config unavailable (tests, tooling)
        pass
    return "http://127.0.0.1:11434"


# /api/ps measured at 0.3 ms locally, so this is affordable on the turn path.
_PS_TIMEOUT_S = 2.0


def _resident_placement(model_tag: str) -> str | None:
    """Where *model_tag* is CURRENTLY loaded: "gpu", "cpu", or None if not loaded.

    Ollama reports ``size_vram`` per loaded model: non-zero means it is on the
    card, zero means it is resident in RAM. Which of the two matters enormously,
    because moving between them is a FULL RELOAD, not a cheap adjustment.
    """
    resp = httpx.get(_ollama_url().rstrip("/") + "/api/ps", timeout=_PS_TIMEOUT_S)
    resp.raise_for_status()
    base = model_tag.split(":")[0]
    for m in resp.json().get("models", []):
        if m.get("name", "").split(":")[0] == base:
            return "gpu" if (m.get("size_vram") or 0) > 0 else "cpu"
    return None


def choose_num_gpu(model_tag: str, *, now: float | None = None) -> int:
    """Layers to offload for this turn: ALL_LAYERS (GPU) or CPU_ONLY.

    TWO RULES, and the first one is the whole lesson:

      1. If the model is ALREADY LOADED, keep it where it is. Flipping
         placement is not an adjustment — Ollama reloads the entire model, and
         a cold CPU load of the tutor was measured at 131 s.

      2. Otherwise prefer the GPU, because it is better cold AND warm:
             GPU   ~5.6 s cold, ~1.4 s warm
             CPU  ~131   s cold, ~13.6 s warm

    WHY THE COOLDOWN DAMPER WAS REMOVED (2026-09-10). The first version backed
    off to CPU for 60 s after each swap, to avoid fighting the co-tenant agent
    for the card. Measured under real concurrent load it made things far worse:
    4 agent + 4 tutor turns took 381 s wall clock, with tutor turns of 162.8 s
    and 157.4 s and one agent turn of 147.1 s. The damper avoided GPU swaps by
    causing PLACEMENT swaps, and a placement swap is a cold reload — strictly
    the more expensive operation. It was designed against a 13.6 s CPU figure
    that had silently assumed the CPU copy was already resident.

    Rule 1 is what actually damps thrash: whoever holds a loaded copy keeps it,
    so neither side can force the other into a reload mid-conversation.

    SNFLWR_GPU_PREFER_CPU=1 inverts rule 2 for an operator who would rather
    protect a co-tenant than the tutor's latency.
    """
    del now  # kept for signature compatibility; the policy is no longer time-based
    try:
        placement = _resident_placement(model_tag)
        if placement == "gpu":
            return ALL_LAYERS
        if placement == "cpu":
            # Loaded on CPU: serving from CPU costs ~13.6 s, forcing it onto the
            # GPU costs a full reload. Keep it.
            return CPU_ONLY

        if os.getenv("SNFLWR_GPU_PREFER_CPU", "").strip() in ("1", "true", "yes"):
            logger.info("gpu_placement: SNFLWR_GPU_PREFER_CPU set, loading on CPU")
            return CPU_ONLY

        logger.info("gpu_placement: not loaded, claiming the GPU for the tutor")
        return ALL_LAYERS
    except Exception as exc:  # noqa: BLE001 - see FAIL-OPEN in the module docstring
        logger.warning(
            "gpu_placement: placement check failed (%s); serving from CPU", exc
        )
        return CPU_ONLY


def apply_to_options(options: dict, model_tag: str) -> dict:
    """Set ``num_gpu`` on an Ollama options dict unless the caller pinned it.

    An explicit caller-supplied ``num_gpu`` always wins: an operator or a test
    asking for a specific placement should get it.
    """
    if "num_gpu" in options:
        return options
    options["num_gpu"] = choose_num_gpu(model_tag)
    return options
