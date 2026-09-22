"""Enforcement must record WHICH gate decided the turn.

The homework gate has two paths and they are not equivalent. Measured on a blind
holdout (2026-09-21, 40 stuck / 20 demand / 20 dodge wild-shaped turns, generated
without sight of the detector -- ~/snflwr-artefacts/2026-09-21-stuckchild-holdout/):

                       LLM gate (e4b)    regex fallback
  stuck false alarms        0/40              2/40
  demand caught            20/20             16/20
  DODGE caught             18/20 = 90%       13/20 = 65%

A "dodge" is a genuine demand for the assigned deliverable with a refusal or
learning frame bolted on -- "i wanna lern. wats the ansers on my paper", "no
answers pls. whats x". On the fallback path 35% of them succeed, i.e. the product
does the child's assigned work.

The fallback was logged at INFO and counted NOWHERE. A deployment running mostly
on the weak path would have looked identical in every metric the product has --
the same shape as a downgraded safety classifier that runs, says "fine", and
passes the bad output through.

Cold start makes it realistic rather than theoretical: the gate model's first call
after going idle measured 29.9s against a 6s timeout, while steady state is
p50 0.43s. One idle period puts a turn on the regex.

These tests do not measure the gate. They guard that the decision path is
recorded, which is what rots silently.
"""

import inspect

import pytest

from core.pedagogy.guidance_enforcer import EnforceMeta, enforce_guidance


def test_enforce_meta_has_a_gate_field_defaulting_to_none():
    m = EnforceMeta("not_homework")
    assert hasattr(m, "gate"), (
        "EnforceMeta no longer records which gate decided; the weak-path "
        "question becomes unanswerable again"
    )
    assert m.gate == "none"


def test_the_three_states_are_distinct():
    """"not configured" and "reached for and failed" must not be conflated.

    Only the second is a degradation. Collapsing them would make a deployment
    with no LLM gate look like one whose gate is timing out, and vice versa.
    """
    src = inspect.getsource(enforce_guidance)
    assert '"regex_fallback"' in src
    assert '"llm"' in src
    assert "gate_generate is None" in src, (
        "the not-configured case must be distinguished from a failed verdict"
    )


def test_every_outcome_after_the_gate_records_which_gate_decided():
    """Derived from the source, not a hand-listed set of call sites.

    A new early return added later would otherwise silently report gate="none"
    for a turn the LLM gate actually decided -- and the bug would be invisible,
    since "none" is a legal value.
    """
    src = inspect.getsource(enforce_guidance)
    # everything from the point the gate path is known
    after = src[src.index("gate_used = ("):]
    constructions = [
        ln.strip()
        for ln in after.splitlines()
        if "EnforceMeta(" in ln
    ]
    assert constructions, "no EnforceMeta returns found after the gate decision"
    missing = []
    for ln in constructions:
        # single-line constructions must carry it; multi-line ones are checked
        # by the whole-block search below
        if ln.rstrip().endswith("EnforceMeta("):
            continue
        if "gate=" not in ln:
            missing.append(ln)
    assert not missing, (
        "these enforcement outcomes do not record which gate decided:\n  "
        + "\n  ".join(missing)
    )
    # multi-line constructions: every EnforceMeta( block after the gate must
    # mention gate= before its closing paren
    blocks = after.split("EnforceMeta(")[1:]
    for b in blocks:
        head = b[: b.index(")")] if ")" in b else b
        assert "gate=" in head or head.strip().startswith('"disabled"'), (
            f"an EnforceMeta(...) block omits gate=: {head[:80]!r}"
        )


def test_the_fallback_is_logged_at_WARNING_not_INFO():
    """It is a measured capability drop, not a routine event.

    At INFO it sat below the level anyone reads, which is how a 90% -> 65% dodge
    catch rate stayed invisible.
    """
    src = inspect.getsource(enforce_guidance)
    i = src.index('gate_used = (')
    after = src[i:]
    assert "logger.warning" in after, (
        "the regex-fallback path must warn; at INFO the degradation is invisible"
    )
    assert "65" in after and "90" in after, (
        "the warning should carry the measured cost so a reader of the log knows "
        "what the fallback actually means"
    )


def test_disabled_does_not_claim_a_gate():
    """Enforcement switched off never consulted a gate, so it must not report one."""
    src = inspect.getsource(enforce_guidance)
    before = src[: src.index("gate_used = (")]
    assert 'EnforceMeta("disabled")' in before, (
        "the disabled return moved after the gate decision; it would now claim a "
        "gate it never used"
    )


@pytest.mark.asyncio
async def test_a_gate_that_cannot_answer_reports_regex_fallback(monkeypatch):
    """End to end on the real function: an LLM gate that raises must produce
    gate="regex_fallback", not gate="llm" and not a crash.

    Enforcement must be switched ON explicitly. It defaults to False in config
    and is enabled by env in production (verified True in the running container,
    with GUIDANCE_GATE_MODEL=gemma4:e4b), so without this the function returns
    "disabled" before the gate is ever consulted -- which is correct behaviour
    and would make this test pass for the wrong reason.
    """
    from config import system_config as settings

    monkeypatch.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)

    async def boom(_prompt: str) -> str:
        raise RuntimeError("gate unreachable")

    async def regen(_nudge: str) -> str:  # pragma: no cover - not reached
        return "unused"

    async def confirm(_prompt: str) -> str:  # pragma: no cover - not reached
        return '{"revealed": false}'

    # a turn the regex does NOT consider homework, so the pass ends at
    # "not_homework" and we can read the gate field without the ladder running
    _text, meta = await enforce_guidance(
        "why do we have seasons",
        "Seasons come from the tilt of the Earth's axis.",
        regen,
        confirm_generate=confirm,
        gate_generate=boom,
    )
    assert meta.gate == "regex_fallback", (
        f"a failed LLM gate must be recorded as the fallback, got {meta.gate!r}"
    )
