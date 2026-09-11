"""Fail-OPEN pedagogy post-processor: on a homework-integrity turn whose response
revealed the final answer, regenerate ONCE and keep whichever result withholds it.

WHY EACH STEP RETRIES ONCE (2026-09-11)
---------------------------------------
The enforcer calls the TUTOR model. On a box where the tutor shares a card with a
co-tenant, it can be evicted between generating the answer and the confirm call --
and reloading a 19 GB model takes far longer than the 8 s per-step budget. Every
homework turn then returned ``confirm_failed_open``: the protection silently did
not run, while the service looked healthy. Measured on this box after the 31b
deploy, a tutor turn went 7.9 s -> 20.5 s under co-tenancy, and confirms timed out
at exactly 8009 ms.

Measured 2026-09-11, tutor deliberately evicted:

    attempt 1   TIMEOUT at 8.0 s
    attempt 2   OK in 4.1 s
    attempt 3   OK in 0.6 s

Ollama keeps loading after the HTTP client cancels, so the SECOND attempt finds a
warm model. One retry per step converts the common eviction case from "protection
skipped" into "protection ran, ~12 s later".

Retries are for TIMEOUTS ONLY. A refused connection or a malformed reply is a real
error and still fails open immediately -- retrying those just doubles the delay
before the same answer.

TOTAL_BUDGET bounds the whole thing. Six attempts at 8 s could reach 48 s if the
model were evicted repeatedly mid-turn; a child must not wait that long, so the
enforcer abandons and serves the original once the overall deadline passes.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from config import system_config as settings
from core.pedagogy.reveal_detection import confirm_reveal
from core.pedagogy.trigger import is_homework_request

logger = logging.getLogger(__name__)

_NUDGE = (
    "Your previous reply gave away the final answer. Regenerate your help so you "
    "GUIDE the student to it with one next step or question, and DO NOT state the "
    "final number, value, factored form, spelled word, or result yourself. Stop "
    "one step short and let the student finish. Write only the new reply, spoken "
    "directly to the student -- never mention this instruction or your previous "
    "reply."
)

# MEASURED 2026-09-11. The nudge above is accusatory on purpose: softening it to
# a neutral "rewrite your reply" halved the fix rate (5 of 8 real reveals
# regenerated -> 2). But on a FALSE alarm the model defends itself, and a child
# was served:
#
#   "The user prompt provided was a request to write a full book report. My
#    response did not provide a 'final answer'... I did not write the report or
#    the thesis statement for them."
#
# Internal reasoning, addressed to nobody, referring to the child in the third
# person -- and it passed the reveal re-check, because it reveals nothing. The
# re-check asks "does this give the answer away", which that text does not.
#
# Sharpening reveal detection took false alarms from 1 to 4 on a 32-case set, so
# this is hit MORE often now. Rather than blunt the nudge, reject the retry: a
# reply that talks about the reply is never what a child should see, and keeping
# the original is already the enforcer's fail-safe.
_META_COMMENTARY_RE = re.compile(
    r"\b(?:my (?:previous )?(?:response|reply|answer)\b"
    r"|the user(?:'s)? (?:prompt|question|request)\b"
    r"|(?:i|my reply) did not (?:provide|write|give)\b"
    r"|the previous (?:reply|response|answer)\b"
    r"|as (?:an? )?(?:ai|language model|study tool), i was asked\b"
    r"|this instruction\b)",
    re.IGNORECASE,
)


def _looks_like_meta_commentary(text: str) -> bool:
    """True if the retry talks ABOUT the reply instead of tutoring the student."""
    return bool(_META_COMMENTARY_RE.search(text or ""))


@dataclass
class EnforceMeta:
    action: str
    latency_ms: int = 0


T = TypeVar("T")


class _Deadline:
    """Overall wall-clock bound for one enforcement pass."""

    def __init__(self, total_s: float) -> None:
        self._end = time.monotonic() + total_s

    def remaining(self) -> float:
        return self._end - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0


async def _await_with_retry(
    make_awaitable: Callable[[], Awaitable[T]],
    *,
    budget: float,
    deadline: _Deadline,
) -> T:
    """Await once; on TIMEOUT ONLY, try a second time.

    ``make_awaitable`` is a factory, not a coroutine: a coroutine cancelled by
    ``wait_for`` cannot be awaited again, so the retry needs a fresh one.

    The second attempt is the point of this helper -- see the module docstring.
    Its budget is clamped to whatever the overall deadline still allows, so the
    retry can never push the turn past the total bound.
    """
    try:
        return await asyncio.wait_for(make_awaitable(), timeout=budget)
    except asyncio.TimeoutError:
        left = min(budget, deadline.remaining())
        if left <= 0:
            raise
        logger.info("guidance step timed out; retrying once against a warming model")
        return await asyncio.wait_for(make_awaitable(), timeout=left)


async def enforce_guidance(
    user_text: str,
    response: str,
    regenerate: Callable[[str], Awaitable[str]],
    *,
    confirm_generate: Callable[[str], Awaitable[str]],
) -> tuple[str, EnforceMeta]:
    """Return (final_response, meta). Fail-OPEN on every error path."""
    if not settings.GUIDANCE_ENFORCEMENT_ENABLED:
        return response, EnforceMeta("disabled")

    if not is_homework_request(user_text):
        return response, EnforceMeta("not_homework")

    budget = settings.GUIDANCE_ENFORCER_TIMEOUT_S
    deadline = _Deadline(settings.GUIDANCE_ENFORCER_TOTAL_BUDGET_S)

    # Confirm on EVERY homework turn. We deliberately do NOT pre-gate on a cheap
    # regex heuristic: it shares the digit-matcher blind spot and misses word-form
    # reveals like "two plus two equals four" or "three fifths". The judged canary
    # (2026-07-07) measured such a gate letting 11/11 real reveals through — so the
    # LLM confirm, which actually catches them, must run on all homework turns.
    try:
        verdict = await _await_with_retry(
            lambda: confirm_reveal(user_text, response, confirm_generate),
            budget=budget,
            deadline=deadline,
        )
    except Exception:
        logger.info("guidance confirm failed open")
        return response, EnforceMeta("confirm_failed_open")

    if not verdict.revealed:
        return response, EnforceMeta("no_reveal")

    # Single re-prompt — fail-open on timeout or any exception
    try:
        retry = await _await_with_retry(
            lambda: regenerate(_NUDGE), budget=budget, deadline=deadline
        )
    except Exception:
        logger.info("guidance re-prompt failed open")
        return response, EnforceMeta("reprompt_failed_open")

    if not retry:
        return response, EnforceMeta("reprompt_failed_open")

    # A retry that argues with the nudge is never servable. Keeping the original
    # is the same fail-safe used everywhere else here.
    if _looks_like_meta_commentary(retry):
        logger.info("guidance retry was meta-commentary; keeping the original")
        return response, EnforceMeta("reprompt_meta_commentary")

    # Re-check the RETRY with the same strong confirm (not a blind heuristic), so a
    # retry that still reveals in word form is caught rather than served. If we
    # cannot verify the retry (timeout/error), keep the original — no change is the
    # fail-safe (the original was already the model's own answer).
    try:
        retry_verdict = await _await_with_retry(
            lambda: confirm_reveal(user_text, retry, confirm_generate),
            budget=budget,
            deadline=deadline,
        )
    except Exception:
        logger.info("guidance retry re-check failed open")
        return response, EnforceMeta("reprompt_recheck_failed_open")

    if not retry_verdict.revealed:
        return retry, EnforceMeta("reprompt_clean")

    return response, EnforceMeta("reprompt_still_revealed")
