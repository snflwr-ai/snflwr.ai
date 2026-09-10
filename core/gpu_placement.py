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
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Ollama's "offload every layer" sentinel. The real layer count is smaller.
ALL_LAYERS = 999
CPU_ONLY = 0

# Minimum seconds between two swaps the TUTOR causes. Bounds swap rate no matter
# how requests interleave. Lower = snappier tutor, more eviction churn for both
# services; higher = more turns served from CPU at the old latency.
SWAP_COOLDOWN_S = float(os.getenv("SNFLWR_GPU_SWAP_COOLDOWN_S", "60"))


# Where the last-swap timestamp lives. Deliberately a plain file: this is a hint,
# not a lock, and it must survive a worker restart without a daemon.
#
# Under APP_DATA_DIR, NOT /tmp. A predictable name in a world-writable directory
# lets any local account pre-create or symlink it (bandit B108), and this file
# steers placement — a planted timestamp could hold the tutor on CPU forever.
# Same finding class as the notify spool. APP_DATA_DIR is the directory this app
# already owns for state (the database lives there), so it inherits its perms.
def _default_state_path() -> Path:
    try:
        from config import system_config  # noqa: PLC0415 - avoid an import cycle

        return Path(system_config.APP_DATA_DIR) / "gpu-last-swap"
    except Exception:  # noqa: BLE001 - config unavailable (tests, tooling)
        return Path(__file__).resolve().parent.parent / "data" / "gpu-last-swap"


# NB: check the env STRING before constructing a Path. `Path("")` is `Path(".")`,
# which is truthy, so `Path(os.getenv(...)) or default` silently resolves to the
# current directory instead of falling through to the default.
_ENV_STATE = os.getenv("SNFLWR_GPU_STATE", "").strip()
STATE_PATH = Path(_ENV_STATE) if _ENV_STATE else _default_state_path()

_OLLAMA = os.getenv("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
# /api/ps measured at 0.3 ms locally, so this is affordable on the turn path.
_PS_TIMEOUT_S = 2.0


def _resident_on_gpu(model_tag: str) -> bool:
    """True if *model_tag* is already loaded with VRAM assigned.

    A model that is resident costs nothing to keep using — no swap, no eviction
    — so this case bypasses the cooldown entirely.
    """
    # httpx, not urllib.request.urlopen: urlopen honours whatever scheme the URL
    # carries, so an OLLAMA_HOST_URL of file:///... would read a local file
    # (bandit B310). httpx speaks HTTP only, and it is already this codebase's
    # HTTP client everywhere else.
    resp = httpx.get(_OLLAMA.rstrip("/") + "/api/ps", timeout=_PS_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    base = model_tag.split(":")[0]
    for m in data.get("models", []):
        if m.get("name", "").split(":")[0] == base:
            return (m.get("size_vram") or 0) > 0
    return False


def _last_swap_at() -> float:
    try:
        return float(STATE_PATH.read_text().strip())
    except Exception:  # noqa: BLE001 - absent/corrupt state means "never"
        return 0.0


def _record_swap(now: float) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(str(now))
    except OSError as exc:
        # Losing the timestamp only weakens the damper; it must never fail a turn.
        logger.warning("gpu_placement: could not record swap time (%s)", exc)


def _safe_tag(model_tag: str) -> str:
    """Make a model tag safe to log.

    The proxy passes the CLIENT-SUPPLIED ``body["model"]`` straight through to
    placement, so this string is attacker-controlled. Logging it raw lets a
    request forge log lines (CodeQL log-injection) — the same class as the
    X-Request-ID log-forging closed in L1. Strip anything that could break out
    of a single log record, and bound the length.
    """
    cleaned = "".join(c for c in str(model_tag) if c.isprintable() and c not in "\r\n")
    return cleaned[:64] or "<unnamed>"


def choose_num_gpu(model_tag: str, *, now: float | None = None) -> int:
    """Layers to offload for this turn: ALL_LAYERS (GPU) or CPU_ONLY.

    Three rules, in order:
      1. already GPU-resident  -> GPU. Free, no swap, cooldown irrelevant.
      2. inside the cooldown   -> CPU. This is the damper.
      3. otherwise             -> GPU, and start a new cooldown.
    """
    now = time.time() if now is None else now
    try:
        if _resident_on_gpu(model_tag):
            return ALL_LAYERS

        if now - _last_swap_at() < SWAP_COOLDOWN_S:
            logger.debug(
                "gpu_placement: within %.0fs cooldown, serving %s from CPU",
                SWAP_COOLDOWN_S,
                _safe_tag(model_tag),
            )
            return CPU_ONLY

        _record_swap(now)
        logger.info("gpu_placement: claiming the GPU for %s", _safe_tag(model_tag))
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
