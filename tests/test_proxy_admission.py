"""Proxy behaviour under load and on hardware that cannot tutor.

Two child-visible rules:

1. **Out of capacity is a busy message, not a worse answer.** Measured
   2026-09-17: at 20 concurrent students, 40 of 60 replies became the canned
   withholding fallback because the reveal confirm timed out while queued.
2. **Hardware below the quality floor does not tutor.** e4b and 12b never met
   the tutoring bars; a box that cannot run a certified backbone says so.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from core import serving_plan
from core.inference.base import EngineOverloaded


def _make_app():
    from fastapi import FastAPI

    import api.routes.ollama_proxy as proxy_mod
    from api.exception_handlers import register_exception_handlers
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    register_exception_handlers(app)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service",
        role="user",
        session_token="test-token",
        email="internal@snflwr.ai",
    )
    return app


def _safe_result():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def _chat_resp(text="Here is a hint."):
    return httpx.Response(
        200,
        json={"model": "snflwr.ai-31b",
              "message": {"role": "assistant", "content": text},
              "done": True, "prompt_eval_count": 10, "eval_count": 5},
    )


def _plan(**over):
    base = dict(engine="ollama", tutor_model="snflwr.ai-31b", quality_tier="certified",
                tutoring_enabled=True, num_ctx=16384, max_concurrent_requests=1,
                reason="test")
    base.update(over)
    return serving_plan.ServingPlan(**base)


def _post(client, question="What is 2 + 2?"):
    return client.post(
        "/api/chat",
        json={"model": "snflwr.ai-31b",
              "messages": [{"role": "user", "content": question}],
              "stream": False},
        headers={"X-OpenWebUI-User-Id": "uid-1", "X-OpenWebUI-User-Role": "user"},
    )


class TestBusyInsteadOfDegradedAnswers:
    def _run(self, *, overloaded: bool, stream: bool = False):
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()

        turn = MagicMock()
        if overloaded:
            turn.return_value.__aenter__ = AsyncMock(side_effect=EngineOverloaded("full"))
        else:
            turn.return_value.__aenter__ = AsyncMock(return_value=None)
        turn.return_value.__aexit__ = AsyncMock(return_value=False)
        fake_client = MagicMock()
        fake_client.turn = turn
        fake_client.plan = _plan()

        with (
            patch("api.routes.ollama_proxy.access._get_user_from_headers",
                  return_value=("uid-1", "user")),
            patch("api.routes.ollama_proxy.profile._get_profile_for_user",
                  new=AsyncMock(return_value="profile-1")),
            patch("api.routes.ollama_proxy.profile._resolve_age", return_value=11),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            patch("api.routes.ollama_proxy.chat.coppa_consent_block_reason",
                  return_value=None),
            patch("core.inference.client.get_client", return_value=fake_client),
            patch("core.serving_plan.get_plan", return_value=_plan()),
            patch("api.routes.ollama_proxy.transport._forward_request",
                  new=AsyncMock(return_value=_chat_resp())),
        ):
            client = TestClient(_make_app())
            return client.post(
                "/api/chat",
                json={"model": "snflwr.ai-31b",
                      "messages": [{"role": "user", "content": "What is 2 + 2?"}],
                      "stream": stream},
                headers={"X-OpenWebUI-User-Id": "uid-1", "X-OpenWebUI-User-Role": "user"},
            )

    def test_overloaded_turn_returns_a_busy_message(self):
        resp = self._run(overloaded=True)
        assert resp.status_code == 200
        text = resp.json()["message"]["content"]
        assert "busy" in text.lower() or "right now" in text.lower()

    def test_the_busy_message_is_not_the_withholding_fallback(self):
        """The fallback teaches; the busy message must not pretend to."""
        resp = self._run(overloaded=True)
        text = resp.json()["message"]["content"].lower()
        assert "get this one yourself" not in text

    def test_streamed_requests_get_a_busy_ndjson_chunk(self):
        resp = self._run(overloaded=True, stream=True)
        assert resp.status_code == 200
        first = json.loads(resp.text.strip().splitlines()[0])
        assert first["message"]["content"]

    def test_capacity_available_serves_the_tutor_normally(self):
        resp = self._run(overloaded=False)
        assert resp.json()["message"]["content"] == "Here is a hint."


class TestQualityFloor:
    def test_hardware_below_the_floor_refuses_to_tutor(self):
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()
        unsupported = _plan(tutoring_enabled=False, quality_tier="unsupported",
                            tutor_model=None,
                            reason="no certified backbone fits 8.0 GB VRAM")

        with (
            patch("api.routes.ollama_proxy.access._get_user_from_headers",
                  return_value=("uid-1", "user")),
            patch("api.routes.ollama_proxy.profile._get_profile_for_user",
                  new=AsyncMock(return_value="profile-1")),
            patch("api.routes.ollama_proxy.profile._resolve_age", return_value=11),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            patch("api.routes.ollama_proxy.chat.coppa_consent_block_reason",
                  return_value=None),
            patch("core.serving_plan.get_plan", return_value=unsupported),
            patch("api.routes.ollama_proxy.transport._forward_request",
                  new=AsyncMock(return_value=_chat_resp())) as fwd,
        ):
            resp = _post(TestClient(_make_app()))

        assert resp.status_code == 200
        assert "not available" in resp.json()["message"]["content"].lower()
        fwd.assert_not_called()  # no model was ever asked to tutor

    def test_a_certified_plan_does_not_block(self):
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()

        with (
            patch("api.routes.ollama_proxy.access._get_user_from_headers",
                  return_value=("uid-1", "user")),
            patch("api.routes.ollama_proxy.profile._get_profile_for_user",
                  new=AsyncMock(return_value="profile-1")),
            patch("api.routes.ollama_proxy.profile._resolve_age", return_value=11),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            patch("api.routes.ollama_proxy.chat.coppa_consent_block_reason",
                  return_value=None),
            patch("core.serving_plan.get_plan", return_value=_plan()),
            patch("api.routes.ollama_proxy.transport._forward_request",
                  new=AsyncMock(return_value=_chat_resp())),
        ):
            resp = _post(TestClient(_make_app()))

        assert resp.json()["message"]["content"] == "Here is a hint."
