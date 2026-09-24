"""Every canned child-facing reply must be enumerable and recognisable.

This is the structural fix for a defect that has recurred four times: an
instrument carrying its own hand-written list of canned replies, which drifts
from the code that emits them, and then scores canned text as genuine tutoring.

The fourth instance is why this file exists. The 2026-09-24 sealed
re-certification served **9 safety refusals in 212 turns** and recorded
`sentinel = None` for every one, because the runner's list knew about the busy
message, the timeout, the unsupported box, the missing profile and the
withholding fallback -- and nothing at all about `safety/`. Four of the nine
surfaced only because they happened to be under 80 characters and tripped an
unrelated length check. The other five would have gone to human raters as
genuine tutor replies.

`core.canned_replies` derives the list by calling the emitters, so a new canned
reply cannot hide from it. These tests pin the properties an instrument relies
on.
"""

import pytest

from core.canned_replies import (
    SCORED_AS_PRODUCT,
    all_canned_replies,
    infrastructure_replies,
    is_canned,
    pedagogy_replies,
    safety_replies,
)

# Real tutor replies. `is_canned` must not fire on any of them -- including the
# one containing the word that caused the production false positive.
REAL_TUTORING = [
    "A simile compares two things using 'like' or 'as'. Which one is this?",
    "Water molecules slip between the starch pieces, so the cracker goes soft.",
    "Each packet carries a destination address, and routers pass it along.",
    "Start at 7 and count forward 6 more times. What number do you land on?",
    "The Gettysburg Address was delivered in 1863. What was its purpose?",
]


def test_every_source_of_canned_replies_is_represented():
    """Infrastructure, pedagogy AND safety. Safety was the class that was missed."""
    assert infrastructure_replies(), "no infrastructure replies enumerated"
    assert pedagogy_replies(), "no pedagogy replies enumerated"
    assert safety_replies(), (
        "no SAFETY replies enumerated -- this is the class that was in nobody's "
        "list and served 9 times in the 2026-09-24 certification run"
    )
    merged = all_canned_replies()
    for source in (infrastructure_replies, pedagogy_replies, safety_replies):
        for name in source():
            assert (
                name in merged
            ), f"{name} is enumerated but missing from the merged list"


def test_both_safety_wordings_are_enumerated():
    """Input-side and output-side refusals differ, and both reach children.

    `get_safe_response` usually appends a follow-up question; `_output_fallback`
    is terser. The production run served both.
    """
    keys = safety_replies().keys()
    assert any(k.startswith("safety_in:") for k in keys), "input-side refusals missing"
    assert any(
        k.startswith("safety_out:") for k in keys
    ), "output-side refusals missing"


@pytest.mark.parametrize(
    "name", sorted(all_canned_replies()), ids=sorted(all_canned_replies())
)
def test_every_canned_reply_recognises_itself(name):
    """Round-trip: the registry must recognise every string it enumerates.

    Guards the failure this module prevents -- a reply in the list that the
    matcher cannot actually find.
    """
    text = all_canned_replies()[name]
    assert (
        is_canned(text) is not None
    ), f"{name} is enumerated but is_canned() does not recognise it verbatim"


@pytest.mark.parametrize(
    "name", sorted(all_canned_replies()), ids=sorted(all_canned_replies())
)
def test_a_truncated_canned_reply_is_still_recognised(name):
    """Truncation is not hypothetical: 30.4% of served replies on 2026-09-20
    were cut off mid-sentence, and a cut canned reply is still canned.

    Full-string matching missed these; prefix matching was adopted because of
    it.
    """
    text = all_canned_replies()[name]
    if len(text) < 45:
        pytest.skip("too short to truncate below the prefix probe")
    assert (
        is_canned(text[:45]) is not None
    ), f"{name} truncated to 45 chars is no longer recognised"


@pytest.mark.parametrize("reply", REAL_TUTORING, ids=range(len(REAL_TUTORING)))
def test_real_tutoring_is_not_mistaken_for_canned(reply):
    """The other direction: a genuine reply must never be discarded as canned.

    Includes "the cracker goes soft" and "destination address" -- the two
    phrases behind the 2026-09-24 safety false positives -- because an
    over-eager canned-reply matcher would hollow out the denominator instead,
    which is the `rate-improved-or-denominator-hollowed` failure.
    """
    assert is_canned(reply) is None, f"real tutoring matched as canned: {reply!r}"


def test_product_behaviour_is_distinguished_from_infrastructure():
    """A withholding fallback IS a bar; a busy message is not a measurement.

    An instrument needs this split to decide whether a canned reply belongs in
    a denominator or is unmeasured.
    """
    assert "withholding" in SCORED_AS_PRODUCT
    assert any(p.startswith("safety") for p in SCORED_AS_PRODUCT)
    for infra in infrastructure_replies():
        assert infra not in SCORED_AS_PRODUCT, (
            f"{infra} is an infrastructure failure -- scoring it as product "
            f"behaviour puts 'the tutor never ran' into a tutoring rate"
        )


def test_is_canned_handles_empty_and_none_safely():
    assert is_canned("") is None
    assert is_canned(None) is None  # type: ignore[arg-type]
