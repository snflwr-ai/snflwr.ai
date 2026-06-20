"""License gate in proxy_chat: students need an active subscription/trial.

Mirrors the safety-gate tests' app construction (see test_ollama_proxy.py).
"""
import json
from unittest.mock import patch, AsyncMock

import httpx


def _make_app():
    from fastapi import FastAPI
    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service",
        role="admin",
        session_token="test-token",
        email="internal@snflwr.ai",
    )
    return app


def _chat_body():
    return {"model": "m", "stream": False,
            "messages": [{"role": "user", "content": "hi"}]}


def _unlicensed():
    from core.licensing import LicenseState
    return LicenseState("unlicensed", False, None, None, "no token")


def _active():
    from core.licensing import LicenseState
    return LicenseState("active", True, "family", 9999999999, "valid")


def test_unlicensed_student_blocked_no_model_call():
    from fastapi.testclient import TestClient
    from config import system_config

    client = TestClient(_make_app())
    with (
        patch.object(system_config, "LICENSE_ENFORCED", True),
        patch("core.licensing.current_state", return_value=_unlicensed()),
        patch("api.routes.ollama_proxy._get_user_from_headers",
              return_value=("stud_1", "user")),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock) as mock_fwd,
        patch("safety.pipeline.safety_pipeline.check_input") as mock_safety,
    ):
        resp = client.post("/api/chat", json=_chat_body())

    assert resp.status_code == 200
    assert "subscription" in json.dumps(resp.json()).lower()
    mock_safety.assert_not_called()   # gate short-circuits before the safety pipeline
    mock_fwd.assert_not_called()      # ... and before any model call


def test_licensed_student_passes_gate():
    from fastapi.testclient import TestClient
    from config import system_config
    from safety.pipeline import SafetyResult, Severity, Category

    safe = SafetyResult(is_safe=True, severity=Severity.NONE,
                        category=Category.VALID, reason="")
    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})

    client = TestClient(_make_app())
    with (
        patch.object(system_config, "LICENSE_ENFORCED", True),
        patch("core.licensing.current_state", return_value=_active()),
        patch("api.routes.ollama_proxy._get_user_from_headers",
              return_value=("stud_1", "user")),
        patch("api.routes.ollama_proxy._get_profile_for_user",
              new_callable=AsyncMock, return_value="profile-1"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=safe),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_chat_body())

    # Past the gate => not the subscription block message.
    assert "subscription is needed" not in resp.text.lower()


def test_admin_never_gated():
    from fastapi.testclient import TestClient
    from config import system_config

    ollama_resp = httpx.Response(200, json={"model": "m", "done": True})
    client = TestClient(_make_app())
    with (
        patch.object(system_config, "LICENSE_ENFORCED", True),
        patch("core.licensing.current_state", return_value=_unlicensed()),
        patch("api.routes.ollama_proxy._get_user_from_headers",
              return_value=("admin_1", "admin")),
        patch("api.routes.ollama_proxy._forward_request",
              new_callable=AsyncMock, return_value=ollama_resp),
    ):
        resp = client.post("/api/chat", json=_chat_body())

    # Admin bypasses both safety and the license gate.
    assert "subscription is needed" not in resp.text.lower()
