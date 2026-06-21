"""F1: proxy_chat must derive the admin safety-bypass from the authenticated
session, not the client-supplied X-OpenWebUI-User-Role header — except when the
caller authenticated via the internal API key (Open WebUI)."""
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_with_session(user_id, role):
    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id=user_id, role=role, session_token="t", email="x@y.com")
    return app


def _body():
    return {"model": "m", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _safe():
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def test_non_internal_session_cannot_forge_admin_via_header():
    """A real user session + forged 'admin' header => still the student path."""
    app = _app_with_session(user_id="parent_1", role="user")
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user",
              new_callable=AsyncMock, return_value="profile-1"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_body(),
                           headers={"X-OpenWebUI-User-Role": "admin",
                                    "X-OpenWebUI-User-Id": "parent_1"})
    assert resp.status_code == 200
    chk.assert_called()  # safety ran => the forged admin header did NOT bypass


def test_internal_service_still_trusts_header_role():
    """Open WebUI (internal key) path still uses the forwarded role (admin bypass)."""
    app = _app_with_session(user_id="internal_service", role="admin")
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    with (
        patch("api.routes.ollama_proxy._get_user_from_headers",
              return_value=("admin_1", "admin")),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_body(),
                           headers={"X-OpenWebUI-User-Role": "admin"})
    assert resp.status_code == 200
    chk.assert_not_called()  # admin bypass via trusted internal-key path
