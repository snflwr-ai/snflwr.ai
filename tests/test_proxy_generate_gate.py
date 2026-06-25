"""P0: the raw Ollama generation + model-management proxy endpoints must NOT be
reachable by non-admin (student) sessions.

Only ``/api/chat`` runs the child-safety pipeline. ``/api/generate`` and
``/api/embed(dings)`` are raw inference that would return UNFILTERED model output
to a child, and ``/api/pull|delete|copy`` mutate the model set. None of these are
part of the student flow (Open WebUI drives everything user-facing through
``/api/chat``), so a non-admin session must be rejected *before* the request is
forwarded to Ollama. Role is resolved with the same anti-forgery rule as
``proxy_chat``: the ``X-OpenWebUI-User-Role`` header is trusted only when the
caller authenticated via the internal API key (``user_id == "internal_service"``).
"""
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
        user_id=user_id, role=role, session_token="t", email="x@y.com"
    )
    return app


# Endpoints that must be admin-only, as (HTTP method, path).
GATED = [
    ("post", "/api/generate"),
    ("post", "/api/embed"),
    ("post", "/api/embeddings"),
    ("post", "/api/pull"),
    ("delete", "/api/delete"),
    ("post", "/api/copy"),
]


def test_student_session_cannot_reach_gated_endpoints():
    """A real (non-admin) user session is rejected and Ollama is never contacted."""
    app = _app_with_session(user_id="parent_1", role="user")
    client = TestClient(app)
    for method, path in GATED:
        with patch(
            "api.routes.ollama_proxy._forward_request", new_callable=AsyncMock
        ) as fwd:
            resp = client.request(method.upper(), path, json={"model": "m", "prompt": "hi"})
        assert resp.status_code == 403, f"{path} should be forbidden for students"
        fwd.assert_not_called()  # must block BEFORE forwarding to Ollama


def test_forged_admin_header_cannot_unlock_gated_endpoints():
    """A real user session + forged 'admin' header is still rejected."""
    app = _app_with_session(user_id="parent_1", role="user")
    client = TestClient(app)
    for method, path in GATED:
        with patch(
            "api.routes.ollama_proxy._forward_request", new_callable=AsyncMock
        ) as fwd:
            resp = client.request(
                method.upper(),
                path,
                json={"model": "m", "prompt": "hi"},
                headers={
                    "X-OpenWebUI-User-Role": "admin",
                    "X-OpenWebUI-User-Id": "parent_1",
                },
            )
        assert resp.status_code == 403, f"{path} forged-admin must stay forbidden"
        fwd.assert_not_called()


def test_internal_service_with_admin_role_is_allowed():
    """Open WebUI (internal key) acting as an admin still reaches the endpoint."""
    app = _app_with_session(user_id="internal_service", role="admin")
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"response": "ok", "done": True})
    for method, path in GATED:
        with (
            patch(
                "api.routes.ollama_proxy._get_user_from_headers",
                return_value=("admin_1", "admin"),
            ),
            patch(
                "api.routes.ollama_proxy._forward_request",
                new_callable=AsyncMock,
                return_value=ollama_resp,
            ) as fwd,
        ):
            resp = client.request(method.upper(), path, json={"model": "m"})
        assert resp.status_code == 200, f"{path} should be allowed for admin"
        fwd.assert_called_once()


def test_real_admin_session_is_allowed():
    """A genuine admin *session* (not via internal key) reaches the endpoint."""
    app = _app_with_session(user_id="admin_1", role="admin")
    client = TestClient(app)
    ollama_resp = httpx.Response(200, json={"response": "ok", "done": True})
    for method, path in GATED:
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=ollama_resp,
        ) as fwd:
            resp = client.request(method.upper(), path, json={"model": "m"})
        assert resp.status_code == 200, f"{path} should be allowed for admin session"
        fwd.assert_called_once()
