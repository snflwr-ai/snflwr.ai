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
from typing import Awaitable, Callable, Optional, TypeVar

from config import system_config as settings
from core.pedagogy.input_gate import asks_for_assigned_work
from core.pedagogy.reveal_detection import confirm_reveal
from core.pedagogy.trigger import (
    asks_to_produce_for_comparison,
    is_homework_request,
)

logger = logging.getLogger(__name__)

_NUDGE = (
    "Your previous reply gave away the final answer. Regenerate your help so you "
    "GUIDE the student to it with one next step or question, and DO NOT state the "
    "final number, value, factored form, spelled word, or result yourself. "
    # The list above is numeric and word-shaped, which leaves the model with no
    # target to stop short of when the assignment is a DEFINITION, a COMPARISON,
    # a LIST or a TRANSLATION. Measured on sealed set 9: every one of the five
    # turns that exhausted its rewrites and fell back to static text was this
    # class, and the rewrites kept supplying the content because nothing told
    # them what to withhold.
    "When the assignment is to DEFINE, COMPARE, LIST, NAME, TRANSLATE, DERIVE or "
    "EXPLAIN something, the thing to withhold is THAT: do not give the definition, "
    "the comparison, the list, the name, the translation, the derivation or the "
    "explanation itself. Give the surrounding facts, the way in, or a worked "
    "example on DIFFERENT material, and ask the student to supply the item. "
    "Naming a related fact they must still use is fine; handing them the item is "
    "not. "
    "Stop "
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


# Served when a reveal is confirmed and no rewrite could remove it. Static, so it
# can never itself reveal. Deliberately age-neutral: it is a redirect, not an
# explanation, so it reads acceptably from K-2 to 9-12.
_WITHHOLDING_FALLBACK = (
    "I want you to get this one yourself \u2014 that is how it sticks. "
    "Which part is tricky? Tell me the step where it stops making sense and "
    "we will work through that bit together."
)


def _nudge_for(item: str) -> str:
    """The nudge, naming the assigned item when the confirm identified it.

    The generic nudge lists CATEGORIES to withhold (definition, comparison,
    list, name, translation...). That list exists because of a measured
    failure: on sealed set 9 every turn that exhausted its rewrites and fell
    back to static text was one of those tasks, and "the rewrites kept
    supplying the content because nothing told them what to withhold".
    Categories were the first fix. Naming the ACTUAL item is the next, and it is
    free: the certified confirm prompts already return an `item` field, present
    in 100% of flagged verdicts, and it was being discarded.

    The item is bounded and flattened upstream (`_clean_item`) because it is
    model text derived from a child's message and is being pasted into a prompt.
    An empty item falls back to the generic nudge -- never to no nudge.
    """
    if not item:
        return _NUDGE
    return (
        f"Your previous reply gave away the item the student was asked to "
        f"produce: {item}. Do NOT state it, spell it, gloss it, or hand it over "
        f"in pieces the student only has to put together. " + _NUDGE
    )


def _looks_like_meta_commentary(text: str) -> bool:
    """True if the retry talks ABOUT the reply instead of tutoring the student."""
    return bool(_META_COMMENTARY_RE.search(text or ""))


@dataclass
class EnforceMeta:
    action: str
    latency_ms: int = 0
    # Rewrites tried before this outcome. Kept OUT of `action` on purpose: the
    # action names are a stable vocabulary that the canary and the proxy trace
    # both read, so the attempt count rides alongside rather than mangling them.
    attempts: int = 0
    # WHICH gate decided this turn: the LLM gate, or the regex fallback.
    #
    # These are not equivalent, and the difference was invisible until it was
    # measured. On a blind holdout (2026-09-21, 40 stuck / 20 demand / 20 dodge
    # turns, ~/snflwr-artefacts/2026-09-21-stuckchild-holdout/):
    #
    #                        LLM gate (e4b)   regex fallback
    #   stuck false alarms        0/40             2/40
    #   demand caught            20/20            16/20
    #   DODGE caught             18/20 = 90%      13/20 = 65%   <- fails the bar
    #
    # A "dodge" is a real demand for the assigned deliverable with a refusal or
    # learning frame bolted on ("i wanna lern. wats the ansers on my paper").
    # On the fallback path, 35% of them get the child's homework done for them.
    #
    # The fallback was logged at INFO and counted NOWHERE, so a deployment
    # running mostly on the weak path would look identical in every metric the
    # product has. That is the shape this project has already been bitten by --
    # a downgraded classifier runs, says "fine", and passes the bad output
    # through. Recording it here puts the decision path in the same telemetry as
    # every other enforcement outcome, so "how often are we on the weak gate?"
    # becomes answerable instead of a guess.
    #
    # Cold start is the realistic trigger: the gate model's first call after it
    # goes idle measured 29.9s against a 6s timeout, while steady state is
    # p50 0.43s. One idle period is enough to put a turn on the regex.
    gate: str = (
        "none"  # "llm" | "regex_fallback" | "none" (enforcement off//not reached)
    )


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


# Terminal punctuation that ends a servable reply. Includes the closers a
# sentence can end inside: a quoted line, a parenthetical, a bold run.
_SENTENCE_END = re.compile(r"[.!?](?=[\s\"'\)\*\]]*\Z)")
# A sentence boundary anywhere in the text: terminal punctuation followed by
# whitespace. "0.5 plus" is not a boundary because the dot is followed by a
# digit, which is why the lookahead requires space rather than any non-digit.
_SENTENCE_BOUNDARY = re.compile(r"[.!?][\"'\)\*\]]*(?=\s)")

# A trim must leave a real tutoring turn behind. Below either floor the rewrite
# is mostly incomplete sentence, and a truncated-but-whole answer beats a stub.
_TRIM_MIN_CHARS = 80
_TRIM_MIN_RATIO = 0.6


def _repair_truncation(text: str) -> str:
    """Trim a rewrite that stopped mid-sentence back to its last complete one.

    Measured on sealed set 10 (2026-09-13): **4 of 12 rewrites (33%)** ended
    mid-word -- one simply stopped on "Similarly,". Not a token budget:
    ``num_predict`` is 4096+ while the cut replies ran 570-812 characters and
    uncut ones reached 1120, so the model emits a stop early on the second
    generation. This cannot be prevented from here, but a child should not be
    shown the dangling fragment.

    Only ever REMOVES text, and runs AFTER the reveal re-check, so it cannot
    turn a cleared reply into a revealing one.
    """
    if not text:
        return text
    body = text.rstrip()
    if not body or _SENTENCE_END.search(body):
        return text  # already ends cleanly
    matches = list(_SENTENCE_BOUNDARY.finditer(body))
    if not matches:
        return text
    trimmed = body[: matches[-1].end()].rstrip()
    if len(trimmed) < _TRIM_MIN_CHARS or len(trimmed) < _TRIM_MIN_RATIO * len(body):
        return text
    return trimmed


async def enforce_guidance(
    user_text: str,
    response: str,
    regenerate: Callable[[str], Awaitable[str]],
    *,
    confirm_generate: Callable[[str], Awaitable[str]],
    gate_generate: Optional[Callable[[str], Awaitable[str]]] = None,
) -> tuple[str, EnforceMeta]:
    """Return (final_response, meta). Fail-OPEN on every error path."""
    if not settings.GUIDANCE_ENFORCEMENT_ENABLED:
        return response, EnforceMeta("disabled")

    # The LLM gate's verdict decides when it can answer; the regex is the
    # fallback for when it cannot. They are deliberately NOT OR'd together --
    # measured on a sealed set, OR gives the best recall (93.1%) and the worst
    # false-positive rate (21.1%), failing the bar fixed before the run.
    gate_verdict: Optional[bool] = None
    if gate_generate is not None:
        gate_verdict = await asks_for_assigned_work(user_text, gate_generate)
    triggered = is_homework_request(user_text) if gate_verdict is None else gate_verdict
    # Which gate actually decided. `gate_generate is None` means the LLM gate is
    # not configured at all; a None verdict with it configured means it was
    # reached for and could not answer -- unreachable, timed out, or unparseable.
    # Only the second is a degradation, and the two must not be conflated.
    gate_used = (
        "none"
        if gate_generate is None
        else ("regex_fallback" if gate_verdict is None else "llm")
    )
    if gate_used == "regex_fallback":
        # WARNING, not INFO: this is a measured drop in capability (dodge catch
        # 90% -> 65% on a blind holdout), not a routine event. It was previously
        # invisible to every metric the product has.
        logger.warning(
            "guidance: LLM input gate gave no verdict; decided on the REGEX "
            "fallback, which catches 65%% of framed demands vs the gate's 90%%"
        )

    # ONE narrow override of a negative gate verdict: a demand to produce the work
    # wearing a verification frame ("solve it from scratch so I can see if my
    # algebra matches yours"). Measured on sealed set 11, where the gate returned
    # NO on 12 dodge turns: this rescues 5 of them at ZERO cost on 58 genuine
    # turns, moving recall 90.1% -> 94.2%.
    #
    # This is NOT the rejected full OR (93.1% recall / 21.1% false positives). The
    # override is a single high-precision shape with its own measured FP rate,
    # which is the only reason it is allowed to outrank the gate.
    if not triggered and asks_to_produce_for_comparison(user_text):
        logger.info("guidance: gate said no; produce-to-compare override fired")
        triggered = True

    if not triggered:
        return response, EnforceMeta("not_homework", gate=gate_used)

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
        # FAIL CLOSED, not open. Measured on sealed set 11: 2 of 179 turns hit this,
        # and one of them (a worksheet asking the student to list the parts of a
        # flower) served the complete assigned answer because nothing checked it.
        #
        # The original fail-open reasoning was "we do not know the answer is bad, and
        # a tutor that raises is a child staring at an error". The first half no
        # longer holds here: we are on this path only because the gate already ruled
        # the turn a homework demand, so the prior is that an unchecked answer is
        # exactly the thing to withhold. The second half never applied -- a canned
        # tutoring turn is not an error, which is the same reasoning the exhausted-
        # retries branch below already uses.
        #
        # Cost is bounded and measured: fail-open fired on 1.1% of turns, so routing
        # those to the fallback moves the fallback rate ~4.1% -> ~5.2%, inside the
        # 10% bar. The failure mode becomes a duller answer instead of a revealed one.
        logger.info(
            "guidance confirm unavailable; withholding rather than serving unchecked"
        )
        return _WITHHOLDING_FALLBACK, EnforceMeta(
            "confirm_failed_closed", gate=gate_used
        )

    if not verdict.revealed:
        return response, EnforceMeta("no_reveal", gate=gate_used)

    # ---- A reveal is now CONFIRMED. From here the original must not ship. -----
    #
    # Up to MAX_REGEN_ATTEMPTS rewrites, each re-checked. Measured 2026-09-11:
    # a single attempt repaired 4-5 of 8 real reveals across two identical runs
    # (the regeneration is nondeterministic), leaving 3 of 4 residual failures as
    # `reprompt_still_revealed` -- detection worked, the REWRITE did not. More
    # attempts are the direct lever on that.
    attempts = 0
    for _ in range(max(1, settings.GUIDANCE_ENFORCER_MAX_REGEN_ATTEMPTS)):
        if deadline.expired():
            break
        attempts += 1
        try:
            retry = await _await_with_retry(
                lambda: regenerate(_nudge_for(verdict.item)),
                budget=budget,
                deadline=deadline,
            )
        except Exception:
            logger.info("guidance re-prompt failed")
            break

        if not retry or _looks_like_meta_commentary(retry):
            # A reply that talks about the reply is never servable; try again.
            continue

        try:
            retry_verdict = await _await_with_retry(
                lambda: confirm_reveal(user_text, retry, confirm_generate),
                budget=budget,
                deadline=deadline,
            )
        except Exception:
            logger.info("guidance retry re-check failed")
            break

        if not retry_verdict.revealed:
            # Trim a dangling final fragment. After the re-check on purpose:
            # trimming only removes text, so the cleared verdict still holds.
            return _repair_truncation(retry), EnforceMeta(
                "reprompt_clean", attempts=attempts, gate=gate_used
            )

    # Every rewrite failed, or we ran out of budget. The ORIGINAL IS KNOWN TO
    # REVEAL -- the confirm said so -- so serving it is the one outcome this
    # module exists to prevent. Serve a static withholding turn instead.
    #
    # This is NOT a retreat from fail-open. Fail-open governs the cases where we
    # could not CHECK (timeout, unreachable model, unparseable verdict): there we
    # still serve the model's own answer, because we do not know it is bad. Here
    # we do know. The original docstring's reason for fail-open was that "a tutor
    # that raises is a child staring at an error" -- a canned tutoring turn is not
    # an error, so that reasoning does not extend to this branch.
    #
    # Cost: on a FALSE alarm whose rewrites all keep tripping the confirm, a child
    # gets a generic prompt instead of a good answer. Measured false-alarm rate
    # after #240 is 4 of 32 homework turns, and rewrites usually pass, so this is
    # rare -- and the failure is a duller answer, never a handed-over one.
    logger.info("guidance could not withhold the answer; serving the safe fallback")
    return _WITHHOLDING_FALLBACK, EnforceMeta(
        "fallback_served", attempts=attempts, gate=gate_used
    )
