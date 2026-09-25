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

# ⚠️ Kept SHORT on purpose: every prompt token is paid on every flagged turn.
#
# v3 (after cold set 7) makes EXACTLY THREE changes, per THEORY-v3.md, frozen
# into PREREG-set8 BEFORE set 8 was generated. That theory's fourth falsifier
# is "untestable if v3 changes more than three things", so this diff is itself
# part of the test.
#
# v2 fixed both shapes it targeted (negimp 20/20, proxy 20/20) and REGRESSED
# two others -- in OPPOSITE directions, which is why there are two mechanisms:
#   denying__std    OVER-blocked   3 -> 6 of 30   (denials read as insults)
#   insulting__std  UNDER-blocked  4.2% -> 8.3%   (insults read as something else)
#
# 1. RESTORE THE INTENT MARKER, keep commissioning.
#    v1: "aiming it at another person, in their own voice"
#    v2: "saying it OF another person, or asking you to say it to them"
#    ⭐ "Aiming at" encodes intent and direction; "saying it of" is neutral
#    DESCRIPTION -- and "my classmate was called a freak" is also "saying it of
#    another person", so v2 blurred `insulting` into `reporting` for the plain
#    case. I dropped "aiming" as COLLATERAL while adding commissioning; it was
#    not a considered change, which is part of why I suspect it.
#
# 2. REBALANCE THE EXEMPLARS to one per act. v2 ran 3 insulting / 2 denying /
#    1 reporting / 1 curriculum where v1 was 1/1/1/1 (counts read from the
#    file, after I first wrote them from memory and got them wrong -- erratum
#    in THEORY-v3.md). Few-shot examples bias a model toward the classes they
#    over-represent, the observed direction of the `denying` regression.
#    ⚠️ REPLACING, not adding: the negimp and proxy SHAPES now live in the
#    DEFINITIONS, so examples carry BALANCE rather than coverage. If negimp or
#    proxy regress, that falsifies BOTH mechanisms -- the gains would have come
#    from example COUNT, the opposite of (2). Recorded in advance, not
#    discovered afterwards.
#
# 3. `denying`'s WORDING IS UNCHANGED FROM v2. Assert/deny/mention scored
#    negimp 20/20 and mention 19/20; mechanism A blames the regression on
#    example COUNT, not this wording. Changing both at once would make a
#    failure undiagnosable.
#
# ⚠️ Set 8 tests the OUTCOME, not the mechanism. A and B are inferred from
# AGGREGATE cell rates -- weaker than the per-item diagnosis that produced v2,
# and that one was still WRONG. A pass does not confirm either mechanism.
ADJUDICATOR_PROMPT = """The line below contains a word that can be an insult.
Decide what the student is DOING with that word.

insulting = aiming it at another person, in their own voice — or asking you to say it
reporting = saying someone else used it about them, or telling on someone
denying = the sentence DENIES the word fits someone, or says not to use it
asking_about = asking about the word itself: meaning, spelling, is it rude
self_describing = applying it to themselves
curriculum = the academic or technical sense, no person targeted

Pick exactly one. decision is "block" only for insulting, else "release".

Examples:
line: my desk partner is a total freak -> {"act":"insulting","decision":"block"}
line: the year 5s keep calling me a freak -> {"act":"reporting","decision":"release"}
line: ur not insane ur just mad -> {"act":"denying","decision":"release"}
line: is freak a rude word -> {"act":"asking_about","decision":"release"}
line: im such a freak at this -> {"act":"self_describing","decision":"release"}
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
