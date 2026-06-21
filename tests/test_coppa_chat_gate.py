"""C1 downstream gate: an under-13 profile that is not coppa_verified must be
blocked from tutoring in the proxy student path, regardless of the safety result."""
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
    # Authenticate as the internal service so role comes from the (student) header.
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="admin", session_token="t", email="i@s")
    return app


def _body():
    return {"model": "m", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _safe():
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def _student_headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


def test_unverified_under13_blocked(monkeypatch):
    from core.authentication import auth_manager
    client = TestClient(_app())
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user",
              new_callable=AsyncMock, return_value="prof_child"),
        patch.object(auth_manager.db, "execute_query",
                     return_value=[{"age": 9, "coppa_verified": 0}]),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("api.routes.ollama_proxy._forward_request", new_callable=AsyncMock) as fwd,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_student_headers())
    assert resp.status_code == 200
    assert "parent" in json.dumps(resp.json()).lower()
    chk.assert_not_called()   # gated before safety/model
    fwd.assert_not_called()


def test_verified_under13_allowed(monkeypatch):
    from core.authentication import auth_manager
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user",
              new_callable=AsyncMock, return_value="prof_child"),
        patch.object(auth_manager.db, "execute_query",
                     return_value=[{"age": 9, "coppa_verified": 1}]),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_body(), headers=_student_headers())
    assert resp.status_code == 200
    chk.assert_called()   # passed the COPPA gate into the safety pipeline


def test_teen_not_coppa_gated(monkeypatch):
    """13+ is never COPPA-gated even if coppa_verified is 0."""
    from core.authentication import auth_manager
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user",
              new_callable=AsyncMock, return_value="prof_teen"),
        patch.object(auth_manager.db, "execute_query",
                     return_value=[{"age": 15, "coppa_verified": 0}]),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_body(), headers=_student_headers())
    assert resp.status_code == 200
    chk.assert_called()
