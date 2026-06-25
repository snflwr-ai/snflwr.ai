"""Hardening: the INTERNAL_API_KEY authenticates Open WebUI as a trusted *relay*
(identity assertion + ownership bypass for the students it forwards), but must
NOT wield admin authority. A leaked key must not reach admin routes, bypass the
child-safety pipeline, or manage models. Authority requires a genuine admin
*session* (a real admin login), never the internal service key + a forwarded
X-OpenWebUI-User-Role header.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _internal_session():
    from core.authentication import AuthSession

    # role="admin" is retained so the relay keeps its ownership bypass, but the
    # user_id marks it as the service principal.
    return AuthSession(
        user_id="internal_service", role="admin", session_token="k", email="i@x.com"
    )


def _genuine_admin():
    from core.authentication import AuthSession

    return AuthSession(
        user_id="admin_1", role="admin", session_token="t", email="a@x.com"
    )


# --- require_admin must reject the internal service key --------------------

@pytest.mark.asyncio
async def test_require_admin_rejects_internal_service():
    from api.middleware.auth import require_admin

    with pytest.raises(HTTPException) as exc:
        await require_admin(session=_internal_session())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_allows_genuine_admin():
    from api.middleware.auth import require_admin

    result = await require_admin(session=_genuine_admin())
    assert result.user_id == "admin_1"


# --- proxy: internal service must NOT bypass safety via a forged header -----

def _app_with_session(session):
    import api.routes.ollama_proxy as proxy_mod

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: session
    return app


def _safe():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(
        is_safe=True, severity=Severity.NONE, category=Category.VALID, reason=""
    )


def test_internal_service_cannot_bypass_safety_with_admin_header():
    """internal_service + X-OpenWebUI-User-Role: admin must still run the
    safety pipeline — consistent with chat.py, which already refuses this."""
    app = _app_with_session(_internal_session())
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
        resp = client.post(
            "/api/chat",
            json={"model": "m", "stream": False,
                  "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-OpenWebUI-User-Role": "admin",
                     "X-OpenWebUI-User-Id": "kid_1"},
        )
    assert resp.status_code == 200
    chk.assert_called()  # safety ran — the forged admin header did NOT bypass


def test_genuine_admin_session_still_bypasses_safety():
    """A real admin USER session (direct, not via the internal key) still
    bypasses the pipeline."""
    app = _app_with_session(_genuine_admin())
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    with (
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()) as chk,
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post(
            "/api/chat",
            json={"model": "m", "stream": False,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    chk.assert_not_called()  # genuine admin bypasses


def test_internal_service_cannot_reach_model_management():
    """/api/generate (and the model-mgmt endpoints) require a genuine admin
    session — the internal key + forged admin header is rejected."""
    app = _app_with_session(_internal_session())
    client = TestClient(app)
    with patch("api.routes.ollama_proxy._forward_request",
               new_callable=AsyncMock) as fwd:
        resp = client.post("/api/generate", json={"model": "m", "prompt": "x"},
                           headers={"X-OpenWebUI-User-Role": "admin"})
    assert resp.status_code == 403
    fwd.assert_not_called()


def test_genuine_admin_session_reaches_model_management():
    app = _app_with_session(_genuine_admin())
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"response": "ok"})
    with patch("api.routes.ollama_proxy._forward_request",
               new_callable=AsyncMock, return_value=ollama_resp) as fwd:
        resp = client.post("/api/generate", json={"model": "m"})
    assert resp.status_code == 200
    fwd.assert_called_once()
