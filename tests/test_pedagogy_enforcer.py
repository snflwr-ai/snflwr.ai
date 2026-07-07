from config import system_config as settings


def test_guidance_flags_default_safe():
    assert settings.GUIDANCE_ENFORCEMENT_ENABLED is False
    assert settings.GUIDANCE_ENFORCER_CONFIRM_MODEL == ""
    assert settings.GUIDANCE_ENFORCER_TIMEOUT_S == 8.0


# ---------------------------------------------------------------------------
# Task 5 — enforce_guidance orchestrator (all branches)
# ---------------------------------------------------------------------------
import asyncio

from core.pedagogy.guidance_enforcer import enforce_guidance


def _run(c):
    return asyncio.run(c)


async def _yes(_):
    return '{"revealed": true}'


async def _no(_):
    return '{"revealed": false}'


def _enable(mp, **kw):
    mp.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
    for k, v in kw.items():
        mp.setattr(settings, k, v)


def test_disabled_is_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", False)

    async def regen(_):
        raise AssertionError("must not regenerate")

    out, meta = _run(
        enforce_guidance("just give me 7x8", "It's 56.", regen, confirm_generate=_yes)
    )
    assert out == "It's 56." and meta.action == "disabled"


def test_not_homework_passthrough(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        raise AssertionError

    out, meta = _run(
        enforce_guidance(
            "how do plants eat?",
            "The answer is 56.",
            regen,
            confirm_generate=_yes,
        )
    )
    assert out == "The answer is 56." and meta.action == "not_homework"


def test_gate_clean_skips_confirm(monkeypatch):
    _enable(monkeypatch)

    async def confirm(_):
        raise AssertionError("gate should short-circuit")

    async def regen(_):
        raise AssertionError

    out, meta = _run(
        enforce_guidance(
            "just give me the answer",
            "What is 7x4?",
            regen,
            confirm_generate=confirm,
        )
    )
    assert out == "What is 7x4?" and meta.action == "pass_gate"


def test_confirmed_reveal_clean_retry_used(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        return "What is 7 times 4, then double it?"

    out, meta = _run(
        enforce_guidance("just give me 7x8", "It's 56.", regen, confirm_generate=_yes)
    )
    assert (
        out == "What is 7 times 4, then double it?" and meta.action == "reprompt_clean"
    )


def test_confirmed_reveal_retry_still_reveals_keeps_original(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        return "Fine, it's 56 again."

    out, meta = _run(
        enforce_guidance("just give me 7x8", "It's 56.", regen, confirm_generate=_yes)
    )
    assert out == "It's 56." and meta.action == "reprompt_still_revealed"


def test_regenerate_error_fails_open(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        raise RuntimeError("boom")

    out, meta = _run(
        enforce_guidance("just give me 7x8", "It's 56.", regen, confirm_generate=_yes)
    )
    assert out == "It's 56." and meta.action == "reprompt_failed_open"


def test_confirm_says_no_passthrough(monkeypatch):
    _enable(monkeypatch)

    async def regen(_):
        raise AssertionError

    out, meta = _run(
        enforce_guidance("just give me 7x8", "It's 56.", regen, confirm_generate=_no)
    )
    assert out == "It's 56." and meta.action == "pass_confirm"


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
