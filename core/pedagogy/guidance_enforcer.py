"""Fail-OPEN pedagogy post-processor: on a homework-integrity turn whose response
revealed the final answer, regenerate ONCE and keep whichever result withholds it."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from config import system_config as settings
from core.pedagogy.reveal_detection import confirm_reveal
from core.pedagogy.trigger import is_homework_request

logger = logging.getLogger(__name__)

_NUDGE = (
    "Your previous reply gave away the final answer. Regenerate your help so you "
    "GUIDE the student to it with one next step or question, and DO NOT state the "
    "final number, value, factored form, spelled word, or result yourself. Stop "
    "one step short and let the student finish."
)


@dataclass
class EnforceMeta:
    action: str
    latency_ms: int = 0


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

    # Confirm on EVERY homework turn. We deliberately do NOT pre-gate on a cheap
    # regex heuristic: it shares the digit-matcher blind spot and misses word-form
    # reveals like "two plus two equals four" or "three fifths". The judged canary
    # (2026-07-07) measured such a gate letting 11/11 real reveals through — so the
    # LLM confirm, which actually catches them, must run on all homework turns.
    try:
        verdict = await asyncio.wait_for(
            confirm_reveal(user_text, response, confirm_generate), timeout=budget
        )
    except Exception:
        logger.info("guidance confirm failed open")
        return response, EnforceMeta("confirm_failed_open")

    if not verdict.revealed:
        return response, EnforceMeta("no_reveal")

    # Single re-prompt — fail-open on timeout or any exception
    try:
        retry = await asyncio.wait_for(regenerate(_NUDGE), timeout=budget)
    except Exception:
        logger.info("guidance re-prompt failed open")
        return response, EnforceMeta("reprompt_failed_open")

    if not retry:
        return response, EnforceMeta("reprompt_failed_open")

    # Re-check the RETRY with the same strong confirm (not a blind heuristic), so a
    # retry that still reveals in word form is caught rather than served. If we
    # cannot verify the retry (timeout/error), keep the original — no change is the
    # fail-safe (the original was already the model's own answer).
    try:
        retry_verdict = await asyncio.wait_for(
            confirm_reveal(user_text, retry, confirm_generate), timeout=budget
        )
    except Exception:
        logger.info("guidance retry re-check failed open")
        return response, EnforceMeta("reprompt_recheck_failed_open")

    if not retry_verdict.revealed:
        return retry, EnforceMeta("reprompt_clean")

    return response, EnforceMeta("reprompt_still_revealed")
