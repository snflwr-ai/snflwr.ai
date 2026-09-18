"""Find the largest context window this machine can actually serve.

WHY A PROBE AND NOT A FORMULA
-----------------------------
Measured on the reference deployment (RTX 3090 Ti, 22.5 GiB usable, 31b tutor):

    num_ctx 16384 -> loads, ~21.0 GiB of card in use
    num_ctx 24576 -> loads, ~21.6 GiB
    num_ctx 32768 -> out of memory, and the model unloads

Arithmetic said 32768 should fit: 21.6 GiB plus another 0.6 GiB of KV is under
22.5. It died anyway, because the transient compute buffers an engine allocates
during a forward pass are not in the resident figure. A context window sized by
formula is therefore a promise the card has not made.

So the probe loads the model at a candidate window and makes it answer. The
largest window that both loads AND produces a token is the one this machine can
serve. It runs at install time, where a few minutes cost nothing, and its result
is written to configuration rather than recomputed on every start.

THE CAP
-------
A window is only offered if a tutoring run has validated it for that backbone.
`CertifiedBackbone.max_validated_num_ctx` carries that number, and `choose()`
never returns more. Bigger is not automatically better: a larger window changes
the KV layout and gives the model more room to run past the guided shape, which
is a quality question, and quality questions are answered by measurement here.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Iterable, Optional, Protocol

logger = logging.getLogger(__name__)

# Tried largest-first. Kept coarse on purpose: the difference between 24k and
# 26k is not worth a minute of install time, and each step costs a model load.
DEFAULT_CANDIDATES: tuple[int, ...] = (32768, 24576, 16384, 8192, 4096)

# A probe load must both fit AND answer; an engine that returns an empty string
# has not proven it can serve a turn.
PROBE_PROMPT = "Hello"
PROBE_TIMEOUT_S = 300.0


class ContextEngine(Protocol):
    """What the probe needs from an engine."""

    def try_context(
        self, model: str, num_ctx: int, timeout_s: Optional[float] = None
    ) -> bool: ...


class OllamaContextEngine:
    """Loads a model at a given window and checks that it answers."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def try_context(
        self, model: str, num_ctx: int, timeout_s: Optional[float] = None
    ) -> bool:
        body = {
            "model": model,
            "stream": False,
            "think": False,
            "options": {"num_ctx": num_ctx},
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
        }
        request = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_s or PROBE_TIMEOUT_S
            ) as resp:
                payload = json.load(resp)
        except Exception as exc:  # noqa: BLE001 - an OOM arrives as an HTTP 500
            logger.info("context probe: num_ctx=%d did not load (%s)", num_ctx, exc)
            return False
        message = payload.get("message") if isinstance(payload, dict) else None
        text = message.get("content", "") if isinstance(message, dict) else ""
        if not text.strip():
            logger.info(
                "context probe: num_ctx=%d loaded but answered nothing", num_ctx
            )
            return False
        logger.info("context probe: num_ctx=%d serves", num_ctx)
        return True


def probe(
    engine: ContextEngine,
    model: str,
    candidates: Iterable[int] = DEFAULT_CANDIDATES,
    timeout_s: Optional[float] = None,
) -> Optional[int]:
    """Largest candidate window this machine serves, or None if none do."""
    for num_ctx in sorted(set(candidates), reverse=True):
        try:
            ok = engine.try_context(model, num_ctx, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - an unreachable engine is not a fit
            logger.warning(
                "context probe: engine error at num_ctx=%d (%s)", num_ctx, exc
            )
            return None
        if ok:
            return num_ctx
    return None


def choose(probed: Optional[int], validated_max: int, unprobed: int) -> int:
    """The window to serve: what the card holds, capped at what was validated.

    `unprobed` is what an un-probed box gets, and it is deliberately NOT
    `validated_max`. The two were the same number until a validation run raised
    the ceiling above the sealed window; after that, falling back to the ceiling
    would hand a window to a card whose footprint at that window nobody has
    measured -- which is how you OOM a machine that never ran the probe. The
    sealed window is the one with a measured footprint, so it is the fallback.
    """
    if probed is None:
        return min(unprobed, validated_max)
    return min(probed, validated_max)
