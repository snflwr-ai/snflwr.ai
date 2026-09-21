"""The tutor must not invent WHERE things sit in a text it cannot see.

A blind double-rated read of 480 served replies (2026-09-21) found 15 with wrong
content. Five of them -- a third, the largest single class -- were confident,
specific, FALSE claims about a named text:

  * *Hatchet* ch.4 given as "what he finds to eat" and asked "which part of the
    wilderness does Brian find shelter in" -- ch.4 is the Secret plus the
    mosquito attack; he finds neither, and the shelter is ch.6
  * *Charlotte's Web* -- "you know Wilbur's early friendship with Charlotte"
    said to a student who had read two chapters; Charlotte appears ch.4-5
  * *The Raven* -- "bleak December" placed in the first two lines; it opens st.2
  * *The Great Gatsby* -- Nick Carraway "the only honest person in East Egg";
    Nick rents in WEST Egg, next door to Gatsby

Why this class and not the others got a prompt block:

  * A child cannot detect it. Arithmetic they can re-check; which stanza a line
    opens they cannot.
  * A RATER often cannot detect it either -- one declined the *Hatchet* case
    outright for want of the book. So the measured rate is a FLOOR here.
  * It lives inside replies the enforcement pipeline scores as WINS. Two of the
    five refuse the homework correctly and confabulate while doing it. The
    reveal confirm asks "did it hand over the answer"; nothing in the pipeline
    asks "is this true about the book". This is the first defect class found
    that enforcement cannot reach.

These tests do not measure the confabulation rate -- that needs the GPU and the
two-sided probe set (see ~/snflwr-artefacts/2026-09-21-textgrounding/PREREG.md).
They guard the SHAPE of the instruction, which is what rots silently.
"""

import pathlib
import re

import pytest

MODELFILE = pathlib.Path(__file__).resolve().parent.parent / "models" / "Snflwr_AI_Kids.modelfile"


@pytest.fixture(scope="module")
def system_prompt() -> str:
    m = re.search(r'SYSTEM\s+"""(.*?)"""', MODELFILE.read_text(), re.DOTALL)
    assert m, "no SYSTEM block in the tutor Modelfile"
    return m.group(1)


def test_the_text_accuracy_block_exists(system_prompt):
    assert "TEXT ACCURACY" in system_prompt, (
        "The TEXT ACCURACY block is gone. It is the only instruction standing "
        "between the tutor and the largest wrong-content class measured."
    )


def test_it_names_every_locator_the_model_got_wrong(system_prompt):
    """Derived from the four measured failures, not from a hand-picked list.

    Each of these words names a locator the model confabulated in the served
    corpus. A block that stops naming one of them stops covering that case.
    """
    block = system_prompt[system_prompt.index("TEXT ACCURACY"):]
    for locator in ("chapter", "stanza", "page"):
        assert locator in block.lower(), (
            f"the block no longer mentions {locator!r} -- a measured failure "
            "shape is uncovered"
        )


def test_it_forbids_the_PRESUPPOSING_QUESTION_specifically(system_prompt):
    """Two of the five errors were QUESTIONS, not statements.

    "Which part of the wilderness does Brian find shelter in?" asserts nothing
    on its face -- it presupposes. A block that only forbids assertions would
    have passed both of those replies, so this is not redundant with the rule
    above.
    """
    block = system_prompt[system_prompt.index("TEXT ACCURACY"):].lower()
    assert "assume" in block or "presuppos" in block, (
        "the block must forbid QUESTIONS that assume unverified text content; "
        "two of the five measured errors were questions"
    )


def test_it_explicitly_refuses_to_become_a_literature_REFUSAL(system_prompt):
    """The failure mode this fix would otherwise introduce.

    1 snflwr reply in 6 was already measured clean-and-useless, and every
    reveal-rate metric scores those as a PASS. A grounding instruction that
    makes the tutor hedge on answerable literature questions trades a hidden
    harm for a visible one and costs the product a subject. The block has to say
    so in its own text, because the next person to edit it will not have read
    the measurement.
    """
    block = system_prompt[system_prompt.index("TEXT ACCURACY"):]
    assert "not a licence to refuse" in block.lower() or "NOT a licence" in block, (
        "the block must state that it is not a licence to refuse literature"
    )
    # and it must still name the things the tutor SHOULD teach in full
    for keep in ("theme", "symbolism", "narrator"):
        assert keep in block.lower(), (
            f"the block no longer names {keep!r} as something to teach fully; "
            "without the positive list it reads as a blanket hedge"
        )


def test_it_gives_the_model_a_ROUTE_and_not_only_a_prohibition(system_prompt):
    """A prohibition with no alternative gets worked around or ignored.

    The route ("ask what happens in the part in front of you, then work with
    what they tell you") is also strictly better teaching and cannot be wrong,
    which is why it is the instruction rather than a bare "do not".
    """
    block = system_prompt[system_prompt.index("TEXT ACCURACY"):].lower()
    assert "in front of" in block or "ask them what" in block, (
        "the block must tell the model what to do INSTEAD, not only what to avoid"
    )


def test_the_branch_gate_comes_BEFORE_both_verdict_sections(system_prompt):
    """Three of the 15 errors told a CORRECT student they were wrong.

    w424: "the math in your first step is not right. Check your squares again"
    -- the student's 194 is exactly 169+25; their real error was adding instead
    of subtracting. w130 says "your constant is not correct" and then contradicts
    itself two sentences later.

    The prompt had a branch for CORRECT and a branch for INCORRECT and nothing
    that said to work out which one applied. The gate only functions if the model
    reads it before either branch, so position is the test.
    """
    gate = system_prompt.find("WHICH OF THE NEXT TWO SECTIONS")
    incorrect = system_prompt.find("WHEN A STUDENT IS INCORRECT")
    correct = system_prompt.find("WHEN A STUDENT IS CORRECT")
    assert gate != -1, "the branch-selection gate is gone"
    assert incorrect != -1 and correct != -1
    assert gate < incorrect < correct, (
        "the gate must precede BOTH verdict sections; a gate placed after them "
        "is read too late to change the branch"
    )


def test_the_gate_names_the_arithmetic_right_method_wrong_case(system_prompt):
    """The exact shape of the measured failure.

    In all three cases the student's arithmetic was correct and the METHOD was
    the problem, and the tutor sent them back to recheck correct arithmetic. A
    gate that only said "check first" would not have caught that.
    """
    gate = system_prompt[system_prompt.find("WHICH OF THE NEXT TWO SECTIONS"):]
    gate = gate[:gate.find("WHEN A STUDENT IS CORRECT")].lower()
    assert "method" in gate, "the gate must name the method-wrong case"
    assert "recheck" in gate or "check" in gate


def test_the_gate_does_not_soften_the_existing_be_accurate_rule(system_prompt):
    """Guard against the obvious over-correction.

    The prompt already forbids softening a wrong answer into a compliment,
    because a student told wrong answers are "interesting thinking" cannot tell
    when they are right. A gate that made the model hedge on real errors would
    reintroduce exactly that harm from the other direction.
    """
    assert "that's not right" in system_prompt.lower()
    assert "without softening it into a compliment" in system_prompt
