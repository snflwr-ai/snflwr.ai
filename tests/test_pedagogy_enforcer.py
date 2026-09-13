from config import system_config as settings


def test_guidance_flags_default_safe():
    assert settings.GUIDANCE_ENFORCEMENT_ENABLED is False
    assert settings.GUIDANCE_ENFORCER_CONFIRM_MODEL == ""
    # Raised from 8s on 2026-09-12: a confirm needing 9.3s on a 1.2k-char
    # answer timed out and fail-opened a reveal it had correctly identified.
    assert settings.GUIDANCE_ENFORCER_TIMEOUT_S == 15.0


# ---------------------------------------------------------------------------
# enforce_guidance orchestrator — confirm on EVERY homework turn (no cheap gate);
# the retry is re-checked with the same confirm. All error paths fail OPEN.
# ---------------------------------------------------------------------------
import asyncio

from core.pedagogy.guidance_enforcer import (
    _WITHHOLDING_FALLBACK,
    enforce_guidance,
)


def _fallback():
    return _WITHHOLDING_FALLBACK


def _run(c):
    return asyncio.run(c)


def _confirm_when(substr):
    """Confirm mock: revealed=true iff `substr` appears in the confirm prompt
    (the prompt embeds the response under test), else false."""

    async def gen(prompt):
        return '{"revealed": true}' if substr in prompt else '{"revealed": false}'

    return gen


def _enable(mp, **kw):
    mp.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
    for k, v in kw.items():
        mp.setattr(settings, k, v)


def test_disabled_is_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", False)

    async def confirm(_):
        raise AssertionError("must not confirm when disabled")

    async def regen(_):
        raise AssertionError("must not regenerate when disabled")

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=confirm
        )
    )
    assert out == "It's 56." and meta.action == "disabled"


def test_not_homework_passthrough(monkeypatch):
    _enable(monkeypatch)

    async def confirm(_):
        raise AssertionError("must not confirm on a non-homework turn")

    async def regen(_):
        raise AssertionError

    out, meta = _run(
        enforce_guidance(
            "how do plants eat?",
            "Photosynthesis makes sugar from sunlight.",
            regen,
            confirm_generate=confirm,
        )
    )
    assert (
        out == "Photosynthesis makes sugar from sunlight."
        and meta.action == "not_homework"
    )


def test_no_reveal_passthrough(monkeypatch):
    # Homework turn, confirm says NOT revealed -> serve original, no re-prompt.
    _enable(monkeypatch)

    async def regen(_):
        raise AssertionError("must not regenerate when nothing was revealed")

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8",
            "What is 7 times 4, and how could that help?",
            regen,
            confirm_generate=_confirm_when("56"),  # no "56" in this response
        )
    )
    assert out.startswith("What is 7 times 4") and meta.action == "no_reveal"


def test_word_form_reveal_is_caught(monkeypatch):
    # The whole point of the fix: a WORD-FORM reveal (no digits) is caught because
    # confirm runs on every homework turn (the old regex gate missed these).
    _enable(monkeypatch)

    async def regen(_):
        return "What comes after three when you count up?"

    # Key on "equals four" (distinctive to the response) — not bare "four", which
    # also appears as an example inside the confirm prompt template.
    out, meta = _run(
        enforce_guidance(
            "just tell me 2+2",
            "Two plus two equals four! Now try with your fingers.",
            regen,
            confirm_generate=_confirm_when("equals four"),
        )
    )
    assert out == "What comes after three when you count up?"
    assert meta.action == "reprompt_clean"


def test_confirmed_reveal_clean_retry_used(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        return "What is 7 times 4, then double it?"

    # confirm flags any response containing "56": original has it, retry doesn't.
    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=_confirm_when("56")
        )
    )
    assert (
        out == "What is 7 times 4, then double it?" and meta.action == "reprompt_clean"
    )


# ---------------------------------------------------------------------------
# CONTRACT CHANGE 2026-09-11. These four cases used to assert that the ORIGINAL
# was served. The original is the text the confirm had just flagged as revealing
# the homework answer, so "keep the original" meant "ship the reveal" -- the one
# outcome this module exists to prevent.
#
# Now: once a reveal is CONFIRMED, the original can never ship. The enforcer
# retries the rewrite up to GUIDANCE_ENFORCER_MAX_REGEN_ATTEMPTS times and, if
# none is clean, serves a static withholding turn.
#
# Fail-open is NOT abandoned -- it still governs "we could not CHECK" (confirm
# timeout, unreachable model, unparseable verdict), where the model's own answer
# is served because nothing is known to be wrong with it. See
# TestFailOpenBoundaryIsPreserved in test_pedagogy_enforcer_retry.py.
# ---------------------------------------------------------------------------


def test_retry_still_reveals_serves_the_fallback(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        return "Fine, it's 56 again."  # retry STILL contains 56

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=_confirm_when("56")
        )
    )
    assert "56" not in out, "served the answer the confirm flagged"
    assert out == _fallback() and meta.action == "fallback_served"


def test_regenerate_error_serves_the_fallback(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        raise RuntimeError("boom")

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=_confirm_when("56")
        )
    )
    # The rewrite could not run, but the reveal is still CONFIRMED, so the
    # original remains unservable.
    assert "56" not in out and meta.action == "fallback_served"


def test_empty_retry_serves_the_fallback(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        return ""  # empty regeneration

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=_confirm_when("56")
        )
    )
    # The rewrite could not run, but the reveal is still CONFIRMED, so the
    # original remains unservable.
    assert "56" not in out and meta.action == "fallback_served"


def test_confirm_timeout_fails_open(monkeypatch):
    _enable(monkeypatch, GUIDANCE_ENFORCER_TIMEOUT_S=0.05)

    async def slow_confirm(_):
        await asyncio.sleep(10)
        return '{"revealed": true}'

    async def regen(_):
        raise AssertionError("must not reach regenerate")

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=slow_confirm
        )
    )
    assert out == "It's 56." and meta.action == "confirm_failed_open"


def test_retry_recheck_timeout_serves_the_fallback(monkeypatch):
    # First confirm (original) is fast + revealed; the retry re-check times out.
    # The retry is unverified AND the original is a known reveal, so neither is
    # servable -> fallback.
    _enable(monkeypatch, GUIDANCE_ENFORCER_TIMEOUT_S=0.1)
    calls = {"n": 0}

    async def confirm(_):
        i = calls["n"]
        calls["n"] += 1
        if i == 0:
            return '{"revealed": true}'  # original revealed, fast
        await asyncio.sleep(10)  # retry re-check hangs
        return '{"revealed": false}'

    async def regen(_):
        return "A clean guiding question?"

    out, meta = _run(
        enforce_guidance(
            "just give me 7x8", "It's 56.", regen, confirm_generate=confirm
        )
    )
    assert "56" not in out, "served the answer the confirm flagged"
    assert out == _fallback() and meta.action == "fallback_served"
