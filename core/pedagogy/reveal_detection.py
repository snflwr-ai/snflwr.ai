"""LLM confirm stage for the pedagogy enforcer.

`confirm_reveal` asks a model whether a tutor response STATED the final answer to a
homework-integrity task (in digits OR words) rather than guiding the student to it.

A cheap regex pre-gate (`heuristic_reveals`) used to run before this, but the judged
canary (2026-07-07) showed it shared the digit-matcher blind spot and missed word-form
reveals like "two plus two equals four" or "three fifths" — letting 11/11 real reveals
through. It was removed; the enforcer now confirms on every homework turn."""

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class RevealVerdict:
    revealed: bool
    reason: str = ""


_CONFIRM_PROMPT = (
    "A student asked a tutor to do their assigned task; the student must produce the "
    "final answer THEMSELVES. Did the tutor's response STATE the final answer, result, "
    "value, factored form, or spelled word (rather than guiding the student to it)? "
    "Count word-form statements (e.g. saying 'four' for 2+2, 'three fifths', "
    "'fifty-six') as stating the answer.\n\n"
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
