"""THE list of canned replies a child can receive instead of tutoring.

One place, enumerated from the code that emits each string. Nothing here is
re-typed, because every copy of these strings has drifted from its original at
least once.

WHY THIS MODULE EXISTS

A canned reply is not a tutor turn. Any instrument that scores one as tutoring
reports a false green, and that has now happened four times in this codebase:

  1. `scripts/latency_bar.py` carried a hand-written list containing
     "too many requests" while the rate limiter actually serves _BUSY_MESSAGE,
     so 5 of 6 rate-limited probes were counted as real latency measurements
     (fixed in #306).
  2. `api/exception_handlers.py` re-typed _BUSY_MESSAGE instead of importing it,
     byte-identical until someone edited the original (fixed in #310).
  3. A certification runner's sentinel list omitted _WITHHOLDING_FALLBACK -- on
     the run whose A3 bar IS the withholding-fallback rate.
  4. ⭐ The SAFETY PIPELINE's refusals were in nobody's list at all. The
     2026-09-24 re-certification served 9 of them in 212 turns and recorded
     `sentinel = None` for every one; they surfaced only because 4 happened to
     be under 80 characters and tripped a length check. The other 5 would have
     gone to human raters as genuine tutoring.

`tests/test_latency_bar_sentinels.py` claimed to enumerate "every named constant
that can reach a child as a chat reply" and had never heard of `safety/`. That
is the gap this module closes: there is now one list, and it is derived, so a
new canned reply cannot hide from it.

WHAT COUNTS AS A CANNED REPLY

Any fixed string served to a child in place of a freely-composed tutor answer:

  * infrastructure  -- busy, timeout, unsupported box, no profile
  * pedagogy        -- the withholding fallback (a deliberate teaching choice)
  * safety          -- every category's refusal, both the input-side
                       (`get_safe_response`) and output-side (`_output_fallback`)
                       wordings, which differ

An instrument must decide per class how to treat them; it must never fail to
RECOGNISE them. `SCORED_AS_PRODUCT` records which are deliberate product
behaviour (and so belong in a denominator) versus infrastructure failure
(unmeasured either way).
"""

from __future__ import annotations

from typing import Dict, Tuple


def infrastructure_replies() -> Dict[str, str]:
    """Canned replies meaning "the tutor never ran"."""
    from api.routes.ollama_proxy.chat import (
        _BUSY_MESSAGE,
        _TIMEOUT_MESSAGE,
        _UNSUPPORTED_MESSAGE,
    )
    from core.profile_gate import NO_PROFILE_MESSAGE

    return {
        "busy": _BUSY_MESSAGE,
        "timeout": _TIMEOUT_MESSAGE,
        "unsupported": _UNSUPPORTED_MESSAGE,
        "no_profile": NO_PROFILE_MESSAGE,
    }


def pedagogy_replies() -> Dict[str, str]:
    """Canned replies that are a deliberate teaching decision."""
    from core.pedagogy.guidance_enforcer import _WITHHOLDING_FALLBACK

    return {"withholding": _WITHHOLDING_FALLBACK}


def safety_replies() -> Dict[str, str]:
    """Every safety refusal, ENUMERATED by calling the emitters.

    Both wordings are collected because both reach children and they differ:
    `get_safe_response` (input-side, usually with a follow-up question) and
    `_output_fallback` (output-side, terser). Keyed by category and side.
    """
    from safety.pipeline import SafetyPipeline
    from safety.pipeline.models import Category, SafetyResult, Severity

    pipeline = SafetyPipeline()
    out: Dict[str, str] = {}
    for category in Category:
        blocked = SafetyResult(
            is_safe=False,
            severity=Severity.MAJOR,
            category=category,
            reason="enumeration",
            triggered_keywords=(),
            suggested_redirection="",
            stage="enumeration",
        )
        inbound = pipeline.get_safe_response(blocked)
        if inbound:
            out[f"safety_in:{category.value}"] = inbound
        outbound = SafetyPipeline._output_fallback(category)
        if outbound:
            out[f"safety_out:{category.value}"] = outbound
    return out


def all_canned_replies() -> Dict[str, str]:
    """Every canned child-facing reply, keyed by a stable name.

    An ImportError here must ABORT the caller. A partial list is worse than no
    list: it looks complete and silently scores canned text as tutoring.
    """
    merged: Dict[str, str] = {}
    merged.update(infrastructure_replies())
    merged.update(pedagogy_replies())
    merged.update(safety_replies())
    if not merged:
        raise RuntimeError(
            "no canned replies enumerated -- refusing to report an empty list, "
            "which any caller would read as 'nothing to look for'"
        )
    return merged


# Which classes are deliberate PRODUCT behaviour, and so belong in a rate's
# denominator, versus infrastructure failure, which is unmeasured either way.
# The distinction decides whether a canned reply is a defect or a datum:
# a withholding fallback IS the A3 bar; a safety refusal IS a child being
# turned away; a busy message is neither.
SCORED_AS_PRODUCT: Tuple[str, ...] = ("withholding", "safety_in", "safety_out")


# Match on a distinctive PREFIX, not the whole string.
#
# Matching the full constant looks stricter and is wrong: it misses any reply
# that was truncated or had its tail edited. Truncation is not hypothetical --
# 30.4% of served replies on 2026-09-20 were cut off mid-sentence, and a cut
# canned reply is still a canned reply. Found while testing this module: a
# shortened withholding fallback returned None under full-string matching.
#
# 40 characters reliably answers "IS this canned?" and does NOT always answer
# "which category?": the 39 strings share only 29 distinct 40-char prefixes,
# because several categories are worded identically (violence and drugs both
# open "I can't help with that topic. Let's focus on..."). That is fine for the
# job this module exists to do -- an instrument must never score canned text as
# tutoring -- and it means the returned NAME is one of possibly several
# matching categories, not a category attribution. Do not build a
# per-category rate on `is_canned`'s return value; use the pipeline's own
# SafetyResult for that.
_PREFIX_LEN = 40


def is_canned(reply: str) -> str | None:
    """The name of the canned reply `reply` contains, or None.

    Longest prefixes are tested first so a specific wording wins over a
    shorter one it happens to contain.
    """
    if not reply:
        return None
    candidates = sorted(all_canned_replies().items(), key=lambda kv: -len(kv[1]))
    for name, text in candidates:
        if text and text[:_PREFIX_LEN] in reply:
            return name
    return None
