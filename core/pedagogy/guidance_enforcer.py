"""Fail-OPEN pedagogy post-processor: on a homework-integrity turn whose response
revealed the final answer, regenerate ONCE and keep whichever result withholds it."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from config import system_config as settings
from core.pedagogy.reveal_detection import confirm_reveal, heuristic_reveals
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

    if not heuristic_reveals(user_text, response):
        return response, EnforceMeta("pass_gate")

    budget = settings.GUIDANCE_ENFORCER_TIMEOUT_S

    # LLM confirm stage — fail-open on timeout or any exception
    try:
        verdict = await asyncio.wait_for(
            confirm_reveal(user_text, response, confirm_generate), timeout=budget
        )
    except Exception:
        logger.info("guidance confirm failed open")
        return response, EnforceMeta("confirm_failed_open")

    if not verdict.revealed:
        return response, EnforceMeta("pass_confirm")

    # Single re-prompt — fail-open on timeout or any exception
    try:
        retry = await asyncio.wait_for(regenerate(_NUDGE), timeout=budget)
    except Exception:
        logger.info("guidance re-prompt failed open")
        return response, EnforceMeta("reprompt_failed_open")

    if retry and not heuristic_reveals(user_text, retry):
        return retry, EnforceMeta("reprompt_clean")

    return response, EnforceMeta("reprompt_still_revealed")
