"""The speech-act adjudicator, at the route level.

main's DEROGATORY word list is the TRIGGER; the adjudicator decides. It can
only ever RELEASE, so it cannot introduce a false positive — but it can
release a real insult the trigger caught, which is a new false negative.

⚠️ `test_a_release_verdict_UNBLOCKS_the_turn` is the positive control and is
load-bearing: every "the block was kept" assertion below is green by
construction if the adjudicator can never fire at all. Mutation-checked.

⚠️ AND A POSITIVE CONTROL IS ONLY AS GOOD AS ITS FIXTURE. The first version of
this file did `pipeline.SafetyResult = SafetyResult` on a bare MagicMock, which
ADDS whatever attribute you touch. That built the interface the route ASSUMED
rather than the one production has, so the control passed while the release
path raised AttributeError on every real turn.

**A mock that can have any attribute cannot catch an interface error: spec it.**
Hence `MagicMock(spec=SafetyPipeline)` below. Mutation-checked: restoring
`safety_pipeline.SafetyResult(` in the route now FAILS the control.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import api.routes.ollama_proxy.chat as chat_mod
from safety.pipeline import Category, SafetyPipeline, SafetyResult, Severity
from tests.test_ollama_proxy import _chat_body, _make_app, _safe_result

TUTOR_REPLY = "Pond scum is algae and bacteria."
BLOCK_TEXT = "Let's keep our conversation kind and respectful."


def _derogatory_block():
    return SafetyResult(
        is_safe=False,
        severity=Severity.MAJOR,
        category=Category.DEROGATORY,
        reason="derogatory (EN)",
        modified_content=BLOCK_TEXT,
    )


def _drive(verdict_json, enabled=True, result=None, raises=None):
    """Drive one turn that main's word list flags as DEROGATORY."""
    from fastapi.testclient import TestClient

    client = TestClient(_make_app(), raise_server_exceptions=False)
    # ⚠️ spec=SafetyPipeline. A bare MagicMock grows ANY attribute you touch,
    # so the previous version's `pipeline.SafetyResult = SafetyResult` ADDED
    # the attribute the route wrongly assumed -- building the interface the
    # code expected instead of the one production has. The positive control
    # then could not fail, while the release path raised AttributeError on
    # every real turn.
    pipeline = MagicMock(spec=SafetyPipeline)
    pipeline.check_input.return_value = result or _derogatory_block()
    pipeline.check_output.return_value = _safe_result()
    pipeline.get_safe_response.return_value = BLOCK_TEXT

    async def _gen(_prompt):
        if raises is not None:
            raise raises
        return verdict_json

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "done": True,
                        "message": {"role": "assistant", "content": TUTOR_REPLY},
                    },
                )
            ),
        ),
        patch.object(chat_mod.safety_config, "SPEECH_ACT_ADJUDICATOR_ENABLED", enabled),
        patch.object(chat_mod, "_make_adjudicator_generate", lambda h, m: _gen),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        resp = client.post("/api/chat", json=_chat_body(text="what is pond scum"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return (body.get("message") or {}).get("content", "")


# ---------------------------------------------------------------------------
# POSITIVE CONTROL — must pass or everything below is vacuous.
# ---------------------------------------------------------------------------


def test_a_release_verdict_UNBLOCKS_the_turn():
    served = _drive('{"act":"curriculum","decision":"release"}')
    assert served == TUTOR_REPLY, (
        "the adjudicator did not release a curriculum turn; every "
        "block-was-kept assertion in this file is therefore vacuous"
    )


# ---------------------------------------------------------------------------
# Fail-CLOSED: every failure keeps the block, i.e. today's behaviour.
# ---------------------------------------------------------------------------


def test_a_block_verdict_keeps_the_block():
    assert _drive('{"act":"insulting","decision":"block"}') == BLOCK_TEXT


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty response — gemma4 without think:false
        '{"',  # truncated, the shape that made the reveal confirm fail OPEN
        "I think this is fine",  # prose, no JSON
        '{"act":"nonsense","decision":"release"}',  # unknown act
        '{"act":"reporting","decision":"block"}',  # decision disagrees with act
    ],
)
def test_an_unusable_verdict_KEEPS_the_block(raw):
    """⭐ Fail-closed here IS today's behaviour, so the failure mode is "no
    improvement" and never "new harm". The truncated `{"` case is the exact
    shape that made the reveal confirm fail OPEN and serve the leak."""
    assert _drive(raw) == BLOCK_TEXT


def test_a_raising_classifier_KEEPS_the_block():
    assert _drive("", raises=RuntimeError("model evicted")) == BLOCK_TEXT


# ---------------------------------------------------------------------------
# Scope.
# ---------------------------------------------------------------------------


def test_the_flag_defaults_OFF():
    from config import safety_config

    assert safety_config.SPEECH_ACT_ADJUDICATOR_ENABLED is False, (
        "the adjudicator must stay off until the WIRED latency measurement "
        "lands; its p90 over 262 calls was 9.03s against an 8s bar scored on 40"
    )


def test_disabled_flag_means_the_adjudicator_never_runs():
    assert (
        _drive('{"act":"curriculum","decision":"release"}', enabled=False) == BLOCK_TEXT
    )


def test_a_NON_derogatory_block_is_never_adjudicated():
    """Scoped to DEROGATORY on purpose. A self-harm or exploitation block must
    not be releasable by a classifier whose taxonomy is about insults."""
    hate = SafetyResult(
        is_safe=False,
        severity=Severity.CRITICAL,
        category=Category.SELF_HARM,
        reason="self harm",
        modified_content=BLOCK_TEXT,
    )
    assert (
        _drive('{"act":"curriculum","decision":"release"}', result=hate) == BLOCK_TEXT
    )


def test_a_SAFE_turn_is_untouched():
    """The adjudicator only ever sees blocked turns, so it cannot introduce a
    false positive."""
    assert (
        _drive('{"act":"insulting","decision":"block"}', result=_safe_result())
        == TUTOR_REPLY
    )


def test_the_adjudicator_judges_the_CURRENT_turn_not_the_history():
    """⚠️ The word-list trigger fires on every student turn CONCATENATED, but
    the adjudicator must judge only the current one.

    A child who once wrote an insult has it in the concatenation forever, so
    judging the blob would block EVERY later turn -- punishment for history,
    when that earlier turn was already blocked when it was sent. It also
    matches what cold set 6 validated: single messages.
    """
    from fastapi.testclient import TestClient

    seen = {}

    async def _gen(prompt):
        seen["prompt"] = prompt
        return '{"act":"curriculum","decision":"release"}'

    client = TestClient(_make_app(), raise_server_exceptions=False)
    pipeline = MagicMock(spec=SafetyPipeline)
    pipeline.check_input.return_value = _derogatory_block()
    pipeline.check_output.return_value = _safe_result()
    pipeline.get_safe_response.return_value = BLOCK_TEXT

    body = {
        "model": "test-model",
        "stream": False,
        "messages": [
            {"role": "user", "content": "you are such a loser"},
            {"role": "assistant", "content": BLOCK_TEXT},
            {"role": "user", "content": "what are fatty acids"},
        ],
    }

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "done": True,
                        "message": {"role": "assistant", "content": TUTOR_REPLY},
                    },
                )
            ),
        ),
        patch.object(chat_mod.safety_config, "SPEECH_ACT_ADJUDICATOR_ENABLED", True),
        patch.object(chat_mod, "_make_adjudicator_generate", lambda h, m: _gen),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        client.post("/api/chat", json=body)

    assert "prompt" in seen, "the adjudicator was never called"
    assert "fatty acids" in seen["prompt"], "the current turn was not sent"
    assert "loser" not in seen["prompt"], (
        "the adjudicator was given the CONCATENATED history; a single past "
        "insult would then block every later turn"
    )


def test_a_flag_from_SERVED_HISTORY_skips_the_model_call():
    """⭐ The latency cascade prime-69 found.

    A RELEASED turn is served, so it IS recorded in the history ledger (a
    BLOCKED turn is not, which is why today's word list doesn't keep re-firing
    after a refusal). So once "they call me a freak" is released, the
    concatenation keeps tripping the word list and every later turn of the
    session would pay another ~6s classifier call to be released again.

    If the CURRENT turn doesn't trip the pattern stage by itself, the flag came
    only from served history: release with NO model call.
    """
    from fastapi.testclient import TestClient

    calls = []

    async def _gen(prompt):
        calls.append(prompt)
        return '{"act":"curriculum","decision":"release"}'

    client = TestClient(_make_app(), raise_server_exceptions=False)
    pipeline = MagicMock(spec=SafetyPipeline)
    pipeline.check_input.return_value = _derogatory_block()
    pipeline.check_output.return_value = _safe_result()
    pipeline.get_safe_response.return_value = BLOCK_TEXT

    body = {
        "model": "test-model",
        "stream": False,
        "messages": [
            {"role": "user", "content": "they call me a freak at school"},
            {"role": "assistant", "content": "That sounds hard."},
            # Trips NOTHING by itself; the flag can only have come from above.
            {"role": "user", "content": "what is the water cycle"},
        ],
    }

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "done": True,
                        "message": {"role": "assistant", "content": TUTOR_REPLY},
                    },
                )
            ),
        ),
        patch.object(chat_mod.safety_config, "SPEECH_ACT_ADJUDICATOR_ENABLED", True),
        patch.object(chat_mod, "_make_adjudicator_generate", lambda h, m: _gen),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        resp = client.post("/api/chat", json=body)

    served = (resp.json().get("message") or {}).get("content", "")
    assert served == TUTOR_REPLY, "the innocent current turn was not released"
    assert calls == [], (
        f"a model call was made for a flag that came only from served history: "
        f"{calls!r} — every later turn of the session would pay ~6s"
    )
