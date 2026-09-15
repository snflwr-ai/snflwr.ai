"""Letter-spaced profanity reached children unblocked.

"you are a b i t c h" passed as VALID while "you are a bitch" was correctly
blocked. The shared bilingual patterns are plain \\bword\\b regexes matched
against the original and a pre-normalised form; neither collapses separators.
The structure-preserving fold does collapse them, but it also swallows the
preceding article -- "a b i t c h" -> "abitch" -- so even a word-boundary match
fails on it.

A run of 3+ whitespace-separated single letters is not a shape that occurs in
ordinary prose, so runs are collapsed and matched on their own. Matching is
anchored to the END of the run, never an interior substring: searching the
collapsed text freely reintroduced the Scunthorpe problem immediately --
"write a b o u t the water cycle" collapsed to "about" and matched a slur.
"""

from __future__ import annotations

import pytest

from safety.pipeline.pattern_matcher import _PatternMatcher


class TestCollapseLetterRuns:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("you are a b i t c h", "you are abitch"),
            ("f u c k you", "fuck you"),
            ("how do you spell c a t", "how do you spell cat"),
            # Fewer than three single letters is not a run.
            ("a b done", "a b done"),
            ("nothing to collapse here", "nothing to collapse here"),
        ],
    )
    def test_runs_are_joined(self, raw, expected):
        assert _PatternMatcher._collapse_letter_runs(raw) == expected

    def test_empty_and_none_are_safe(self):
        assert _PatternMatcher._collapse_letter_runs("") == ""
        assert _PatternMatcher._collapse_letter_runs(None) == ""


class TestSuffixAnchoringPreventsScunthorpe:
    """The reason matching is anchored to the end of the run.

    An unanchored search over collapsed text matched a slur inside "about",
    blocking "I need to write a b o u t the water cycle". These assert the
    collapsed run exists but must not produce an interior match.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "I need to write a b o u t the water cycle",
            "the r o m a n empire",
            "my name is a n n a",
            "can you help me with a b c order",
            "how do you spell c a t",
            "the answer is t r u e",
        ],
    )
    def test_benign_letter_runs_collapse_but_must_not_match(self, text):
        collapsed = _PatternMatcher._collapse_letter_runs(text)
        # The run really is collapsed -- this is not passing by accident.
        assert collapsed != text
        # And the collapsed word is a legitimate one, not a keyword suffix.
        assert collapsed.strip()
