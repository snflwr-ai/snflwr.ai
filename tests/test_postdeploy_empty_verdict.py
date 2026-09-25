"""An empty confirm verdict must not tell an operator to roll back a good deploy.

⚠️ Measured in production 2026-09-24, minutes after a batch deploy. The smoke
test raised `reveal-confirm raised (confirm model returned nothing)`, which
matched NO signature, fell through to the default FAIL, and deploy.sh printed
"roll back". The card was in fact held by the co-tenant and the tutor was 100%
CPU in /api/ps — textbook contention, and the deploy was fine.

A false FAIL that tells an operator to roll back a good deploy is worse than
the silence it replaced: it spends trust in the check, and the next real FAIL
gets argued with.

⭐ But it is classified AMBIGUOUS and NOT capacity, because "returned nothing"
has three causes and only one is load:

  1. contention — the tutor is on CPU and unusable            -> UNMEASURED
  2. the confirm runs on the TUTOR and its brevity rules
     truncate the verdict to `{"` — the 0/20-recall incident
     this whole file exists to catch                          -> FAIL
  3. a thinking-capable model with `think` unset returns an
     EMPTY response with done_reason=length, at any
     num_predict (measured on gemma4 2026-09-24)              -> FAIL

Downgrading it unconditionally would hide (2) and (3), and (2) is the silent
failure that motivated the check.
"""

import pytest

import scripts.postdeploy_smoke as m
from scripts.postdeploy_smoke import classify_confirm_failure

EMPTY = [
    "reveal-confirm raised (confirm model returned nothing)",
    "confirm returned no content",
    "empty response from the confirm model",
    "no verdict parsed",
]


@pytest.fixture
def no_gpu_evidence(monkeypatch):
    monkeypatch.setattr(m, "_gpu_corroborates_contention", lambda: "")


@pytest.fixture
def gpu_corroborates(monkeypatch):
    monkeypatch.setattr(
        m,
        "_gpu_corroborates_contention",
        lambda: "ironclaw holds the card and the tutor is 100% CPU",
    )


@pytest.mark.parametrize("msg", EMPTY, ids=range(len(EMPTY)))
def test_an_empty_verdict_under_real_contention_is_UNMEASURED(msg, gpu_corroborates):
    verdict, reason = classify_confirm_failure(RuntimeError(msg))
    assert verdict == "UNMEASURED", (
        f"a good deploy would be rolled back on GPU contention: {reason}"
    )


@pytest.mark.parametrize("msg", EMPTY, ids=range(len(EMPTY)))
def test_an_empty_verdict_with_NO_gpu_evidence_still_FAILS(msg, no_gpu_evidence):
    """⚠️ The half that must not be softened. An empty verdict with a healthy
    card is the 0/20-recall bug, or `think` unset — both real, both shipped,
    both invisible without this check."""
    verdict, _ = classify_confirm_failure(RuntimeError(msg))
    assert verdict == "FAIL", (
        "an empty verdict was downgraded without GPU evidence; that hides the "
        "tutor-persona truncation this file was written for"
    )


def test_the_operator_message_names_the_right_suspects(no_gpu_evidence):
    """An operator reads this while a deploy is failing. "wrong host/port" is
    actively misleading here: nothing was unreachable, the model answered with
    nothing."""
    _, reason = classify_confirm_failure(RuntimeError("confirm model returned nothing"))
    assert "ANSWERED and said nothing" in reason
    assert "think" in reason, "the thinking-token cause is not mentioned"
    assert "host/port" not in reason, (
        "the empty-verdict message still blames a wrong host, which sends the "
        "operator to the wrong place"
    )

    _, conn = classify_confirm_failure(RuntimeError("connection refused"))
    assert "host/port" in conn, "the connection message lost its own advice"


def test_capacity_errors_are_still_downgraded_without_gpu_evidence(no_gpu_evidence):
    """Unchanged: a real OOM needs no corroboration."""
    verdict, _ = classify_confirm_failure(RuntimeError("CUDA out of memory"))
    assert verdict == "UNMEASURED"


def test_an_unrecognised_error_still_defaults_to_FAIL(no_gpu_evidence):
    """The default must stay FAIL. #318's first version returned [] for
    UNMEASURED, which made deploy.sh print "Shipped behaviour verified." — a
    false GREEN, caught in review."""
    verdict, _ = classify_confirm_failure(ValueError("something new"))
    assert verdict == "FAIL"


def test_empty_verdict_signatures_are_a_subset_of_the_ambiguous_ones():
    """They must be classified identically; the split exists only so the FAIL
    message can differ. A drift would make one set unclassified."""
    assert set(m._EMPTY_VERDICT_SIGNATURES) <= set(m._AMBIGUOUS_SIGNATURES)
    assert not set(m._EMPTY_VERDICT_SIGNATURES) & set(m._CAPACITY_SIGNATURES), (
        "an empty verdict is in the CAPACITY list, so it downgrades "
        "unconditionally and hides the persona and think-unset bugs"
    )
