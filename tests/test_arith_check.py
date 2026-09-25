"""The deterministic arithmetic check on check-my-work turns (core.pedagogy.arith_check).

Pinned from the live run of 2026-09-25, where the tutor told children that
"8 / 4 = 4", "84 / 12 = 6", "72 + 6 = 76", "9/12 - 2/12 = 6/12" and
"21 + 15 = 35" were correct. The contract has three halves and all three matter:

  1. a wrong step is found;
  2. correct work is NEVER flagged (a calculator that calls right work wrong is a
     defect by construction, so this is a zero bar, not a rate);
  3. the note NEVER contains the correct value, and there is NO note -- in
     particular no "your arithmetic is correct" -- when nothing is wrong or
     nothing parses. Wrong answers with correct arithmetic and a wrong METHOD
     are common, and an "arithmetic is correct" note would push the tutor to
     affirm them.
"""

from fractions import Fraction

import pytest

from core.pedagogy.arith_check import build_note, check_shown_arithmetic, wrong_steps

# The five affirmed-wrong cases from the live run, verbatim.
AFFIRMED_WRONG = [
    ("assignment problem 4: find the slope between (1, 3) and (5, 11). i did "
     "(11 - 3) / (5 - 1) = 8 / 4 = 4 so the slope is 4. right?", "8 / 4 = 4"),
    ("worksheet q7 is 3/4 - 1/6. common denominator is 12 so 9/12 - 2/12 = 6/12 "
     "= 1/2. is that right? i simplified it at the end", "9/12 - 2/12 = 6/12"),
    ("science worksheet: find the density of a rock with mass 84 g and volume 12 "
     "cm3. i did 84 / 12 = 6 so 6 g/cm3. is that right?", "84 / 12 = 6"),
    ("homework question 2: concert tickets are $18 each. we need 4 tickets plus a "
     "$6 fee. whats the total? i did 18 x 4 = 72 and 72 + 6 = 76", "72 + 6 = 76"),
    ("science hw: we measured rain as 12 mm on monday, 9 mm on tuesday and 15 mm "
     "on wednesday. total? i did 12 + 9 = 21 and 21 + 15 = 35 mm", "21 + 15 = 35"),
]

# Wrong steps whose detection depends on one parsing rule each. A test of CORRECT
# work cannot pin these: if the rule breaks, the step stops parsing and "no
# wrong steps" still passes. Found by mutation testing.
WRONG_BY_RULE = [
    ("worksheet question 5: 6 in a box, 9 boxes. i said 6 x 9 = 56 so 56 pencils", "6 * 9 = 56"),
    ("7x8=54 on my sheet right??", "7 * 8 = 54"),
    # a sentence-ending period is not a decimal point
    ("i did 50 + 20 = 70 and 6 + 7 = 13 so 70 + 13 = 73. is that right?", "70 + 13 = 73"),
]

CORRECT_WORK = [
    "worksheet problem 5 is 47 + 38. i did 40 + 30 = 70 and 7 + 8 = 15 so 70 + 15 = 85. is that right?",
    "homework problem 3: sam has $20 and buys a book for $7.50. i did 20 - 7.50 = 12.50 so $12.50. is it right?",
    "worksheet q9 is 2/3 + 1/4. so 8/12 + 3/12 = 11/12. is that right?",
    "hw number 12: find 30% of 250. i did 0.3 x 250 = 75. did i do it right?",
    "assignment problem 3: slope between (2, 5) and (6, 17). (17 - 5) / (6 - 2) = 12 / 4 = 3. right?",
    "personal finance: 15 x 22 = 330, 330 x 0.2 = 66, 330 - 66 = 264",
    "i got 3.33 for 10 / 3 = 3.33 is that right",          # rounding to the places written
    "7x8=56 on my sheet right??",                          # digit-x-digit is multiplication
    "1,200 + 300 = 1,500 on my homework",                  # thousands separators
    "10 ÷ 4 = 2.5 and 3 × 4 = 12",               # unicode operators
]

# Wrong answers the checker must NOT touch: correct arithmetic, wrong method.
METHOD_ERRORS = [
    "problem 3 hw: solve 4x + 5 = 29. i did 29 + 5 = 34 and then 34 / 4 = 8.5 so x = 8.5. is that right?",
    "physics homework: force 56 N, mass 8 kg, find acceleration. i did 56 x 8 = 448 so 448 m/s2. right?",
]

NOTHING_TO_PARSE = [
    "is the capital of australia sydney?",
    "i think x = 5 is that right",                          # no computation on the left
    "4x + 5 = 29 so x = 6?",                                # a variable, not arithmetic
    "",
]


@pytest.mark.parametrize("text,step", AFFIRMED_WRONG)
def test_the_measured_affirmed_wrong_cases_are_flagged(text, step):
    bad = wrong_steps(text)
    assert bad, text
    assert f"{bad[0].expr} = {bad[0].claimed}" == step


@pytest.mark.parametrize("text,step", WRONG_BY_RULE)
def test_wrong_steps_that_depend_on_one_parsing_rule(text, step):
    bad = wrong_steps(text)
    assert bad and f"{bad[0].expr} = {bad[0].claimed}" == step, (text, bad)


@pytest.mark.parametrize("text", CORRECT_WORK)
def test_correct_work_is_never_flagged(text):
    assert wrong_steps(text) == [], text
    assert build_note(text) is None


@pytest.mark.parametrize("text", METHOD_ERRORS)
def test_method_errors_produce_no_note_at_all(text):
    # Arithmetic correct, method wrong: the checker cannot see this, so it must
    # stay silent -- never "the arithmetic is correct".
    assert build_note(text) is None


@pytest.mark.parametrize("text", NOTHING_TO_PARSE)
def test_unparseable_text_produces_no_note(text):
    assert check_shown_arithmetic(text) == []
    assert build_note(text) is None


@pytest.mark.parametrize("text,step", AFFIRMED_WRONG)
def test_the_note_never_contains_the_correct_value(text, step):
    note = build_note(text)
    expr = step.split(" = ")[0]
    from core.pedagogy.arith_check import _evaluate, _normalise

    right = _evaluate(_normalise(expr))
    assert isinstance(right, Fraction)
    shown = {str(right), f"{float(right):g}"}
    if right.denominator != 1:
        shown.add(f"{right.numerator}/{right.denominator}")
    # Strip the student's own step, which the note quotes, before looking.
    rest = note.replace(step, "")
    for v in shown:
        assert v not in rest.split(), (v, note)


def test_the_note_says_what_it_did_not_check():
    note = build_note(AFFIRMED_WRONG[0][0])
    assert "not whether they are the right steps" in note


def test_no_eval_on_student_text():
    # The evaluator is a hand-written parser; arbitrary text must not execute.
    assert check_shown_arithmetic("__import__('os').system('true') = 1") == []
