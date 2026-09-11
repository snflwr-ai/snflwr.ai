"""Guards for the expanded homework-integrity probe set.

The `answer` token in a homework_integrity case is the whole measurement: the
scorer decides "did the tutor hand the answer over" by looking for it. Two ways
that silently breaks, both of which have already happened in this harness:

  * the token also appears in the QUESTION (or is unavoidable when merely
    discussing the problem), so guiding responses read as reveals -- PR #203
    fixed exactly this, where the "1" in "1/4" matched;
  * the token is phrased so no realistic reveal contains it, making the case
    inert -- it scores a perfect non-reveal forever and measures nothing.

These run without a model, so the probe set cannot rot unnoticed.
"""
from pathlib import Path

import pytest
import yaml

from evals.tutoring import scorers

_PROBES = Path(__file__).resolve().parents[1] / "evals" / "tutoring" / "homework_integrity.yaml"
_CASES = yaml.safe_load(_PROBES.read_text())["cases"]
_IDS = [c["id"] for c in _CASES]


def test_probe_set_is_large_enough_to_decide_something():
    """10 cases could not resolve a 7.9-point gap against an 8.6-point spread."""
    assert len(_CASES) >= 20, "the point of this file is breadth"


def test_ids_are_unique():
    assert len(set(_IDS)) == len(_IDS)


def test_every_case_is_a_homework_probe_with_an_answer():
    for c in _CASES:
        assert c["probe"] == "homework_integrity", c["id"]
        assert c.get("answer"), c["id"]
        assert c["band"] in scorers.AGE_BANDS, c["id"]


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
def test_answer_token_is_not_already_in_the_question(case):
    """A token present in the prompt makes every response look like a reveal."""
    assert not scorers.reveals_answer(case["question"], case["answer"]), (
        f"{case['id']}: answer {case['answer']!r} collides with the question text"
    )


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
def test_a_blatant_reveal_is_actually_detected(case):
    """If this fails the case is inert: it can never register a violation."""
    assert scorers.reveals_answer(f"Sure, the answer is {case['answer']}.", case["answer"]), (
        f"{case['id']}: answer {case['answer']!r} is undetectable, so the case measures nothing"
    )


def test_bands_are_covered():
    """A probe set skewed to one band measures that band, not the product."""
    for band in scorers.AGE_BANDS:
        assert any(c["band"] == band for c in _CASES), f"no cases for band {band}"
