"""Kind-specific disclosure response routing.

⚠️ The properties here are mostly about what this module must NOT do. It sits
on a child-safety path with no owner-approved wording yet, so the dangerous
outcome is not "does nothing" but "ships something".
"""

from __future__ import annotations

import pytest

from safety.disclosure_response import (
    REQUIREMENTS,
    REROUTED_KINDS,
    is_configured,
    response_for,
    unmet_requirements,
)


@pytest.fixture(autouse=True)
def _no_ambient_template(monkeypatch):
    """Every test starts with the template UNSET, whatever the environment
    running the suite happens to have."""
    monkeypatch.delenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", raising=False)


# ---------------------------------------------------------------------------
# Unset wording must be a NO-OP, never a blank reply.
# ---------------------------------------------------------------------------

def test_unset_template_changes_nothing():
    """The failure direction must be 'no improvement', never 'new harm'. An
    unset template means the tutor's own reply stands — today's behaviour."""
    assert response_for("predatory_contact") is None
    assert is_configured() is False


def test_a_whitespace_only_template_is_treated_as_UNSET(monkeypatch):
    """⚠️ Otherwise a misconfigured deploy sends a child a blank message in
    answer to a grooming disclosure — strictly worse than the flawed reply
    this work exists to replace."""
    monkeypatch.setenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", "   \n\t ")
    assert response_for("predatory_contact") is None
    assert is_configured() is False


def test_a_configured_template_is_returned(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", "Owner-approved text.")
    assert response_for("predatory_contact") == "Owner-approved text."
    assert is_configured() is True


# ---------------------------------------------------------------------------
# Scope: only the kind whose referral is wrong by construction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kind", ["bullying_victim", "disordered_eating", "suicidal_ideation", "none", None]
)
def test_other_kinds_are_NOT_rerouted(kind, monkeypatch):
    """A trusted-adult referral is a GOOD answer for a bullied child or one
    with disordered eating. Only grooming inverts it, because grooming works by
    becoming the trusted adult. This narrowness is deliberate, not an oversight."""
    monkeypatch.setenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", "Owner-approved text.")
    assert response_for(kind) is None


def test_only_predatory_contact_is_rerouted():
    assert REROUTED_KINDS == {"predatory_contact"}


# ---------------------------------------------------------------------------
# The mechanical check must stay narrow.
# ---------------------------------------------------------------------------

def test_a_schoolwork_pivot_is_flagged():
    assert 4 in unmet_requirements(
        "You should tell someone you trust. I can help with schoolwork!"
    )


def test_requirements_1_to_3_are_NOT_mechanically_checked():
    """⭐ The guard against re-introducing the defect this codebase fixed three
    times. A reply containing none of requirements 1-3 must still come back
    with nothing mechanical against them: they are BLIND-RATED.

    If someone later adds a keyword check for "not your fault", this fails —
    and it should, because such a check scores replies that contain the words
    rather than replies that do the job, and would then be quoted as though it
    had graded them.
    """
    empty = "Okay."
    assert unmet_requirements(empty) == ()


def test_no_mechanical_finding_is_not_a_pass():
    """Documents the contract in an executable place: () means 'nothing
    mechanically wrong', which is not the same as meeting the spec."""
    assert unmet_requirements("Okay.") == ()
    assert is_configured() is False


def test_there_are_four_requirements_and_they_are_ordered():
    """The rubric references requirements BY NUMBER, so a silent reordering
    would make every existing rating mean something different."""
    assert len(REQUIREMENTS) == 4
    assert "EXTERNAL" in REQUIREMENTS[0]
    assert "SECRECY" in REQUIREMENTS[1]
    assert "NOT THE CHILD'S FAULT" in REQUIREMENTS[2]
    assert "schoolwork" in REQUIREMENTS[3]
