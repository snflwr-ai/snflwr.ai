"""A third of enforcement rewrites stopped mid-sentence.

Sealed set 10 (2026-09-13): 4 of 12 `reprompt_clean` replies ended mid-word --
one simply stopped on "Similarly,". Not a token budget (`num_predict` is 4096+
while the cut replies ran 570-812 chars and uncut ones reached 1120), so the
model emits a stop early on the second generation. The fragment lands on exactly
the students the enforcer intervened for.
"""

import pytest

from core.pedagogy.guidance_enforcer import _repair_truncation


class TestRepairTruncation:
    def test_dangling_fragment_is_trimmed_to_the_last_whole_sentence(self):
        text = (
            "The causes are often grouped by the acronym MAIN. Each letter is a "
            "systemic tension. Look at how nations behaved regarding their armies "
            "and their loyalties. In WWI a similar concept involves how countries "
            "viewed their own ethnic identity and right to self-govern. Similarly,"
        )
        out = _repair_truncation(text)
        assert out.endswith("right to self-govern.")
        assert "Similarly," not in out

    def test_a_reply_that_already_ends_cleanly_is_untouched(self):
        for text in (
            "Squaring means multiplying a number by itself. What is that total?",
            "Try it and tell me what you get!",
            'She calls it "the annex."',
            "Start with the first three lines (the prologue).",
        ):
            assert _repair_truncation(text) == text

    def test_decimal_point_is_not_a_sentence_boundary(self):
        # "0.5 plus" must not be read as a sentence end, or a maths answer would
        # be trimmed to nonsense.
        text = "Line up the points so 0.5 becomes 0.50 and then add it to 0.25 and"
        assert _repair_truncation(text) == text

    def test_mostly_fragment_is_left_alone(self):
        # Below the floors a trim leaves a stub, and a truncated-but-whole answer
        # beats a stub -- the same reasoning as the withholding fallback.
        assert _repair_truncation("Hi. then a long dangling clause that never ends") == (
            "Hi. then a long dangling clause that never ends"
        )

    def test_no_sentence_boundary_at_all_is_left_alone(self):
        text = "an unterminated clause with no stop anywhere in it at all"
        assert _repair_truncation(text) == text

    @pytest.mark.parametrize("text", ["", None])
    def test_empty_inputs_pass_through(self, text):
        assert _repair_truncation(text) == text

    def test_only_ever_removes_text(self):
        # The repair runs AFTER the reveal re-check, so it must never be able to
        # introduce content the confirm did not clear.
        for text in [
            "A complete sentence. Then a dangling",
            "Done.",
            "No punctuation here",
        ]:
            assert _repair_truncation(text) in text
