"""The arithmetic-check note, at the proxy (ARITH_CHECK_ENABLED).

What must hold on the path children use:
  * a wrong shown step puts a note on the FORWARDED copy of the child's turn;
  * the note never names the correct value;
  * no SYSTEM message is ever added -- in Ollama a system message replaces the
    tutor's Modelfile prompt, discarding the persona and safety instructions;
  * the history ledger records the child's ORIGINAL text, because Open WebUI
    will resend it without the note and a mutated record would stop matching;
  * flag off, or correct work: the forwarded body is untouched.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

WRONG = "homework q2: i did 18 x 4 = 72 and 72 + 6 = 76. is that right?"
RIGHT = "homework q2: i did 18 x 4 = 72 and 72 + 6 = 78. is that right?"


def _make_app():
    from fastapi import FastAPI

    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service",
        role="user",
        session_token="test-token",
        email="internal@snflwr.ai",
    )
    return app


def _safe():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def _resp(text):
    return httpx.Response(
        200,
        json={"model": "snflwr.ai", "message": {"role": "assistant", "content": text}, "done": True},
    )


def _run(text, flag):
    from fastapi.testclient import TestClient

    from config import system_config

    pipe = MagicMock()
    pipe.check_input.return_value = _safe()
    pipe.check_output.return_value = _safe()
    fwd = AsyncMock(return_value=_resp("Let's look at your second step together."))
    recorded = []
    with (
        patch.object(system_config, "ARITH_CHECK_ENABLED", flag),
        patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", False),
        patch("api.routes.ollama_proxy.access._get_user_from_headers", return_value=("uid-a", "user")),
        patch("api.routes.ollama_proxy.profile._get_profile_for_user", new=AsyncMock(return_value="profile-a")),
        patch("api.routes.ollama_proxy.transport._forward_request", new=fwd),
        patch("safety.pipeline.safety_pipeline", pipe),
        patch(
            "api.routes.ollama_proxy.chat.history_ledger.record_turn",
            side_effect=lambda pid, msg, reply: recorded.append(dict(msg)),
        ),
    ):
        r = TestClient(_make_app()).post(
            "/api/chat",
            json={"model": "snflwr.ai", "stream": False, "messages": [{"role": "user", "content": text}]},
        )
    assert r.status_code == 200, r.text
    chat_calls = [c for c in fwd.call_args_list if c.args[:2] == ("POST", "/api/chat")]
    assert chat_calls, "the tutor was never called"
    sent = json.loads(chat_calls[0].kwargs["content"])
    return sent, recorded


def test_wrong_step_puts_a_note_on_the_forwarded_turn():
    sent, _ = _run(WRONG, flag=True)
    last = sent["messages"][-1]
    assert last["role"] == "user"
    assert last["content"].startswith(WRONG)
    assert "`72 + 6 = 76` has an arithmetic error" in last["content"]


def test_the_note_never_names_the_correct_value():
    sent, _ = _run(WRONG, flag=True)
    note = sent["messages"][-1]["content"][len(WRONG):]
    assert "78" not in note


def test_no_system_message_is_ever_added():
    sent, _ = _run(WRONG, flag=True)
    assert all(m.get("role") != "system" for m in sent["messages"])


def test_the_ledger_records_the_childs_original_text():
    _, recorded = _run(WRONG, flag=True)
    assert recorded and recorded[-1]["content"] == WRONG


def test_flag_off_leaves_the_turn_untouched():
    sent, _ = _run(WRONG, flag=False)
    assert sent["messages"][-1]["content"] == WRONG


def test_correct_work_leaves_the_turn_untouched():
    sent, _ = _run(RIGHT, flag=True)
    assert sent["messages"][-1]["content"] == RIGHT
