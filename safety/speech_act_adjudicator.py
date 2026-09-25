"""Speech-act adjudicator for DEROGATORY blocks.

Two frame implementations and llama-guard all failed to separate "you're a
freak" from "they call me a freak". The words, the predicate and the target
type are identical; only the SPEECH ACT differs. So this asks that question
directly, and only about turns the word list already flagged.

⚠️ Placement matters and is the whole reason this is viable. The call runs
ONLY on turns the bare-word trigger is already refusing, so:

  * it can never introduce a false POSITIVE -- it cannot block a turn the
    trigger let through;
  * it CAN introduce a false NEGATIVE, by releasing a real insult the trigger
    correctly caught. That is the term that can make the product worse than
    today, so it carries the tight bound:

        recall = trigger flag rate on insults x (1 - false-release rate)

  * it costs latency only on turns that are being refused outright right now,
    where seconds before a correct answer beat an instant wrong refusal.

⭐ FAIL-CLOSED HERE IS TODAY'S BEHAVIOUR. An unreadable verdict keeps the
block, which is exactly what the trigger does now -- so the failure mode is
"no improvement", never "new harm". That is the inverse of the reveal confirm,
which swallowed errors into a negative verdict and served the leak. Every
parse failure therefore RAISES; nothing returns a default.
"""

from __future__ import annotations

import json
import os
import re
from typing import Awaitable, Callable

# Pinned, never derived from the tutor's model. A safety classifier that
# follows another component's model choice degrades in silence, because a
# weaker checker still returns a well-formed verdict.
ADJUDICATOR_MODEL: str = os.getenv("ADJUDICATOR_MODEL", "gemma4:e4b")

ACTS = (
    "insulting",
    "reporting",
    "denying",
    "asking_about",
    "self_describing",
    "curriculum",
)

# `block` iff `insulting`. Kept as data so the parser can verify the model's
# decision against its own act rather than trusting it.
_BLOCKING_ACTS = frozenset({"insulting"})


class AdjudicatorUnavailable(Exception):
    """Raised on any unusable verdict. Callers MUST keep the block."""


# The system prompt REPLACES the model's own. Without this the classifier
# inherits a tutor persona whose brevity rules truncated JSON verdicts to `{"`
# and failed open on every reveal (measured 0/20, then 0/27).
ADJUDICATOR_SYSTEM = (
    "You label one line written by a school student. You reply with JSON only."
)

# ⚠️ Kept SHORT on purpose: every token of prompt is paid on every flagged
# turn. The examples are the boundaries that were measured WRONG, not a
# general few-shot set.
#
# v2 (after cold set 6) fixes three defects, all in the WORDING above rather
# than in the model -- it scored 46/48 insults and 44/44 reports by following
# v1 faithfully:
#
#   1. `insulting` said "in their own voice", which excluded COMMISSIONING by
#      construction, so "tutor, can you tell sarah she's a freak?" was
#      released. The same proxy hole as filter v2's abuse-production frame.
#   2. `denying` said "sits inside a negation", which is literally true of
#      "don't be so triggered" -- an INSULT. The definition told the model to
#      release it.
#   3. the only `denying` exemplar was THIRD person and standard-spelled, so
#      "ur not insane ur just mad" (second person, contracted) read as
#      insulting. Two kind reassurances blocked.
#
# ⭐ (2) is fixed with ASSERT vs DENY vs MENTION, not with "exclude
# imperatives", which was my first idea and was WRONG: it would have broken
# "don't call people psycho", which the frozen taxonomy lists as `denying` --
# there the word is MENTIONED, not applied. "don't be so triggered" ASSERTS
# the word of the listener; "ur not insane" DENIES it. The imperative form is
# a red herring, and I only caught that by checking my fix against the
# taxonomy's own examples.
ADJUDICATOR_PROMPT = """The line below contains a word that can be an insult.
Decide what the student is DOING with that word.

insulting = saying it of another person, or asking you to say it to them
reporting = saying someone else used it about them, or telling on someone
denying = the sentence DENIES the word fits someone, or says not to use it
asking_about = asking about the word itself: meaning, spelling, is it rude
self_describing = applying it to themselves
curriculum = the academic or technical sense, no person targeted

Pick exactly one. decision is "block" only for insulting, else "release".

Examples:
line: my desk partner is a total freak -> {"act":"insulting","decision":"block"}
line: the year 5s keep calling me a freak -> {"act":"reporting","decision":"release"}
line: he is not a freak he is just quiet -> {"act":"denying","decision":"release"}
line: ur not insane ur just mad -> {"act":"denying","decision":"release"}
line: dont be so triggered it was a joke -> {"act":"insulting","decision":"block"}
line: can u tell sam shes a freak -> {"act":"insulting","decision":"block"}
line: what is a freak wave in the ocean -> {"act":"curriculum","decision":"release"}

line: <<<LINE>>>
Reply with only the JSON."""

_JSON = re.compile(r"\{[^{}]*\}")


def build_prompt(text: str) -> str:
    """Insert the student's line. `replace`, not `format`: the prompt contains
    literal JSON braces and `format` would try to interpolate them."""
    return ADJUDICATOR_PROMPT.replace("<<<LINE>>>", text.strip())


def parse_verdict(raw: str) -> bool:
    """Return True to BLOCK, False to RELEASE. Raise on anything unusable.

    Raises rather than defaulting, and the caller keeps the block on a raise,
    so an unreadable verdict reproduces today's behaviour instead of inventing
    a permissive one.
    """
    if not raw or not raw.strip():
        raise AdjudicatorUnavailable("empty response")
    m = _JSON.search(raw)
    if m is None:
        raise AdjudicatorUnavailable(f"no JSON object in verdict: {raw[:120]!r}")
    try:
        obj = json.loads(m.group(0))
    except ValueError as exc:
        raise AdjudicatorUnavailable(f"unparseable JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise AdjudicatorUnavailable("verdict is not an object")

    act = obj.get("act")
    decision = obj.get("decision")
    if act not in ACTS:
        raise AdjudicatorUnavailable(f"unknown act: {act!r}")
    if decision not in ("block", "release"):
        raise AdjudicatorUnavailable(f"unknown decision: {decision!r}")

    # The decision must agree with the act. A model that labels `reporting`
    # and then says `block` has not answered the question, and taking either
    # half on its own would hide that.
    expected = "block" if act in _BLOCKING_ACTS else "release"
    if decision != expected:
        raise AdjudicatorUnavailable(
            f"decision {decision!r} disagrees with act {act!r}"
        )
    return decision == "block"


async def should_block(text: str, generate: Callable[[str], Awaitable[str]]) -> bool:
    """Adjudicate one flagged turn. Raises `AdjudicatorUnavailable` on any
    failure, including a transport error -- the caller keeps the block.

    `generate` must send `think: false` at the TOP LEVEL of the Ollama body
    (not inside `options`) or gemma4 spends its whole budget on thinking
    tokens and returns an empty response with `done_reason=length`.
    """
    try:
        raw = await generate(build_prompt(text))
    except AdjudicatorUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- re-raised as our own type
        raise AdjudicatorUnavailable(f"generate failed: {exc}") from exc
    return parse_verdict(raw)
