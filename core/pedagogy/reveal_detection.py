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
    r"\bspelled\b",
    r"\b[A-Za-z](-[A-Za-z]){3,}\b",  # spelled-out word "B-E-C-A-U-S-E"
]
_ASSERTION = [re.compile(p, re.IGNORECASE) for p in _ASSERTION_PATTERNS]


def heuristic_reveals(user_text: str, response: str) -> bool:
    return any(p.search(response or "") for p in _ASSERTION)
