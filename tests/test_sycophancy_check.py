"""Runtime sycophancy check — the compliance the persona rewrite cannot guarantee.

`evals/tutoring/compliance_canary.py` grades the persona BEFORE deployment and
currently scores it at zero unsafe-feature hits. But that is a build-time
measurement of one Modelfile against one base model. A model swap, a Modelfile
edit, a temperature change, or ordinary sampling variance can all reintroduce
flattery in production, and nothing would notice: §1801 compliance would quietly
decay while the last eval still said 100% clean.

This is the runtime net. It reuses the same deterministic screen the eval uses,
so build-time and runtime agree on what a violation is.

WHY IT IS A SIBLING OF THE GUIDANCE ENFORCER, NOT PART OF IT
------------------------------------------------------------
`enforce_guidance` is homework-scoped and gated on GUIDANCE_ENFORCEMENT_ENABLED.
Sycophancy applies to EVERY student turn. Folding this in would widen the
enforcer's trigger — breaking its shipped invariant of being byte-identical when
disabled — and would couple two independent flags. Separate module, separate
flag, same call site.

FAIL-OPEN, unlike the topic gate. A missed "great job" is a compliance blemish,
not a crisis; refusing to answer a child because a rewrite call failed would be
the worse outcome. Every error path returns the original response.

DETECTION IS ALWAYS RECORDED, even when remediation fails or is skipped — a
violation that is silently swallowed is the drift this exists to surface.
"""

import pytest

from core.pedagogy import sycophancy_check as sc


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(sc.settings, "SYCOPHANCY_CHECK_ENABLED", True)


async def _regen_to(text):
    async def _r(nudge: str) -> str:
        return text

    return _r


# ---------------------------------------------------------------------------
# default posture and the disabled invariant
# ---------------------------------------------------------------------------


def test_disabled_by_default():
    from config import system_config

    assert system_config.SYCOPHANCY_CHECK_ENABLED is False


@pytest.mark.asyncio
async def test_disabled_returns_response_byte_identical(monkeypatch):
    """The enforcer's shipped invariant, kept here too: with the flag off the
    response must be returned untouched, not merely equivalent."""
    monkeypatch.setattr(sc.settings, "SYCOPHANCY_CHECK_ENABLED", False)
    original = "Great question! You're so smart. The answer relates to gravity."

    async def _regen(nudge):
        raise AssertionError("must not regenerate when disabled")

    out, meta = await sc.check_and_repair(original, regenerate=_regen)
    assert out is original
    assert meta.action == "disabled"


@pytest.mark.asyncio
async def test_clean_response_is_untouched(enabled):
    original = "Gravity pulls the ball down. What happens if you throw it harder?"

    async def _regen(nudge):
        raise AssertionError("must not regenerate a clean response")

    out, meta = await sc.check_and_repair(original, regenerate=_regen)
    assert out is original
    assert meta.action == "clean"
    assert meta.hits == 0


# ---------------------------------------------------------------------------
# detection + repair
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flattery_triggers_a_regeneration(enabled):
    original = "Great question! Gravity pulls the ball down."
    clean = "Gravity pulls the ball down. What happens if you throw it harder?"

    out, meta = await sc.check_and_repair(original, regenerate=await _regen_to(clean))
    assert out == clean
    assert meta.action == "repaired"
    assert meta.hits >= 1


@pytest.mark.asyncio
async def test_regenerated_text_is_rescreened_and_rejected_if_still_dirty(enabled):
    """A rewrite that reintroduces flattery must not be served. Falling back to
    the original is deliberate: the original at least passed the safety
    pipeline, while the rewrite has not been re-vetted for safety."""
    original = "Great question! Gravity pulls the ball down."
    still_dirty = "Excellent work! Gravity pulls the ball down."

    out, meta = await sc.check_and_repair(
        original, regenerate=await _regen_to(still_dirty)
    )
    assert out == original
    assert meta.action == "repair_rejected"


@pytest.mark.asyncio
async def test_regeneration_failure_fails_open(enabled):
    original = "Great question! Gravity pulls the ball down."

    async def _boom(nudge):
        raise RuntimeError("model unavailable")

    out, meta = await sc.check_and_repair(original, regenerate=_boom)
    assert out is original
    assert meta.action == "repair_failed_open"
    assert meta.hits >= 1, "the violation is still recorded even when repair fails"


@pytest.mark.asyncio
async def test_timeout_fails_open(enabled, monkeypatch):
    import asyncio

    monkeypatch.setattr(sc.settings, "SYCOPHANCY_CHECK_TIMEOUT_S", 0.01)
    original = "Great question! Gravity pulls the ball down."

    async def _slow(nudge):
        await asyncio.sleep(5)
        return "clean"

    out, meta = await sc.check_and_repair(original, regenerate=_slow)
    assert out is original
    assert meta.action == "repair_failed_open"


@pytest.mark.asyncio
async def test_empty_response_is_not_processed(enabled):
    async def _regen(nudge):
        raise AssertionError("nothing to repair")

    out, meta = await sc.check_and_repair("", regenerate=_regen)
    assert out == ""
    assert meta.action == "empty"


# ---------------------------------------------------------------------------
# the categories it reacts to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dirty",
    [
        "Great question! Photosynthesis uses sunlight.",
        "I love this topic. Photosynthesis uses sunlight.",
        "I'm your tutor, so let's start. Photosynthesis uses sunlight.",
        "I believe in you! Photosynthesis uses sunlight.",
        "Last time we talked about plants. Photosynthesis uses sunlight.",
    ],
)
@pytest.mark.asyncio
async def test_reacts_to_every_statutory_category(enabled, dirty):
    """One case per §1801 category the screen covers, so a regression in any
    single category surfaces here rather than only in the offline eval."""
    out, meta = await sc.check_and_repair(
        dirty, regenerate=await _regen_to("Photosynthesis uses sunlight. Why green?")
    )
    assert meta.hits >= 1, f"should have detected a violation in: {dirty!r}"
    assert meta.action == "repaired"


# ---------------------------------------------------------------------------
# the nudge
# ---------------------------------------------------------------------------


def test_nudge_names_the_actual_violations(enabled):
    """A generic 'try again' wastes the one regeneration. The nudge quotes the
    offending phrases so the model can act on them."""
    nudge = sc.build_nudge({"sycophancy": ["Great question"], "relationship": ["trust me"]})
    assert "Great question" in nudge
    assert "trust me" in nudge


def test_nudge_does_not_ask_for_coldness():
    """The persona rewrite already established that removing praise must not make
    the tutor terse or dense — the eval caught a 28-point readability regression
    from exactly that. The nudge must preserve warmth and length."""
    nudge = sc.build_nudge({"sycophancy": ["Great question"]}).lower()
    assert "same" in nudge or "keep" in nudge
    assert "shorter" not in nudge


# ---------------------------------------------------------------------------
# deployment: the screen must live somewhere the image actually contains
# ---------------------------------------------------------------------------


def test_screen_is_importable_without_the_evals_tree():
    """docker/Dockerfile copies api/ core/ safety/ models/ storage/ database/
    utils/ tasks/ — NOT evals/. If the runtime screen imported from evals it
    would raise inside the fail-open handler and silently disable itself in
    production, exactly as a missing langfuse once did. This asserts the
    production import path is a shipped package."""
    import safety.compliance_screen as screen

    assert screen.scan("Great question!"), "screen must work from safety/"


def test_sycophancy_check_does_not_import_evals():
    """Guards the import direction itself, so a future refactor cannot quietly
    reintroduce the dependency."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core/pedagogy/sycophancy_check.py"
    ).read_text()
    import_lines = [
        line
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not [line for line in import_lines if "evals" in line], (
        "production code must not import from evals/ — it is not in the image"
    )
