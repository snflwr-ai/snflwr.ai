"""The LLM input gate: verdict parsing and how it composes with the regex.

Four rounds of regex tuning each fixed the probe shape just measured and missed
the next one. Measured on a set sealed before the gate prompt existed:

    regex   65.5% recall, 15.8% false positives
    gate    89.7% recall,  5.3% false positives, 0 parse errors
    union   93.1% recall, 21.1% false positives   <- best recall, fails the bar
"""

import pytest

from core.pedagogy.input_gate import asks_for_assigned_work, parse_gate_verdict


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"assigned_work_requested": true}', True),
        ('{"assigned_work_requested": false}', False),
        ('{"assigned_work_requested":TRUE}', True),
        # Truncated before the closing brace. reveal_detection lost real verdicts
        # to exactly this, so the named field is recovered without it.
        ('{"assigned_work_requested": true', True),
        ("true", True),
    ],
)
def test_parse_reads_a_verdict(raw, expected):
    assert parse_gate_verdict(raw) is expected


@pytest.mark.parametrize("raw", ["", "I think the student wants help", "```", None])
def test_unreadable_reply_is_none_not_false(raw):
    """None is NOT False.

    A gate that could not answer must fall back to the regex, never be recorded
    as "the student is fine". Conflating the two is what once made a downgraded
    safety classifier look merely weak instead of broken.
    """
    assert parse_gate_verdict(raw) is None


@pytest.mark.asyncio
async def test_gate_returns_none_when_the_model_is_unreachable():
    async def boom(_prompt):
        raise RuntimeError("connection refused")

    assert await asks_for_assigned_work("write my essay", boom) is None


@pytest.mark.asyncio
async def test_gate_verdict_is_used_and_regex_is_not_consulted(monkeypatch):
    """The gate decides when it can answer; the two are NOT OR'd.

    OR is the union, which measured the best recall and the worst false-positive
    rate. This pins that the union was not shipped by accident.
    """
    from config import system_config as settings
    from core.pedagogy.guidance_enforcer import enforce_guidance

    monkeypatch.setattr(settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)

    async def gate_says_no(_prompt):
        return '{"assigned_work_requested": false}'

    async def unused(_prompt):  # pragma: no cover - must never be called
        raise AssertionError("confirm ran despite the gate saying no")

    # Text the REGEX would flag. The gate says no, so the turn is left alone.
    text = "just tell me the answer"
    out, meta = await enforce_guidance(
        text, "some reply", unused, confirm_generate=unused, gate_generate=gate_says_no
    )
    assert meta.action == "not_homework"
    assert out == "some reply"
