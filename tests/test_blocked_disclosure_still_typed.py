"""A disclosure the harm classifier blocks must still be TYPED as a disclosure.

⚠️ Found 2026-09-24 while measuring whether llama-guard backstops the regex
disclosure detector. It does not (2 of 22 on a held-out sealed set) -- but the
measurement surfaced something worse than the low number:

`detect_disclosure` was called inside `if result.is_safe`, so the two nets were
MUTUALLY EXCLUSIVE. A grooming report that happened to trip the harm classifier
never reached the disclosure detector at all. The only incident written was
`exploitation` -- which reads as the child REQUESTING harmful content, the exact
opposite of a child reporting that it happened to them.

A parent alert that describes the wrong one of those is worse than a late alert:
it points suspicion at the child who reached out.

Both rows are expected on such a turn. The safety row says why the reply was
replaced; the disclosure row says what the child was actually doing.
"""

import pytest

from api.routes.ollama_proxy import blocks


class _Cat:
    value = "exploitation"


class _Sev:
    value = "major"


class _Result:
    """Minimal stand-in for the safety pipeline's result object."""

    def __init__(self, is_safe: bool):
        self.is_safe = is_safe
        self.category = _Cat()
        self.severity = _Sev()
        self.modified_content = None
        self.stage = "classifier"
        self.triggered_keywords = ()


@pytest.fixture
def logged(monkeypatch):
    """Capture what reaches the incident logger, without a DB."""
    rows = []

    class _Logger:
        def log_incident(self, **kw):
            rows.append(kw)

    # ⚠️ A real footgun in this package, worth knowing about beyond this test:
    # `safety/__init__.py` binds the SINGLETON to the name
    # `safety.incident_logger`, shadowing the submodule of the same name. So
    # `safety.incident_logger` read as an ATTRIBUTE is an IncidentLogger
    # instance, while `sys.modules["safety.incident_logger"]` is the module.
    # Both `import ... as il` and monkeypatch's dotted-string form walk
    # attributes and therefore land on the instance, patching something the code
    # under test never reads. importlib returns the module itself.
    import importlib

    monkeypatch.setattr(
        importlib.import_module("safety.incident_logger"),
        "incident_logger",
        _Logger(),
    )
    return rows


def test_blocked_disclosure_is_recorded_as_a_disclosure(logged):
    blocks._record_disclosure_incident(
        "p1", "predatory_contact", "meet alone", "he wants to meet me alone", blocked=True
    )
    assert len(logged) == 1
    row = logged[0]
    assert row["incident_type"] == "disclosure_predatory_contact", (
        "a blocked disclosure was not typed as a disclosure -- a parent alert "
        "would describe the child as requesting harmful content"
    )
    assert row["severity"] == "major", "severity below major does not raise a parent alert"
    assert row["metadata"]["blocked"] is True, (
        "a reviewer cannot tell whether the child got the tutor's reply or a "
        "canned refusal"
    )


def test_unblocked_disclosure_still_records_blocked_false(logged):
    """The default path must not regress: blocked defaults to False."""
    blocks._record_disclosure_incident(
        "p1", "predatory_contact", "meet alone", "he wants to meet me alone"
    )
    assert logged[0]["metadata"]["blocked"] is False


def test_the_detector_call_is_not_gated_on_is_safe():
    """⭐ The actual defect, asserted against the SOURCE of the route.

    A behavioural test would need the whole proxy stack (auth, profile, ollama).
    What went wrong was one `if`, so this asserts the `if` is gone: the
    disclosure detector must not sit inside an `is_safe` branch.
    """
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    i = src.index("detect_disclosure(text)")
    # Walk back to the enclosing block and confirm no is_safe gate intervenes
    # between the try and the call.
    window = src[max(0, i - 600) : i]
    assert "if result.is_safe" not in window, (
        "detect_disclosure is gated on result.is_safe again -- blocked "
        "disclosures will be typed as the child requesting harmful content"
    )
    assert "blocked=not result.is_safe" in src[i : i + 600], (
        "the disclosure incident no longer records whether the turn was blocked"
    )
