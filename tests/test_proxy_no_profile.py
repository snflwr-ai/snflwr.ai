"""A student with no real child profile is blocked from /api/chat; a student
with a real profile proceeds. Modeled on tests/test_proxy_observability.py."""
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.profile_gate import NO_PROFILE_MESSAGE


def _app():
    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    # internal_service relay => is_genuine_admin False => STUDENT path
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="admin", session_token="t", email="i@s"
    )
    return app


def _body():
    return {"model": "snflwr.ai", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


def test_student_without_profile_is_blocked():
    client = TestClient(_app())
    with (
        patch("api.routes.ollama_proxy.profile._get_profile_for_user", new_callable=AsyncMock,
              return_value="safety_required_stud_1"),
        patch("api.routes.ollama_proxy.transport._forward_request", new_callable=AsyncMock) as fwd,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    assert NO_PROFILE_MESSAGE in resp.json()["message"]["content"]
    fwd.assert_not_called()  # upstream Ollama never reached


def test_student_with_real_profile_proceeds():
    client = TestClient(_app())
    ollama_resp = httpx.Response(
        200, json={"model": "snflwr.ai", "message": {"role": "assistant", "content": "2+2=4"}, "done": True}
    )
    from safety.pipeline import SafetyResult, Severity, Category
    safe = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")
    with (
        patch("api.routes.ollama_proxy.profile._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=safe),
        patch("safety.pipeline.safety_pipeline.check_output", return_value=safe),
        patch("api.routes.ollama_proxy.transport._forward_request", new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    assert NO_PROFILE_MESSAGE not in resp.json().get("message", {}).get("content", "")
