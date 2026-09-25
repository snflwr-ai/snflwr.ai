"""A crisis must not get a routine parent email because of WHICH STAGE caught it.

⚠️ Measured IN PRODUCTION on 2026-09-24, minutes after the batch deploy, by a
peer session running two phrasings through the live container:

    "i want to die"                                -> PATTERN stage -> CRITICAL
    "i just want to go to sleep and never wake up" -> CLASSIFIER    -> MAJOR

Both are the same child in the same danger. `email_service.send_safety_alert`
picks the URGENT template only for `severity in ["critical","high"]`, and
escalation passes severity straight through -- so the second child's parent got
an ORDINARY notice.

The cause is structural, not a tuning miss: `safety/pipeline/classifier.py`
emits `Severity.MAJOR` at all 7 of its sites and `Severity.CRITICAL` at NONE,
while the pattern stage can emit CRITICAL. So the softer a child phrases
ideation -- the more it slips the regex and lands on the classifier -- the less
urgent their parent's alert becomes. Exactly backwards.

This is #327's lesson one stage further along. There, the crisis TEXT could not
depend on the classifier choosing the SELF_HARM label. Here, the crisis
URGENCY cannot depend on which component did the catching. Decide on the
outcome, never on the machinery that noticed.
"""

import pytest

from api.routes.ollama_proxy.blocks import crisis_escalation_severity
from safety.pipeline.models import Category, Severity, _block


def block(category, severity):
    return _block(severity, category, "blocked", stage="x")


# The regression: what the classifier actually produces for a crisis.
def test_a_classifier_caught_crisis_escalates_at_critical():
    result = block(Category.SELF_HARM, Severity.MAJOR)
    assert (
        crisis_escalation_severity(
            result,
            "i just want to go to sleep and never wake up",
            category_describes_child=True,
        )
        == "critical"
    )


def test_a_pattern_caught_crisis_stays_critical():
    """The path that already worked must not regress."""
    result = block(Category.SELF_HARM, Severity.CRITICAL)
    assert (
        crisis_escalation_severity(result, "i want to die", category_describes_child=True)
        == "critical"
    )


@pytest.mark.parametrize(
    "category",
    [Category.VIOLENCE, Category.DRUGS, Category.TOPIC_REDIRECT, Category.WEAPONS],
)
def test_a_crisis_mislabelled_as_another_category_still_escalates_critical(category):
    """⚠️ The classifier types an overdose-method question as VIOLENCE (measured
    on a cold sealed set). The label is not trustworthy, so the TEXT decides —
    the same rule #327 established for the 988 line itself."""
    result = block(category, Severity.MAJOR)
    assert (
        crisis_escalation_severity(
            result, "how many pills would it take", category_describes_child=True
        )
        == "critical"
    )


@pytest.mark.parametrize(
    "text",
    [
        "how many people died in the battle of hastings",
        "explain how Romeo takes his own life in the play",
        "i need help with my essay",
    ],
)
def test_ordinary_blocks_keep_their_own_severity(text):
    """⚠️ The guard. Promoting everything to critical would make URGENT mean
    nothing, which is the same harm as never sending it -- a parent who learns
    that urgent alerts are routine stops opening them."""
    result = block(Category.VIOLENCE, Severity.MAJOR)
    assert crisis_escalation_severity(result, text, category_describes_child=True) == "major"

    minor = block(Category.TOPIC_REDIRECT, Severity.MINOR)
    assert crisis_escalation_severity(minor, text, category_describes_child=True) == "minor"


def test_the_classifier_still_cannot_emit_critical_on_its_own():
    """The reason this helper exists. If the classifier ever starts emitting
    CRITICAL directly, this test fails and the helper can be reconsidered --
    it should not quietly become redundant."""
    import inspect

    from safety.pipeline import classifier

    src = inspect.getsource(classifier)
    assert "Severity.CRITICAL" not in src, (
        "the classifier now emits CRITICAL; re-check whether "
        "crisis_escalation_severity is still needed, rather than leaving two "
        "places deciding crisis urgency"
    )


# ---------------------------------------------------------------------------
# ⚠️⚠️ OUTPUT BLOCKS. Peer review found the first version inflating URGENT here,
# which is the exact guard this change claims to protect.
#
# On an output block the snippet is the TUTOR'S draft, not the child's words.
# The first version fed that snippet to the crisis check, so a blocked reply
# mentioning "Juliet's suicide" promoted a child who asked "summarize act 5" to
# a CRITICAL crisis alert.
#
# ⭐ And note HOW I missed it: every test above calls the helper DIRECTLY with a
# child's sentence. None went through the call sites, so none could see that one
# of them passes the model's text. That is the same mistake recorded this
# morning in `stage-tested-in-isolation-is-not-coverage` — testing a unit in
# isolation and reporting it as coverage of the behaviour.
# ---------------------------------------------------------------------------


def test_a_blocked_tutor_reply_about_a_set_text_is_not_a_child_crisis():
    """The reply mentions suicide. The CHILD asked about Act 5."""
    result = block(Category.VIOLENCE, Severity.MAJOR)
    assert (
        crisis_escalation_severity(
            result,
            "summarize act 5 of romeo and juliet",
            category_describes_child=False,
        )
        == "major"
    ), "a parent got an URGENT crisis email because the TUTOR said 'suicide'"


def test_an_unsafe_tutor_draft_is_a_model_defect_not_a_child_crisis():
    """The classifier labelled the TUTOR'S output self_harm — it drifted into
    method detail on a health question. That is a model defect. Promoting it
    makes URGENT mean 'the model misbehaved'."""
    result = block(Category.SELF_HARM, Severity.MAJOR)
    assert (
        crisis_escalation_severity(
            result, "what does acetaminophen do", category_describes_child=False
        )
        == "major"
    )


def test_a_child_in_crisis_whose_reply_was_also_blocked_still_escalates():
    """The child's own words still promote on the output path."""
    result = block(Category.VIOLENCE, Severity.MAJOR)
    assert (
        crisis_escalation_severity(
            result, "i want to die", category_describes_child=False
        )
        == "critical"
    )


def test_every_call_site_states_whose_text_the_category_describes():
    """⭐ The guard for the mistake actually made: the helper was right and a
    CALL SITE was wrong. Asserted on the source, because that is where it lived.
    """
    import inspect
    import re

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    calls = re.findall(r"_record_safety_incident\(([^)]*)\)", src, re.S)
    assert len(calls) >= 3, f"expected the input and two output sites, found {len(calls)}"
    for c in calls:
        assert "category_describes_child=" in c, (
            "a _record_safety_incident call site does not say whose text the "
            "category describes; on an output path that silently promotes the "
            f"model's own misbehaviour to a child crisis. Call: {c[:120]!r}"
        )
    # The output sites must hand over the CHILD's question, never the draft.
    assert src.count("child_text=user_question") == 2, (
        "an output block is not passing the child's question as the crisis text"
    )


def test_the_native_route_gets_the_same_rule():
    """⚠️ `api/routes/chat.py` is MOUNTED at /api/chat/send (api/server.py:154),
    so the same gap was live on a second path — despite a docstring elsewhere
    saying students reach the model through the proxy. That describes the OWUI
    deployment, not reachability, and I checked the router rather than trusting
    it.

    Its input site must promote (the category describes the child); its output
    site must not (it describes the model's own text).
    """
    import inspect
    import re

    from api.routes import chat as native

    src = inspect.getsource(native)
    calls = re.findall(r"crisis_escalation_severity\(([^)]*)\)", src, re.S)
    assert len(calls) == 2, f"expected the input and output sites, found {len(calls)}"
    assert any("category_describes_child=True" in c for c in calls), (
        "the native INPUT site does not promote a crisis, so a classifier-caught "
        "crisis there still gets the ordinary parent email"
    )
    assert any("category_describes_child=False" in c for c in calls), (
        "the native OUTPUT site promotes on the model's own category, which "
        "turns an unsafe tutor draft into an urgent child-crisis alert"
    )
    assert "severity=response_filter.severity.value" not in src
    assert "severity=filter_result.severity.value" not in src
