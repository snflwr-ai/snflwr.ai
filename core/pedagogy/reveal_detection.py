"""Detect whether a tutor response ASSERTS a final answer (recall-favoring gate).
A hit triggers the LLM confirm, never an action directly."""

import re

_ASSERTION_PATTERNS = [
    r"\bthe (final )?(answer|result|solution) (is|=|equals)\b",
    r"\bthe roots? (are|is)\b",
    r"=\s*[-+(]?\s*[\dxX]",  # "x = 5", "= 1", "= (x"
    r"\bequals?\s+[-+(]?\s*\d",  # "equals 4"
    r"\bis\s+[-+]?\d+\.?\d*\s*[.!]",  # "...is 56."
    r"\byou get\s+[-+]?\d",
    r"\bfactors? (as|to|into)\b",  # factored form handed over
    # FP-1: bare \bspelled\b removed — fires on guiding phrases like
    # "How is the word spelled?" / "You spelled that wrong".
    # Actual spelled-out forms are caught by the hyphenated-letter pattern below.
    r"\b[A-Za-z](-[A-Za-z]){3,}\b",  # spelled-out word "B-E-C-A-U-S-E"
    # FP-2: natural reveal phrasings ("gives us 3/5", "comes out to 7", "end up with 9")
    r"\bgives (us|you)\s+[-+]?\d",
    r"\bcomes out to\s+[-+]?\d",
    r"\bends? up with\s+[-+]?\d",
]
_ASSERTION = [re.compile(p, re.IGNORECASE) for p in _ASSERTION_PATTERNS]


def heuristic_reveals(
    user_text: str,  # reserved for Task 4 confirm_reveal(); not used in this heuristic stage
    response: str,
) -> bool:
    return any(p.search(response or "") for p in _ASSERTION)


# ---------------------------------------------------------------------------
# Task 4: LLM confirm stage
# ---------------------------------------------------------------------------
import json
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class RevealVerdict:
    revealed: bool
    reason: str = ""


_CONFIRM_PROMPT = (
    "A student asked a tutor to do their assigned task; the student must produce the "
    "final answer THEMSELVES. Did the tutor's response STATE the final answer, result, "
    "value, factored form, or spelled word (rather than guiding the student to it)?\n\n"
    "STUDENT: {question}\nTUTOR: {response}\n\n"
    'Reply with ONLY JSON: {{"revealed": true}} or {{"revealed": false}}.'
)


def _parse_verdict(raw: str) -> RevealVerdict:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return RevealVerdict(False, "parse_error")  # fail-open
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return RevealVerdict(False, "parse_error")
    revealed = data.get("revealed")
    return RevealVerdict(bool(revealed) if isinstance(revealed, bool) else False)


async def confirm_reveal(
    user_text: str,
    response: str,
    generate: Callable[[str], Awaitable[str]],
) -> RevealVerdict:
    prompt = _CONFIRM_PROMPT.format(question=user_text, response=response)
    try:
        raw = await generate(prompt)
    except Exception:
        return RevealVerdict(False, "confirm_error")  # fail-open
    return _parse_verdict(raw)
