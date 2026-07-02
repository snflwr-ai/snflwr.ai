"""Characterization guard for the ollama_proxy refactor: pins the student guard
ORDER and the router surface. Passes against the pre-split module and every
step of the split."""
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.ollama_proxy as proxy_mod
from core.authentication import AuthSession


def _app():
    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="admin", session_token="t", email="i@s"
    )
    return app


def _body():
    return {"model": "snflwr.ai", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


ROUTES = {
    "/api/chat", "/api/tags", "/api/show", "/api/generate", "/api/embed",
    "/api/embeddings", "/api/delete", "/api/pull", "/api/copy", "/api/version",
    "/api/health",
}


def test_all_routes_registered():
    paths = {r.path for r in proxy_mod.router.routes}
    missing = ROUTES - paths
    assert not missing, f"router missing routes: {missing}"


def test_direct_import_surface_intact():
    from api.routes.ollama_proxy import (  # noqa: F401
        router,
        _forward_request,
        _get_profile_for_user,
        _get_user_from_headers,
        _extract_last_user_message,
        _ollama_block_response,
        _filter_tags_for_students,
        _filter_show_for_students,
        _stream_chat_from_ollama,
    )


def test_rate_limit_precedes_profile_lookup():
    # When the rate limiter denies, the profile is never looked up and Ollama is
    # never called — proves rate-limit is an earlier gate than profile/safety.
    client = TestClient(_app())
    with (
        patch.object(proxy_mod.rate_limiter, "check_rate_limit",
                     return_value=(False, {"retry_after": 42})),
        patch("api.routes.ollama_proxy.profile._get_profile_for_user",
              new_callable=AsyncMock) as prof,
        patch("api.routes.ollama_proxy.transport._forward_request",
              new_callable=AsyncMock) as fwd,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    prof.assert_not_called()
    fwd.assert_not_called()


def test_no_profile_precedes_forward():
    # A student with no real profile is blocked before Ollama is called.
    client = TestClient(_app())
    with (
        patch("api.routes.ollama_proxy.profile._get_profile_for_user",
              new_callable=AsyncMock, return_value="safety_required_stud_1"),
        patch("api.routes.ollama_proxy.transport._forward_request",
              new_callable=AsyncMock) as fwd,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    fwd.assert_not_called()
