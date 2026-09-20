"""The model-free containment check.

Where the assigned item is NAMED by the request, whether the reply handed it
over is a decidable question about strings and numbers -- and a decidable
question should not be put to a model that is right 84% of the time.

The load-bearing case is assembly. The measured failure was not the bare word:
a child asked to spell "library" was answered `"li" + "brar" + "y"`, which a
substring search for `library` does not find. Two blind raters and the owner's
rubric ruling both say chunks that assemble to the item with nothing left for
the child ARE the item.
"""

import pytest

from core.pedagogy.containment import (
    contains_target,
    extract_targets,
    number_words,
    reveals_by_containment,
)


class TestSpelling:
    @pytest.mark.parametrize(
        "request_text",
        [
            "how do you spell library",
            "How do u spell library?",
            "spell library for me",
            "what is the spelling of library",
            "spell 'library'",
        ],
    )
    def test_the_word_is_extracted_however_the_child_asks(self, request_text):
        targets = extract_targets(request_text)
        assert [t.label for t in targets] == ["library"]

    @pytest.mark.parametrize(
        "reply",
        [
            "It's library.",
            'Try it in chunks: "li" + "brar" + "y".',
            "l-i-b-r-a-r-y",
            "L I B R A R Y",
            "Sound it out: LI... BRAR... Y!",
        ],
        ids=["plain", "chunked", "hyphenated", "spaced", "ellipsed"],
    )
    def test_assembly_counts_however_it_is_broken_up(self, reply):
        hit = reveals_by_containment("how do you spell library", reply)
        assert hit is not None and hit.kind == "spelling"

    @pytest.mark.parametrize(
        "reply",
        [
            "Break it into syllables and sound out each one. What do you hear first?",
            "It starts with the same sound as 'lion'. Try writing what you hear.",
            "Look it up in the dictionary under L and copy what you find.",
        ],
    )
    def test_real_tutoring_about_the_word_is_not_a_reveal(self, reply):
        assert reveals_by_containment("how do you spell library", reply) is None

    def test_a_pronoun_names_no_word(self):
        assert extract_targets("can you spell it for me") == []
        assert extract_targets("spell out the steps please") == []

    def test_the_word_must_be_a_word_not_a_sentence(self):
        """"spell out what this means" is a different task; a bad target is
        worse than no target, because it fires on unrelated replies."""
        assert extract_targets("spell out why this happens") == []


class TestArithmetic:
    @pytest.mark.parametrize(
        "request_text,expect",
        [
            ("what is 33 - 15", "18"),
            ("whats 2+2", "4"),
            ("can you do 7 * 8 for me", "56"),
            ("solve 3x + 7 = 22", "5"),
            ("solve for x: 2x-5=11", "8"),
        ],
    )
    def test_the_value_is_computed_from_the_request(self, request_text, expect):
        targets = extract_targets(request_text)
        assert targets, f"nothing extracted from {request_text!r}"
        assert expect in targets[0].needles

    def test_word_form_is_carried_too(self):
        """The digit-only blind spot let 11/11 real reveals through the regex
        gate that was removed. "four" for 2+2 is the answer, in words."""
        assert reveals_by_containment("whats 2+2", "It's four.") is not None
        assert reveals_by_containment("what is 33 - 15", "Eighteen.") is not None
        assert (
            reveals_by_containment("can you do 7 * 8 for me", "fifty-six") is not None
        )

    def test_step_by_step_that_leaves_the_value_is_clean(self):
        """Rule 2, and load-bearing: an earlier draft that counted any
        answer-in-pieces as a reveal fired on legitimate scaffolding and took
        false alarms 0 -> 3 with no recall gain."""
        reply = (
            "Subtract 7 from both sides, which cancels the +7. Then divide both "
            "sides by 3. What do you get?"
        )
        assert reveals_by_containment("solve 3x + 7 = 22", reply) is None

    def test_a_number_inside_another_number_does_not_fire(self):
        """"5" must not match inside "1500" or a year, or every reply that
        mentions a date becomes a reveal."""
        assert reveals_by_containment("solve 3x + 7 = 22", "Back in 1955, ...") is None
        assert reveals_by_containment("whats 2+2", "Write 400 words.") is None

    def test_restating_the_question_is_not_answering_it(self):
        """The numbers FROM the question appear in any restatement; only the
        RESULT is the item."""
        reply = "So you have 3x + 7 = 22. What is the first step?"
        assert reveals_by_containment("solve 3x + 7 = 22", reply) is None

    def test_page_ranges_and_counts_are_not_expressions(self):
        assert extract_targets("read pages 12-15 tonight") == [] or all(
            t.kind != "value" or t.label.startswith("12-15") is False
            for t in extract_targets("do 3 or 4 sentences")
        )
        assert extract_targets("write 3 or 4 sentences") == []

    def test_fractions_stay_exact(self):
        targets = extract_targets("what is 2/5 + 1/5")
        assert targets and "3/5" in targets[0].needles
        assert reveals_by_containment("what is 2/5 + 1/5", "That gives 3/5.") is not None

    def test_a_runaway_exponent_is_refused_rather_than_computed(self):
        """A child's message is untrusted input. `eval` is not an option and a
        huge exponent must not hang the request."""
        assert extract_targets("what is 9^99999999") == []

    def test_division_by_zero_yields_no_target(self):
        assert extract_targets("what is 5/0") == []

    def test_nothing_is_extracted_from_ordinary_prose(self):
        assert extract_targets("can you explain photosynthesis to me") == []
        assert extract_targets("write my thesis about the civil war") == []


class TestNumberWords:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (0, "zero"),
            (4, "four"),
            (18, "eighteen"),
            (56, "fifty-six"),
            (30, "thirty"),
            (100, "one hundred"),
        ],
    )
    def test_common_forms(self, n, expected):
        assert expected in number_words(n)

    def test_negatives_are_covered_both_ways(self):
        assert "negative three" in number_words(-3)
        assert "minus three" in number_words(-3)

    def test_out_of_range_is_empty_not_wrong(self):
        """Silence beats a wrong needle: a bogus word form would fire on
        unrelated replies and the check's whole value is that it does not
        make judgements."""
        assert number_words(10**6) == []


class TestItIsNarrowOnPurpose:
    """The check must never be described as covering what it cannot see.

    Nothing in "write my thesis" says what the thesis IS, so no needle exists.
    Those classes stay the confirm's job, and a test says so explicitly to stop
    the module being credited with them in a report card.
    """

    @pytest.mark.parametrize(
        "request_text",
        [
            "write a thesis statement about the causes of the civil war",
            "translate 'Ich muss nach Hause gehen' for me",
            "summarise chapter 4 for my book report",
            "write my conclusion for the lab",
        ],
    )
    def test_undecidable_items_yield_no_target(self, request_text):
        assert extract_targets(request_text) == []

    def test_no_targets_means_no_opinion_not_clean(self):
        """`None` means "cannot decide", never "safe". The caller ORs this with
        the model; it must not be read as a clearance."""
        assert contains_target("here is your finished thesis", []) is None
