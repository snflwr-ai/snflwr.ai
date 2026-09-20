"""A safety classifier must never inherit the tutor's persona.

Measured twice, a week apart, on this exact failure:

    confirm on the tutor model    0/20 recall (2026-09-13)
    confirm on the tutor model    0/27 recall, 27 of 27 verdicts UNPARSEABLE (2026-09-20)
    same weights, persona replaced   22/27 on the same cases

The tutor's system prompt caps answer length hard, so the classifier emits `{"`
and stops. Unparseable fails OPEN, so every reveal is served -- with no error, a
healthy container, and a reveal rate that has quadrupled.

The fallback chain is CONFIRM_MODEL -> GATE_MODEL -> tutor, and BOTH env vars
default to "" (config.py), while the production compose passes neither. So the
dangerous branch is the DEFAULT branch, not an exotic one.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.ollama_proxy import chat as chat_mod

ROOT = Path(__file__).resolve().parents[1]


def _payload_of(mock_forward):
    """The JSON body the proxy would have sent."""
    import json

    kwargs = mock_forward.await_args.kwargs
    return json.loads(kwargs["content"] if "content" in kwargs else mock_forward.await_args.args[2])


@pytest.mark.asyncio
async def test_calling_the_tutor_replaces_its_system_prompt():
    resp = type("R", (), {"text": '{"revealed": false}', "json": lambda self: {"message": {"content": "{}"}}})()
    with patch.object(chat_mod.transport, "_forward_request", new=AsyncMock(return_value=resp)) as fwd:
        await chat_mod._pedagogy_oneshot(
            "is this a reveal?", "snflwr.ai-31b", {}, system=chat_mod._CLASSIFIER_SYSTEM
        )
    body = _payload_of(fwd)
    roles = [m["role"] for m in body["messages"]]
    assert roles[0] == "system", (
        "no system message: the call would inherit the tutor's persona, which "
        "measured 0/27 recall with every verdict unparseable"
    )
    assert "not a tutor" in body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_a_dedicated_classifier_model_gets_no_override():
    """gemma4:e4b base weights carry no persona; overriding would be noise."""
    resp = type("R", (), {"text": "{}", "json": lambda self: {"message": {"content": "{}"}}})()
    with patch.object(chat_mod.transport, "_forward_request", new=AsyncMock(return_value=resp)) as fwd:
        await chat_mod._pedagogy_oneshot("is this a reveal?", "gemma4:e4b", {})
    body = _payload_of(fwd)
    assert [m["role"] for m in body["messages"]] == ["user"]


def test_the_confirm_closure_overrides_only_for_the_tutor():
    """Read the wiring: the override must be tied to 'the model IS the tutor'.

    A source-level check because the closure is built inside the request handler
    and the branch that matters is the DEFAULT one -- unconfigured env, where the
    chain falls through to the tutor.
    """
    src = (chat_mod.__file__).replace(".pyc", ".py")
    text = open(src).read()
    assert "_CLASSIFIER_SYSTEM if confirm_model == model else None" in text, (
        "the confirm no longer replaces the persona when it falls back to the "
        "tutor model -- that is the 0/27 configuration, reachable by default"
    )


def test_the_dangerous_fallback_is_actually_reachable_by_default():
    """Guard the guard: these tests matter only while the tutor fallback is the
    DEFAULT path. If either variable stops defaulting to empty, revisit them
    rather than trusting them."""
    import re

    src = open(ROOT / "config.py").read()
    for var in ("GUIDANCE_ENFORCER_CONFIRM_MODEL", "GUIDANCE_GATE_MODEL"):
        m = re.search(rf'os\.getenv\(\s*"{var}",\s*"([^"]*)"', src)
        assert m, f"{var} is no longer read with an explicit default"
        assert m.group(1) == "", (
            f"{var} now defaults to {m.group(1)!r}. The tutor fallback may no "
            "longer be the default path; re-check these tests' premise."
        )
