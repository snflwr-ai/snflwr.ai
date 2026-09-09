"""Tests for the pure parts of the S9051B persona A/B canary.

The canary itself needs a live model, so only the model-free helpers are tested
here: pulling a SYSTEM block out of a Modelfile, and aggregating per-arm results.
"""

import pytest
from evals.tutoring import compliance_canary as cc

# ---------------------------------------------------------------------------
# extract_system_prompt
# ---------------------------------------------------------------------------


def test_extracts_triple_quoted_system_block():
    modelfile = 'FROM gemma4:e4b\n\nSYSTEM """\nYou are a tutor.\nBe clear.\n"""\n\nPARAMETER temperature 0.7\n'
    assert cc.extract_system_prompt(modelfile) == "You are a tutor.\nBe clear."


def test_extraction_stops_before_trailing_parameters():
    modelfile = 'FROM x\nSYSTEM """\nAlpha\n"""\nPARAMETER stop "Student:"\n'
    extracted = cc.extract_system_prompt(modelfile)
    assert "PARAMETER" not in extracted
    assert extracted == "Alpha"


def test_extracts_single_line_system():
    assert cc.extract_system_prompt('FROM x\nSYSTEM "Be brief."\n') == "Be brief."


def test_raises_when_no_system_block():
    with pytest.raises(ValueError, match="no SYSTEM"):
        cc.extract_system_prompt("FROM gemma4:e4b\nPARAMETER temperature 0.7\n")


def test_preserves_internal_quotes_and_blank_lines():
    modelfile = 'SYSTEM """\nSay "hello".\n\nThen stop.\n"""\n'
    assert cc.extract_system_prompt(modelfile) == 'Say "hello".\n\nThen stop.'


# ---------------------------------------------------------------------------
# summarize_arm
# ---------------------------------------------------------------------------


def _row(compliance, hits, categories=None, words=40, guiding=True):
    return {
        "compliance_pct": compliance,
        "hits": hits,
        "categories": categories or {},
        "words": words,
        "guiding_question": guiding,
    }


def test_summarize_averages_compliance_and_totals_hits():
    rows = [_row(100.0, 0), _row(60.0, 2), _row(80.0, 1)]
    summary = cc.summarize_arm(rows)
    assert summary["mean_compliance"] == 80.0
    assert summary["total_hits"] == 3
    assert summary["n"] == 3


def test_summarize_reports_clean_rate():
    """Mean compliance can look fine while most responses still violate; the
    share of fully clean responses is the number that maps to the statute,
    which has no partial credit."""
    rows = [_row(100.0, 0), _row(100.0, 0), _row(20.0, 4)]
    assert cc.summarize_arm(rows)["clean_rate"] == pytest.approx(66.7, abs=0.1)


def test_summarize_counts_hits_per_category():
    rows = [
        _row(80.0, 1, {"sycophancy": ["great question"]}),
        _row(60.0, 2, {"sycophancy": ["nice try"], "relationship": ["trust me"]}),
    ]
    per_cat = cc.summarize_arm(rows)["by_category"]
    assert per_cat["sycophancy"] == 2
    assert per_cat["relationship"] == 1


def test_summarize_tracks_guiding_question_rate():
    """Pedagogy guardrail: the rewrite must not buy compliance by making the
    tutor stop asking guiding questions."""
    rows = [_row(100.0, 0, guiding=True), _row(100.0, 0, guiding=False)]
    assert cc.summarize_arm(rows)["guiding_rate"] == 50.0


def test_summarize_handles_empty_rows():
    summary = cc.summarize_arm([])
    assert summary["n"] == 0
    assert summary["mean_compliance"] is None
    assert summary["total_hits"] == 0


# ---------------------------------------------------------------------------
# bait cases — the eval set must actually contain what it claims to measure
# ---------------------------------------------------------------------------


def test_bait_cases_exist_and_are_well_formed():
    """A compliance eval run only on neutral math questions would score ~100 on
    any persona and prove nothing. These cases actively pull for flattery,
    emotional bonding, and cross-session recall."""
    assert len(cc.BAIT_CASES) >= 8
    for case in cc.BAIT_CASES:
        assert case["id"] and case["question"]
        assert case["band"] in {"K-2", "3-5", "6-8", "9-12"}
        assert case["subject"] == "meta"


def test_bait_cases_have_unique_ids():
    ids = [c["id"] for c in cc.BAIT_CASES]
    assert len(ids) == len(set(ids))


def test_bait_cases_cover_every_unsafe_category():
    """Each statutory category needs at least one prompt aimed at it, or the
    eval silently reports 0 hits for a category it never probed."""
    targeted = {c["targets"] for c in cc.BAIT_CASES}
    assert targeted >= {
        "sycophancy",
        "anthropomorphism",
        "relationship",
        "emotional_appeal",
        "cross_session_memory",
    }
