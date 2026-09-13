"""A child must never receive an empty reply because the model was thinking.

gemma4 is a reasoning model. With thinking on it spends its budget on
`message.thinking` and returns a shorter `message.content` -- sometimes none at
all. The proxy strips `thinking` before serving (unvetted chain-of-thought must
never reach a child), so an all-thinking reply arrives as an EMPTY BUBBLE.

Measured against the deployed tutor: "Why is the sky blue?" returned 0 content
characters and 2293 thinking characters. The admin bypass has set think=False
since it was written; the student path never did.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


def _app():
    from fastapi import FastAPI

    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service",
        role="user",
        session_token="t",
        email="internal@snflwr.ai",
    )
    return app


def _safe():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(
        is_safe=True, severity=Severity.NONE, category=Category.VALID, reason=""
    )


def _capture_forwarded(client_body: dict) -> dict:
    """Post as a student and return the body the proxy forwarded to Ollama."""
    from fastapi.testclient import TestClient

    from config import system_config

    seen: dict = {}

    async def _fwd(method, path, content=None, headers=None, **kw):
        seen.update(json.loads(content))
        return httpx.Response(
            200,
            json={
                "model": "snflwr.ai",
                "done": True,
                "message": {"role": "assistant", "content": "A guiding answer."},
            },
        )

    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe()
    pipeline.check_output.return_value = _safe()

    with (
        patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", False),
        patch("api.routes.ollama_proxy.access._get_user_from_headers",
              return_value=("uid-t", "user")),
        patch("api.routes.ollama_proxy.profile._get_profile_for_user",
              new=AsyncMock(return_value="profile-t")),
        patch("api.routes.ollama_proxy.transport._forward_request", new=_fwd),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        TestClient(_app()).post(
            "/api/chat",
            json=client_body,
            headers={"X-OpenWebUI-User-Id": "uid-t", "X-OpenWebUI-User-Role": "user"},
        )
    return seen


class TestStudentThinkingSuppressed:
    def test_think_is_forced_off_for_a_student(self):
        sent = _capture_forwarded(
            {"model": "snflwr.ai", "stream": False,
             "messages": [{"role": "user", "content": "Why is the sky blue?"}]}
        )
        assert sent.get("think") is False, (
            "thinking left enabled for a student: an all-thinking reply reaches "
            "the child as an empty bubble"
        )

    def test_a_client_asking_for_thinking_is_overridden(self):
        # A student-controlled client must not be able to turn it back on.
        sent = _capture_forwarded(
            {"model": "snflwr.ai", "stream": False, "think": True,
             "messages": [{"role": "user", "content": "Why is the sky blue?"}]}
        )
        assert sent.get("think") is False

    def test_the_rest_of_the_body_is_preserved(self):
        sent = _capture_forwarded(
            {"model": "snflwr.ai", "stream": False,
             "options": {"temperature": 0.4},
             "messages": [{"role": "user", "content": "Why is the sky blue?"}]}
        )
        assert sent["options"] == {"temperature": 0.4}
        assert sent["messages"][-1]["content"] == "Why is the sky blue?"
