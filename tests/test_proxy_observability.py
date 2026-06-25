"""proxy_chat emits one metadata-only trace per turn and never breaks on trace errors."""
import json
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession
    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="admin", session_token="t", email="i@s")
    return app


def _body():
    return {"model": "snflwr.ai", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _safe():
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def _headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


def test_trace_emitted_on_allowed_turn():
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "snflwr.ai", "message": {"role": "assistant", "content": "2+2=4"}, "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()),
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request", new_callable=AsyncMock, return_value=ollama_resp),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn") as tr,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    tr.assert_called_once()
    kwargs = tr.call_args.kwargs
    assert kwargs["blocked"] is False
    assert "total" in kwargs["latency_ms"]
    # no content leaked into the trace call
    assert "text" not in kwargs and "content" not in kwargs


def test_trace_emitted_on_blocked_input():
    from safety.pipeline import SafetyResult, Severity, Category
    blocked = SafetyResult(is_safe=False, severity=Severity.MAJOR, category=Category.SELF_HARM, reason="x")
    client = TestClient(_app())
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=blocked),
        patch("api.routes.ollama_proxy._record_safety_incident"),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn") as tr,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    tr.assert_called_once()
    assert tr.call_args.kwargs["blocked"] is True


def test_trace_failure_does_not_break_chat():
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "snflwr.ai", "message": {"role": "assistant", "content": "ok"}, "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()),
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request", new_callable=AsyncMock, return_value=ollama_resp),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn", side_effect=RuntimeError("trace down")),
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200   # chat unaffected by a tracing error
