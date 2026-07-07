"""Detect whether a student turn is a request to DO their assigned task
(produce the finished answer) rather than a genuine 'how/why does this work?'.
Heuristic + recall-favoring: a miss just means the enforcer no-ops that turn."""

import re

_PATTERNS = [
    r"just (tell|give) me (the )?(answer|solution|roots?|value)",
    r"just (tell|give) me\b",  # catch-all: "just give me 7x8", "just tell me X"
    r"just the answer",
    r"do my (homework|assignment|essay|paper|worksheet)",
    r"write my (?:\w+ )*(essay|paragraph|report|book report|thesis)",
    r"\b(solve|compute|calculate|factor|spell) (this|it|the|my)\b",
    r"so i can (copy|hand it in|turn it in)",
    r"(don'?t|do not) (want the steps|explain|show me)",
    r"no (working|steps|explanation)",
    r"without (the )?steps",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def is_homework_request(user_text: str) -> bool:
    text = user_text or ""
    return any(p.search(text) for p in _COMPILED)
