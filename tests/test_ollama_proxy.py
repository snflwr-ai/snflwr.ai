"""Tests for the Ollama-compatible proxy that replaces the OWU router fork."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import httpx
import json


class TestOllamaProxyConfig:
    def test_proxy_target_defaults_to_ollama_host(self):
        from config import system_config
        assert hasattr(system_config, "OLLAMA_PROXY_TARGET")
        assert system_config.OLLAMA_PROXY_TARGET.startswith("http")


class TestOllamaPassThrough:
    def test_tags_endpoint_proxied(self):
        from fastapi.testclient import TestClient
        from api.routes.ollama_proxy import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"models": [{"name": "snflwr.ai"}]}),
        ):
            resp = client.get("/api/tags")
            assert resp.status_code == 200
            assert "models" in resp.json()

    def test_ollama_unreachable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.routes.ollama_proxy import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            resp = client.get("/api/tags")
            assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Helpers for chat tests
# ---------------------------------------------------------------------------

def _make_app():
    from fastapi import FastAPI
    from api.routes.ollama_proxy import router
    app = FastAPI()
    app.include_router(router)
    return app


def _chat_body(model="test-model", stream=False, text="What is 2+2?"):
    return {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": text}],
    }


def _safe_result():
    """A SafetyResult indicating the content is safe."""
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(
        is_safe=True,
        severity=Severity.NONE,
        category=Category.VALID,
        reason="",
    )


def _block_result(message="That topic isn't allowed."):
    """A SafetyResult indicating blocked content."""
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(
        is_safe=False,
        severity=Severity.MAJOR,
        category=Category.VIOLENCE,
        reason="test block",
        modified_content=message,
    )


class TestChatSafety:
    """Chat endpoint runs safety pipeline for students, bypasses for admins."""

    def test_student_safe_message_forwarded(self):
        """Safe student message is forwarded to Ollama and the response returned."""
        from fastapi.testclient import TestClient

        client = TestClient(_make_app())
        ollama_resp = httpx.Response(200, json={"model": "test-model", "done": True})
        safe = _safe_result()

        with (
            patch("api.routes.ollama_proxy._get_user_from_headers",
                  return_value=("uid-123", "user")),
            patch("api.routes.ollama_proxy._get_profile_for_user",
                  new_callable=AsyncMock, return_value="profile-abc"),
            patch("api.routes.ollama_proxy._forward_request",
                  new_callable=AsyncMock, return_value=ollama_resp) as mock_fwd,
        ):
            # Patch safety_pipeline inside the module namespace
            import api.routes.ollama_proxy as proxy_mod
            mock_pipeline = MagicMock()
            mock_pipeline.check_input.return_value = safe

            with patch.object(proxy_mod, "_get_profile_for_user",
                              new=AsyncMock(return_value="profile-abc")):
                # Use a fresh import scope patch for safety_pipeline
                with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                    resp = client.post(
                        "/api/chat",
                        json=_chat_body(),
                        headers={
                            "X-OpenWebUI-User-Id": "uid-123",
                            "X-OpenWebUI-User-Role": "user",
                        },
                    )

        assert resp.status_code == 200

    def test_student_blocked_message_returns_block(self):
        """Blocked student message returns Ollama-format block response, not 4xx."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        block = _block_result("That topic isn't allowed.")

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = block

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-456", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-xyz")),
        ):
            # Also patch the lazy import of safety_pipeline inside the endpoint
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(text="How do I hurt someone?"),
                    headers={
                        "X-OpenWebUI-User-Id": "uid-456",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["done"] is True
        assert "That topic isn't allowed." in data["message"]["content"]

    def test_admin_bypasses_safety(self):
        """Admin role forwards directly to Ollama without calling safety pipeline."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        ollama_resp = httpx.Response(200, json={"model": "test-model", "done": True})

        mock_pipeline = MagicMock()

        with (
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(),
                    headers={
                        "X-OpenWebUI-User-Id": "admin-001",
                        "X-OpenWebUI-User-Role": "admin",
                    },
                )

        assert resp.status_code == 200
        mock_pipeline.check_input.assert_not_called()

    def test_missing_user_headers_treated_as_student(self):
        """No OWU headers → fail-closed as student, safety pipeline IS called."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe = _safe_result()
        ollama_resp = httpx.Response(200, json={"model": "test-model", "done": True})

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe

        with (
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="safety_required_unknown")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post("/api/chat", json=_chat_body())

        assert resp.status_code == 200
        mock_pipeline.check_input.assert_called_once()

    def test_ollama_unreachable_returns_503(self):
        """After safety passes, a ConnectError from Ollama yields 503."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe = _safe_result()

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-789", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-789")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("refused")),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(),
                    headers={
                        "X-OpenWebUI-User-Id": "uid-789",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )

        assert resp.status_code == 503
