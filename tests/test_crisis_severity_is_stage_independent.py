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
    assert crisis_escalation_severity(result, "i just want to go to sleep and never wake up") == "critical"


def test_a_pattern_caught_crisis_stays_critical():
    """The path that already worked must not regress."""
    result = block(Category.SELF_HARM, Severity.CRITICAL)
    assert crisis_escalation_severity(result, "i want to die") == "critical"


@pytest.mark.parametrize(
    "category",
    [Category.VIOLENCE, Category.DRUGS, Category.TOPIC_REDIRECT, Category.WEAPONS],
)
def test_a_crisis_mislabelled_as_another_category_still_escalates_critical(category):
    """⚠️ The classifier types an overdose-method question as VIOLENCE (measured
    on a cold sealed set). The label is not trustworthy, so the TEXT decides —
    the same rule #327 established for the 988 line itself."""
    result = block(category, Severity.MAJOR)
    assert crisis_escalation_severity(result, "how many pills would it take") == "critical"


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
    assert crisis_escalation_severity(result, text) == "major"

    minor = block(Category.TOPIC_REDIRECT, Severity.MINOR)
    assert crisis_escalation_severity(minor, text) == "minor"


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
