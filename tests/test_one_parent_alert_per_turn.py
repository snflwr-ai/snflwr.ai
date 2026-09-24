"""One blocked disclosure must reach a parent ONCE, with the right framing.

⚠️ #325 made the disclosure detector run on blocked turns too, so a grooming
report that trips the harm classifier is typed as a disclosure instead of as
`exploitation`. Correct — but it left a blocked disclosure writing TWO major
incidents, and escalation alerts on every major/critical row
(`if send_alert and severity in ["major","critical"]`).

So a parent got two emails for one message, and one of them still described
their child as having requested harmful content. That partly undoes the fix.

⚠️⚠️ The dangerous way to fix this is to suppress the safety alert whenever ANY
disclosure is detected. Only `suicidal_ideation` and `predatory_contact` are
MAJOR; bullying and disordered-eating disclosures are MINOR and alert nobody.
Suppressing on those would trade two alerts for ZERO — a silent regression
strictly worse than the bug. Hence the suppression is driven by what the
disclosure row actually did, not by whether one existed.
"""

import importlib

import pytest

from api.routes.ollama_proxy import blocks


@pytest.fixture
def sent(monkeypatch):
    """Capture every log_incident call and whether it would alert."""
    rows = []

    class _Logger:
        def log_incident(self, **kw):
            rows.append(kw)
            return True, len(rows)

    monkeypatch.setattr(
        importlib.import_module("safety.incident_logger"), "incident_logger", _Logger()
    )
    return rows


def _alerts(rows):
    """Rows that actually reach a parent: escalation routes major/critical."""
    return [
        r
        for r in rows
        if r.get("send_alert", True) and r.get("severity") in ("major", "critical")
    ]


@pytest.mark.parametrize("kind", ["predatory_contact", "suicidal_ideation"])
def test_major_disclosure_alerts_and_reports_that_it_did(sent, kind):
    alerted = blocks._record_disclosure_incident(
        "p1", kind, "matched", "the child's words", blocked=True
    )
    assert alerted is True, (
        f"{kind} is a MAJOR disclosure and must report that it alerted, or the "
        f"caller cannot know to suppress the duplicate"
    )
    assert len(_alerts(sent)) == 1


@pytest.mark.parametrize("kind", ["bullying_victim", "disordered_eating"])
def test_minor_disclosure_reports_that_it_did_NOT_alert(sent, kind):
    """⭐ The regression guard. These record but never alert, so the caller must
    keep the safety alert — otherwise this 'fix' silences the only one."""
    alerted = blocks._record_disclosure_incident(
        "p1", kind, "matched", "the child's words", blocked=True
    )
    assert alerted is False, (
        f"{kind} is MINOR and alerts nobody, but it claimed to have alerted — "
        f"the caller will now suppress the safety alert and the parent gets "
        f"NOTHING for a blocked turn"
    )
    assert _alerts(sent) == []


def test_safety_row_is_still_written_when_its_alert_is_suppressed(sent):
    """Suppressing the ALERT must not suppress the RECORD. The safety row is the
    only thing that says why the child's reply was replaced."""

    class _Cat:
        value = "exploitation"

    class _Sev:
        value = "major"

    class _Result:
        is_safe = False
        category = _Cat()
        severity = _Sev()
        stage = "classifier"
        triggered_keywords = ()

    blocks._record_safety_incident("p1", _Result(), "the child's words", send_alert=False)
    assert len(sent) == 1, "the safety incident was not recorded at all"
    assert sent[0]["incident_type"] == "exploitation"
    assert sent[0]["send_alert"] is False
    assert _alerts(sent) == [], "the suppressed row still alerted"


def test_the_alerting_kinds_match_the_severity_rule():
    """The constant and the severity it drives must not drift apart — two call
    sites depend on them agreeing, and a drift means two alerts or none."""
    import inspect

    src = inspect.getsource(blocks._record_disclosure_incident)
    assert "ALERTING_DISCLOSURE_KINDS" in src, (
        "the severity rule was re-inlined; it must read the shared constant"
    )
    assert set(blocks.ALERTING_DISCLOSURE_KINDS) == {
        "suicidal_ideation",
        "predatory_contact",
    }


def test_the_route_suppresses_only_on_an_actual_alert():
    """Asserted on the SOURCE of the route, because the behavioural path needs
    the whole proxy stack. What must be true is that the suppression reads the
    RETURNED flag, not merely whether a disclosure existed."""
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    assert "_disclosure_alerted = blocks._record_disclosure_incident(" in src
    assert "send_alert=not _disclosure_alerted" in src, (
        "the safety alert is not gated on whether the disclosure row alerted"
    )
    assert "send_alert=_disclosure is None" not in src, (
        "suppression is keyed on a disclosure EXISTING, which silences the only "
        "alert for the minor kinds"
    )
