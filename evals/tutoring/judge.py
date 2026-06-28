"""
LLM-as-judge for the open-ended tutoring-quality dimensions that deterministic
scorers can't measure: correctness, pedagogy (guide don't tell), age-fit, and
tone. The judge backend is pluggable — the default talks to a local Ollama
model (privacy-aligned for an offline-first product); a stronger external judge
(e.g. Claude) can be slotted in by passing a different `generate` callable.

The prompt builder and response parser are pure and unit-tested; only the
network call to the backend is non-deterministic.
"""

import json
import re

# Each rubric dimension is scored 0 (poor) / 1 (partial) / 2 (good).
RUBRIC_DIMENSIONS = ("correctness", "pedagogy", "age_fit", "tone")
_MAX_PER_DIM = 2

_RUBRIC_TEXT = """\
Score each dimension as an integer 0, 1, or 2:

- correctness: Is the explanation factually/mathematically accurate? \
(2 = fully correct, 1 = minor error, 0 = wrong/misleading)
- pedagogy: Does it teach by guiding the student to think (questions, hints, \
steps) rather than just handing over the answer? (2 = strongly guiding, \
1 = some guidance, 0 = just states the answer or unhelpful)
- age_fit: Is the vocabulary, length, and complexity appropriate for the \
target age band? (2 = well-matched, 1 = somewhat off, 0 = clearly wrong level)
- tone: Is it warm, encouraging, and on-topic for the subject being tutored? \
(2 = excellent, 1 = neutral, 0 = cold/off-topic/inappropriate)
"""


def build_judge_prompt(case: dict, response: str) -> str:
    """Build the judge prompt for one (case, response) pair."""
    band = case.get("band", "?")
    ages = {
        "K-2": "ages 5-7", "3-5": "ages 8-10",
        "6-8": "ages 11-13", "9-12": "ages 14-18",
    }.get(band, band)
    return f"""\
You are an expert K-12 education evaluator grading an AI tutor's response.

Target student: grade band {band} ({ages}).
Subject: {case.get('subject', 'general')}

STUDENT QUESTION:
{case.get('question', '')}

TUTOR RESPONSE:
{response}

{_RUBRIC_TEXT}
Reply with ONLY a JSON object of the form:
{{"correctness": <0-2>, "pedagogy": <0-2>, "age_fit": <0-2>, "tone": <0-2>, "rationale": "<one sentence>"}}
"""


def parse_judge_response(raw: str) -> dict:
    """Extract rubric scores from the judge's reply.

    Tolerant of JSON embedded in prose. Clamps scores to 0..2. Missing
    dimensions become None. Returns parse_error=True if no JSON is found."""
    result = {dim: None for dim in RUBRIC_DIMENSIONS}
    result["rationale"] = None
    result["parse_error"] = False

    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        result["parse_error"] = True
        return result
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        result["parse_error"] = True
        return result

    for dim in RUBRIC_DIMENSIONS:
        if dim in data and isinstance(data[dim], (int, float)):
            result[dim] = max(0, min(_MAX_PER_DIM, int(data[dim])))
    if isinstance(data.get("rationale"), str):
        result["rationale"] = data["rationale"]
    return result


def judge_score_0_100(scores: dict):
    """Aggregate the present rubric dimensions into a 0..100 score.

    Ignores None dimensions; returns None if no dimension was scored."""
    present = [scores.get(dim) for dim in RUBRIC_DIMENSIONS if isinstance(scores.get(dim), int)]
    if not present:
        return None
    return round(100.0 * sum(present) / (_MAX_PER_DIM * len(present)), 1)


def judge_case(case: dict, response: str, generate) -> dict:
    """Run the judge for one case using a `generate(prompt) -> str` backend.

    `generate` is injected so the backend (local Ollama, Claude, a stub) is
    the caller's choice. Returns the parsed scores plus the 0..100 aggregate."""
    raw = generate(build_judge_prompt(case, response))
    scores = parse_judge_response(raw)
    scores["judge_0_100"] = judge_score_0_100(scores)
    return scores
