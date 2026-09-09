"""Runtime §1801 screen on tutor output — the net under the persona rewrite.

WHY
---
``evals/tutoring/compliance_canary.py`` grades the persona before deployment and
currently scores it at zero unsafe-feature hits. That is a BUILD-TIME
measurement, of one Modelfile against one base model, on one dataset. A model
swap, a Modelfile edit, a temperature change or ordinary sampling variance can
all reintroduce flattery in production — and nothing would notice. §1801
compliance would decay silently while the last eval still read "100% clean".

This is the runtime net. It reuses ``evals.tutoring.compliance_scorers`` — the
same deterministic screen the offline eval uses — so build time and run time
agree on what a violation is, rather than drifting into two definitions.

WHY A SIBLING OF THE GUIDANCE ENFORCER RATHER THAN PART OF IT
--------------------------------------------------------------
``enforce_guidance`` is homework-scoped and gated on
``GUIDANCE_ENFORCEMENT_ENABLED``. Sycophancy applies to EVERY student turn.
Folding this into the enforcer would widen its trigger — breaking its shipped
invariant of being byte-identical when disabled — and would couple two
independent flags to one switch. Separate module, separate flag, same call site.

FAIL-OPEN — the opposite of the topic gate, on purpose
-------------------------------------------------------
A missed "great job" is a compliance blemish. Refusing to answer a child because
a rewrite call timed out is a broken product. Every error path returns the
ORIGINAL response untouched.

One consequence is deliberate: when a rewrite still contains flattery, the
original is served rather than the rewrite. The original has passed the output
safety pipeline; the rewrite has not been re-vetted, and serving unvetted text to
a child to fix a politeness problem is the wrong trade.

DETECTION IS ALWAYS RECORDED
----------------------------
``EnforceMeta.hits`` carries the violation count even when repair fails or is
skipped. A violation that is silently swallowed is exactly the drift this exists
to surface — the count is what makes it visible in the trace.

OFF BY DEFAULT (``SYCOPHANCY_CHECK_ENABLED``), matching the guidance-enforcer and
topic-gate precedent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Tuple

from config import system_config as settings
from utils.logger import get_logger

logger = get_logger(__name__)

Regenerator = Callable[[str], Awaitable[str]]


@dataclass
class CheckMeta:
    """What happened, for the request trace.

    ``hits`` is the number of §1801 matches in the ORIGINAL response, recorded
    regardless of whether repair succeeded — that is the drift signal.
    """

    action: str
    hits: int = 0
    categories: Tuple[str, ...] = ()


# Kept as an alias so the trace shape matches the guidance enforcer's.
EnforceMeta = CheckMeta


def _scan(text: str) -> Dict[str, List[str]]:
    """Screen output with the same module the offline eval uses.

    Imports from ``safety.compliance_screen``, NOT from ``evals``. The screen
    started life under evals/, which docker/Dockerfile does not copy — so this
    import would have raised inside the fail-open handler below and made the
    whole check a silent no-op in production. That is the identical failure mode
    that hid a missing langfuse. ``safety/`` ships; ``evals/`` does not.
    """
    from safety.compliance_screen import scan

    return scan(text)


def build_nudge(categories: Dict[str, List[str]]) -> str:
    """Ask for one rewrite, quoting the actual offending phrases.

    Naming them matters: a generic "try again" wastes the single regeneration.

    The length-and-warmth clause is not padding. When the persona was first
    rewritten, removing praise made the prose denser and cost 28 points of
    readability across every age band before the guardrail caught it. A nudge
    that just says "remove the flattery" reproduces that regression one response
    at a time.
    """
    quoted = "; ".join(
        f"{name}: " + ", ".join(f'"{phrase}"' for phrase in phrases[:3])
        for name, phrases in sorted(categories.items())
    )
    return (
        "Rewrite your previous answer to remove these phrases and anything like "
        f"them — {quoted}. "
        "Do not praise the student, do not claim feelings or a relationship with "
        "them, and do not refer to earlier sessions. "
        "Keep the same explanation, the same warmth, and the same length and "
        "reading level; say the same thing without the praise. "
        "Reply with only the rewritten answer."
    )


async def check_and_repair(
    response: str,
    *,
    regenerate: Regenerator,
) -> Tuple[str, CheckMeta]:
    """Screen *response*; on a hit, attempt one rewrite. Never raises.

    Returns ``(text_to_serve, meta)``. When disabled or clean, the ORIGINAL
    object is returned so the caller can assert identity, not just equality.
    """
    if not settings.SYCOPHANCY_CHECK_ENABLED:
        return response, CheckMeta("disabled")
    if not (response or "").strip():
        return response, CheckMeta("empty")

    try:
        found = _scan(response)
    except Exception as exc:
        logger.debug("sycophancy screen unavailable (failing open): %s", exc)
        return response, CheckMeta("screen_unavailable")

    if not found:
        return response, CheckMeta("clean")

    hits = sum(len(v) for v in found.values())
    categories = tuple(sorted(found))
    logger.info(
        "Sycophancy screen matched %d phrase(s) in tutor output (%s)",
        hits,
        ", ".join(categories),
    )

    try:
        rewritten = await asyncio.wait_for(
            regenerate(build_nudge(found)),
            timeout=settings.SYCOPHANCY_CHECK_TIMEOUT_S,
        )
    except Exception as exc:
        logger.info("sycophancy repair failed open: %s", exc)
        return response, CheckMeta("repair_failed_open", hits, categories)

    if not (rewritten or "").strip():
        return response, CheckMeta("repair_failed_open", hits, categories)

    # Re-screen. A rewrite that still flatters is worse than the original, which
    # at least cleared the output safety pipeline.
    try:
        if _scan(rewritten):
            logger.info("sycophancy rewrite still matched; serving the original")
            return response, CheckMeta("repair_rejected", hits, categories)
    except Exception:
        return response, CheckMeta("repair_rejected", hits, categories)

    return rewritten, CheckMeta("repaired", hits, categories)
