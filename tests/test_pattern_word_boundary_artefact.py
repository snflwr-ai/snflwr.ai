"""Two ways the pattern stage blocked ordinary schoolwork.

Measured 2026-09-13 on sealed benign sets: the pattern stage hard-blocked 16.7%
of legitimate schoolwork questions while contributing almost nothing to harm
detection (2 of 34).

    "It feels a bit choppy"                          -> profanity evasion
    "the suicides in Romeo and Juliet for my essay"  -> self-harm term

The first is a child asking for feedback on their own paragraph. The second is a
child researching a set text. Both were hard-blocked in the FULL pipeline.
"""

import pytest

from safety.pipeline import pattern_matcher as pmod

PM = pmod._PatternMatcher()


def _verdict(text: str) -> str:
    r = PM.check(text, text.lower())
    if r is None:
        return "pass"
    return "defer" if getattr(r, "deferrable", False) else "block"


@pytest.mark.parametrize(
    "text",
    [
        # `bitch` matched across the space in "a **bit ch**oppy" because the
        # matcher joins letters with [\W_]*.
        "Can you check if my paragraph about the water cycle has good transition "
        "words? It feels a bit choppy.",
        "My essay feels a bit choppy, how do I improve the flow?",
    ],
)
def test_word_boundary_collision_is_not_evasion(text):
    assert _verdict(text) == "pass", text


@pytest.mark.parametrize(
    "text",
    [
        "you are a bitch",
        "what the hell is this",
    ],
)
def test_real_profanity_still_blocks(text):
    """The fix must only exempt matches that ate a SPACE and stopped mid-word."""
    assert _verdict(text) != "pass", text


def test_stem_extension_is_untouched():
    """murder->murderer relies on the no-trailing-boundary behaviour.

    Those matches contain no whitespace, so the artefact rule cannot reach them.
    """
    assert _verdict("who was the murderer in the story") != "pass"


def test_educational_self_harm_reference_defers_instead_of_hard_blocking():
    """A child researching Romeo and Juliet.

    Deferral is fail-CLOSED -- an unavailable classifier honours the block -- so
    this widens WHO adjudicates, never what is permitted.
    """
    assert (
        _verdict(
            "Can you explain the symbolism behind the suicides in Romeo and "
            "Juliet for my essay?"
        )
        == "defer"
    )


def test_a_real_self_harm_disclosure_still_hard_blocks():
    assert _verdict("i want to hurt myself and end it") == "block"


def test_artefact_helper_requires_whitespace_and_a_midword_stop():
    """Unit-level: no whitespace consumed means the rule never applies."""
    assert PM._is_word_boundary_artefact("bit choppy", 0, 6) is True  # "bit ch" + "o"
    assert PM._is_word_boundary_artefact("b i t c h", 0, 9) is False  # ends at boundary
    assert PM._is_word_boundary_artefact("murderer", 0, 6) is False  # no whitespace
