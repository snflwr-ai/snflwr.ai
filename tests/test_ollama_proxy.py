"""Tests for the Ollama-compatible proxy that replaces the OWU router fork."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import importlib
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
        client = TestClient(_make_app())

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
        client = TestClient(_make_app())

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
    """Build a TestClient app with auth bypassed via dependency override.

    Key the override on `proxy_mod.get_current_session` — the reference the
    router captured at import time — rather than re-importing the symbol
    from api.middleware.auth. A sibling test (test_auth_middleware.py's
    Redis-fallback case) calls importlib.reload(api.middleware.auth) to
    exercise the import-error path; after that reload, the symbol exported
    from api.middleware.auth is a NEW function object, but ollama_proxy's
    router still holds the OLD one. FastAPI matches overrides by object
    identity, so we must key on the SAME reference the router did, which
    is now only reachable via proxy_mod's own namespace.
    """
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


def _make_app_real_auth():
    """No dependency override — exercises the real Bearer check."""
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


class TestProxyHealth:
    """Proxy health endpoint verifies Ollama round-trip."""

    def test_health_returns_healthy(self):
        from fastapi.testclient import TestClient
        app = _make_app()
        client = TestClient(app)
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"version": "0.9.0"}),
        ):
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["ollama"]["version"] == "0.9.0"

    def test_health_returns_503_when_ollama_down(self):
        from fastapi.testclient import TestClient
        app = _make_app()
        client = TestClient(app)
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            resp = client.get("/api/health")
            assert resp.status_code == 503
            assert resp.json()["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# New coverage tests
# ---------------------------------------------------------------------------


class TestForwardRequest:
    """Cover the real _forward_request function body (lines 29-33)."""

    @pytest.mark.asyncio
    async def test_forward_request_calls_httpx(self):
        """_forward_request builds the correct URL and calls httpx."""
        from api.routes.ollama_proxy import _forward_request

        fake_resp = httpx.Response(200, json={"ok": True})
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routes.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await _forward_request("GET", "/api/tags")

        assert result.status_code == 200
        mock_client.request.assert_awaited_once()
        call_args = mock_client.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1].endswith("/api/tags")


class TestGetProfileForUser:
    """Cover _get_profile_for_user (lines 88-106)."""

    @pytest.mark.asyncio
    async def test_none_user_id_returns_safety_required_unknown(self):
        from api.routes.ollama_proxy import _get_profile_for_user

        result = await _get_profile_for_user(None)
        assert result == "safety_required_unknown"

    @pytest.mark.asyncio
    async def test_profiles_found_returns_first_profile_id(self):
        import sys
        from api.routes.ollama_proxy import _get_profile_for_user

        mock_profile = MagicMock()
        mock_profile.profile_id = "child-001"

        mock_pm_instance = MagicMock()
        mock_pm_instance.get_profiles_by_parent.return_value = [mock_profile]

        mock_auth = MagicMock()
        mock_auth.db = MagicMock()

        mock_auth_mod = MagicMock()
        mock_auth_mod.auth_manager = mock_auth

        mock_pm_mod = MagicMock()
        mock_pm_mod.ProfileManager = MagicMock(return_value=mock_pm_instance)

        with patch.dict(sys.modules, {
            "core.authentication": mock_auth_mod,
            "core.profile_manager": mock_pm_mod,
        }):
            result = await _get_profile_for_user("user-42")

        assert result == "child-001"

    @pytest.mark.asyncio
    async def test_no_profiles_returns_safety_required_user_id(self):
        from api.routes.ollama_proxy import _get_profile_for_user

        mock_pm = MagicMock()
        mock_pm.get_profiles_by_parent.return_value = []

        mock_auth = MagicMock()
        mock_auth.db = MagicMock()

        with patch.dict("sys.modules", {
            "core.authentication": MagicMock(auth_manager=mock_auth),
            "core.profile_manager": MagicMock(ProfileManager=MagicMock(return_value=mock_pm)),
        }):
            result = await _get_profile_for_user("user-99")

        assert result == "safety_required_user-99"

    @pytest.mark.asyncio
    async def test_exception_returns_safety_required_user_id(self):
        from api.routes.ollama_proxy import _get_profile_for_user

        with patch.dict("sys.modules", {
            "core.authentication": MagicMock(
                auth_manager=MagicMock(db=property(lambda s: (_ for _ in ()).throw(RuntimeError("db boom"))))
            ),
            "core.profile_manager": MagicMock(
                ProfileManager=MagicMock(side_effect=RuntimeError("db boom"))
            ),
        }):
            result = await _get_profile_for_user("user-err")

        assert result == "safety_required_user-err"


class TestExtractMultimodal:
    """Cover multimodal message extraction (line 117)."""

    def test_multimodal_content_parts_joined(self):
        from api.routes.ollama_proxy import _extract_last_user_message

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "image_url", "image_url": "data:..."},
                    {"type": "text", "text": "world"},
                ],
            }
        ]
        assert _extract_last_user_message(messages) == "Hello world"

    def test_non_dict_messages_skipped(self):
        from api.routes.ollama_proxy import _extract_last_user_message

        messages = ["not a dict", 42, {"role": "user", "content": "valid"}]
        assert _extract_last_user_message(messages) == "valid"

    def test_non_user_role_skipped(self):
        from api.routes.ollama_proxy import _extract_last_user_message

        messages = [{"role": "assistant", "content": "I am bot"}]
        assert _extract_last_user_message(messages) == ""

    def test_empty_messages_returns_empty(self):
        from api.routes.ollama_proxy import _extract_last_user_message

        assert _extract_last_user_message([]) == ""

    def test_only_non_dict_returns_empty(self):
        from api.routes.ollama_proxy import _extract_last_user_message

        assert _extract_last_user_message(["string", 42]) == ""


class TestChatInvalidJson:
    """Cover JSON parse error path (lines 202-203)."""

    def test_invalid_json_returns_400(self):
        from fastapi.testclient import TestClient

        client = TestClient(_make_app())
        resp = client.post(
            "/api/chat",
            content=b"not valid json{{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]


class TestAdminStreamAndConnectError:
    """Cover admin streaming path (line 223) and admin ConnectError (lines 231-232)."""

    def test_admin_streaming_calls_stream_helper(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())

        async def _fake_stream(body, headers):
            from fastapi.responses import StreamingResponse

            async def gen():
                yield b'{"done":false}\n'
                yield b'{"done":true}\n'

            return StreamingResponse(gen(), media_type="application/x-ndjson")

        with (
            patch.object(proxy_mod, "_stream_chat_from_ollama",
                         side_effect=_fake_stream),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(stream=True),
                headers={
                    "X-OpenWebUI-User-Id": "admin-s",
                    "X-OpenWebUI-User-Role": "admin",
                },
            )

        assert resp.status_code == 200
        assert b"done" in resp.content

    def test_admin_non_stream_connect_error_returns_503(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())

        with patch.object(
            proxy_mod, "_forward_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(stream=False),
                headers={
                    "X-OpenWebUI-User-Id": "admin-err",
                    "X-OpenWebUI-User-Role": "admin",
                },
            )

        assert resp.status_code == 503


class TestAgeResolution:
    """Cover age resolution try/except (lines 254-256)."""

    def test_age_resolved_from_profile(self):
        """When ProfileManager returns a profile with age, it's used in safety check."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe = _safe_result()
        ollama_resp = httpx.Response(200, json={"model": "test-model", "done": True})

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe

        mock_profile = MagicMock()
        mock_profile.age = 10

        mock_pm = MagicMock()
        mock_pm.get_profile.return_value = mock_profile

        mock_auth = MagicMock()
        mock_auth.db = MagicMock()

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-age", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-age")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
            patch.dict("sys.modules", {
                "core.authentication": MagicMock(auth_manager=mock_auth),
                "core.profile_manager": MagicMock(ProfileManager=MagicMock(return_value=mock_pm)),
            }),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(),
                headers={
                    "X-OpenWebUI-User-Id": "uid-age",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        # Verify age=10 was passed to check_input
        call_kwargs = mock_pipeline.check_input.call_args
        assert call_kwargs[1].get("age") == 10 or call_kwargs.kwargs.get("age") == 10

    def test_age_resolution_exception_handled(self):
        """Exception during age lookup doesn't block the request."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe = _safe_result()
        ollama_resp = httpx.Response(200, json={"model": "test-model", "done": True})

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-ageerr", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-ageerr")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
            patch.dict("sys.modules", {
                "core.authentication": MagicMock(
                    auth_manager=MagicMock(db=property(lambda s: (_ for _ in ()).throw(RuntimeError("boom"))))
                ),
                "core.profile_manager": MagicMock(
                    ProfileManager=MagicMock(side_effect=RuntimeError("boom"))
                ),
            }),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(),
                headers={
                    "X-OpenWebUI-User-Id": "uid-ageerr",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        # age should be None when resolution fails
        call_kwargs = mock_pipeline.check_input.call_args
        age_val = call_kwargs[1].get("age") if call_kwargs[1] else call_kwargs.kwargs.get("age")
        assert age_val is None


class TestSafetyPipelineException:
    """Cover safety pipeline exception handler (lines 264-268)."""

    def test_safety_pipeline_exception_fails_closed(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.side_effect = RuntimeError("pipeline crash")

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-exc", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-exc")),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(),
                headers={
                    "X-OpenWebUI-User-Id": "uid-exc",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["done"] is True
        assert "unable to process" in data["message"]["content"].lower()


class TestStudentStreaming:
    """Cover student streaming path (line 291)."""

    def test_student_safe_streaming(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe = _safe_result()

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.return_value = safe

        chunks = [
            b'{"message":{"role":"assistant","content":"hello"},"done":false}\n',
            b'{"message":{"role":"assistant","content":""},"done":true}\n',
        ]

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-stream", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-stream")),
            patch.object(proxy_mod, "_stream_chunks_from_ollama",
                         new=_async_iter(chunks)),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(stream=True),
                headers={
                    "X-OpenWebUI-User-Id": "uid-stream",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        assert b"done" in resp.content
        assert b"hello" in resp.content
        mock_pipeline.check_output.assert_called_once()


class TestStreamChatFromOllama:
    """Cover _stream_chat_from_ollama (lines 156-181)."""

    @pytest.mark.asyncio
    async def test_stream_success_returns_streaming_response(self):
        from api.routes.ollama_proxy import _stream_chat_from_ollama

        chunks = [b'{"msg":"chunk1"}\n', b'{"done":true}\n']

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/x-ndjson"}

        async def aiter_bytes():
            for c in chunks:
                yield c

        mock_resp.aiter_bytes = aiter_bytes
        mock_resp.aclose = AsyncMock()

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("api.routes.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await _stream_chat_from_ollama(b'{"model":"x"}', {})

        from fastapi.responses import StreamingResponse
        assert isinstance(result, StreamingResponse)

        # Consume the streaming body to cover the _yield_chunks generator
        collected = []
        async for chunk in result.body_iterator:
            collected.append(chunk)
        assert len(collected) == 2

    @pytest.mark.asyncio
    async def test_stream_connect_error_returns_503(self):
        from api.routes.ollama_proxy import _stream_chat_from_ollama

        mock_client = AsyncMock()
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.aclose = AsyncMock()

        with patch("api.routes.ollama_proxy.httpx.AsyncClient", return_value=mock_client):
            result = await _stream_chat_from_ollama(b'{"model":"x"}', {})

        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 503


class TestPassThroughEndpoints:
    """Cover all single-line pass-through handlers (lines 325-360)."""

    def _get_client(self):
        from fastapi.testclient import TestClient
        return TestClient(_make_app())

    def test_show_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"modelfile": "..."}),
        ):
            resp = client.post("/api/show", json={"name": "test"})
            assert resp.status_code == 200

    def test_generate_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"response": "hi"}),
        ):
            resp = client.post("/api/generate", json={"model": "test", "prompt": "hi"})
            assert resp.status_code == 200

    def test_embed_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"embedding": [0.1]}),
        ):
            resp = client.post("/api/embed", json={"model": "test", "input": "hi"})
            assert resp.status_code == 200

    def test_embeddings_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"embedding": [0.1]}),
        ):
            resp = client.post("/api/embeddings", json={"model": "test", "prompt": "hi"})
            assert resp.status_code == 200

    def test_delete_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={}),
        ):
            resp = client.request("DELETE", "/api/delete", json={"name": "test"})
            assert resp.status_code == 200

    def test_pull_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"status": "ok"}),
        ):
            resp = client.post("/api/pull", json={"name": "test"})
            assert resp.status_code == 200

    def test_copy_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={}),
        ):
            resp = client.post("/api/copy", json={"source": "a", "destination": "b"})
            assert resp.status_code == 200

    def test_version_endpoint(self):
        client = self._get_client()
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"version": "0.9.0"}),
        ):
            resp = client.get("/api/version")
            assert resp.status_code == 200


class TestOutputFiltering:
    """The proxy must run check_output on Ollama responses, not just check_input.

    Critical: an attacker who jailbreaks the input filter must not get unsafe
    model output through to the student.
    """

    def test_unsafe_nonstreaming_response_replaced_with_safe_fallback(self):
        """Ollama returns unsafe content; check_output blocks; client sees fallback."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        unsafe_text = "Here's how to make a weapon: ..."
        ollama_resp = httpx.Response(
            200,
            json={
                "model": "test-model",
                "message": {"role": "assistant", "content": unsafe_text},
                "done": True,
            },
        )

        safe_input = _safe_result()
        unsafe_output = _block_result("Let's talk about something else!")

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe_input
        mock_pipeline.check_output.return_value = unsafe_output

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-1", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-1")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(),
                    headers={
                        "X-OpenWebUI-User-Id": "uid-1",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert unsafe_text not in data["message"]["content"], (
            "Unsafe content leaked through — check_output not wired."
        )
        assert "Let's talk about something else!" in data["message"]["content"]
        mock_pipeline.check_output.assert_called_once()

    def test_safe_nonstreaming_response_passes_through(self):
        """When check_output passes, Ollama content reaches the client unchanged."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        safe_text = "The Pythagorean theorem states that a² + b² = c²."
        ollama_resp = httpx.Response(
            200,
            json={
                "model": "test-model",
                "message": {"role": "assistant", "content": safe_text},
                "done": True,
            },
        )

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-2", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-2")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(),
                    headers={
                        "X-OpenWebUI-User-Id": "uid-2",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert safe_text in data["message"]["content"]
        mock_pipeline.check_output.assert_called_once()

    def test_unsafe_streaming_response_replaced_with_block(self):
        """Streamed unsafe content is buffered and replaced with safe fallback."""
        import asyncio
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())

        # Simulate Ollama streaming NDJSON chunks of an unsafe response
        unsafe_chunks = [
            json.dumps({"model": "m", "message": {"role": "assistant",
                       "content": "Here's how "}, "done": False}).encode() + b"\n",
            json.dumps({"model": "m", "message": {"role": "assistant",
                       "content": "to make a "}, "done": False}).encode() + b"\n",
            json.dumps({"model": "m", "message": {"role": "assistant",
                       "content": "weapon."}, "done": False}).encode() + b"\n",
            json.dumps({"model": "m", "message": {"role": "assistant",
                       "content": ""}, "done": True,
                       "done_reason": "stop"}).encode() + b"\n",
        ]

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _block_result(
            "I can't help with that. Let's try something else!"
        )

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-3", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-3")),
            patch.object(proxy_mod, "_stream_chunks_from_ollama",
                         new=_async_iter(unsafe_chunks)),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(stream=True),
                    headers={
                        "X-OpenWebUI-User-Id": "uid-3",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )

        assert resp.status_code == 200
        # Aggregate streamed content from NDJSON chunks
        body = resp.content.decode()
        assembled = ""
        for line in body.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            assembled += obj.get("message", {}).get("content", "")

        assert "weapon" not in assembled, "Unsafe streamed content leaked"
        assert "I can't help with that" in assembled
        mock_pipeline.check_output.assert_called()


def _async_iter(items):
    """Build a no-arg callable returning an async iterator over items."""
    async def _gen(*_args, **_kwargs):
        for item in items:
            yield item
    return _gen


def _bearer():
    """Authorization header carrying the configured INTERNAL_API_KEY."""
    from config import INTERNAL_API_KEY
    return {"Authorization": f"Bearer {INTERNAL_API_KEY}"}


class TestProxyBearerAuth:
    """The Ollama proxy must require a Bearer token (INTERNAL_API_KEY or session).

    Without this, anyone able to reach :39150 directly can claim
    X-OpenWebUI-User-Role=admin and bypass the entire safety pipeline.
    """

    def test_chat_without_bearer_returns_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(_make_app_real_auth())
        resp = client.post(
            "/api/chat",
            json=_chat_body(),
            headers={
                "X-OpenWebUI-User-Id": "uid-spoof",
                "X-OpenWebUI-User-Role": "admin",
            },
        )
        assert resp.status_code == 401

    def test_chat_with_invalid_bearer_returns_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(_make_app_real_auth())
        resp = client.post(
            "/api/chat",
            json=_chat_body(),
            headers={
                "Authorization": "Bearer not-the-real-key",
                "X-OpenWebUI-User-Id": "uid-spoof",
                "X-OpenWebUI-User-Role": "admin",
            },
        )
        assert resp.status_code == 401

    def test_tags_without_bearer_returns_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(_make_app_real_auth())
        resp = client.get("/api/tags")
        assert resp.status_code == 401

    def test_chat_with_valid_bearer_proceeds(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app_real_auth())
        ollama_resp = httpx.Response(200, json={
            "model": "test-model",
            "message": {"role": "assistant", "content": "ok"},
            "done": True,
        })

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()

        with (
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-x")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
        ):
            with patch("safety.pipeline.safety_pipeline", mock_pipeline):
                resp = client.post(
                    "/api/chat",
                    json=_chat_body(),
                    headers={
                        **_bearer(),
                        "X-OpenWebUI-User-Id": "uid-real",
                        "X-OpenWebUI-User-Role": "user",
                    },
                )
        assert resp.status_code == 200


class TestCrisisEscalation:
    """A blocked student message must escalate to a human, not just render a
    safe response. Students reach the model via this proxy (not chat.py), so the
    incident_logger.log_incident call (DB incident + parent alert for
    major/critical) has to fire here.
    """

    def test_input_block_records_incident(self):
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        # A self-harm-style block (MAJOR severity → parent alert path).
        block = _block_result("Please talk to a trusted adult. You can reach 988.")
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = block

        mock_incident = MagicMock()
        mock_incident.log_incident.return_value = (True, 1)

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-sh", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-sh")),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            # Patch the module object directly. `safety/__init__.py` re-exports
            # the `incident_logger` instance, which shadows the same-named
            # submodule, so the string target "safety.incident_logger.incident_logger"
            # resolves to the instance (not the module) under Python 3.10's mock and
            # raises AttributeError. importlib.import_module returns the real module.
            patch.object(
                importlib.import_module("safety.incident_logger"),
                "incident_logger",
                mock_incident,
            ),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(text="i want to die"),
                headers={
                    "X-OpenWebUI-User-Id": "uid-sh",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        # The child still gets the safe response...
        assert "988" in resp.json()["message"]["content"]
        # ...AND a human-escalation incident was recorded.
        mock_incident.log_incident.assert_called_once()
        kwargs = mock_incident.log_incident.call_args.kwargs
        assert kwargs["profile_id"] == "profile-sh"
        assert kwargs["incident_type"]  # category.value present
        assert kwargs["severity"] in ("minor", "major", "critical")

    def test_escalation_failure_does_not_break_child_response(self):
        """If incident logging raises, the child STILL gets the safe response."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        block = _block_result("Let's talk to a trusted adult.")
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = block

        mock_incident = MagicMock()
        mock_incident.log_incident.side_effect = RuntimeError("db down")

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-x", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-x")),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            # Patch the module object directly. `safety/__init__.py` re-exports
            # the `incident_logger` instance, which shadows the same-named
            # submodule, so the string target "safety.incident_logger.incident_logger"
            # resolves to the instance (not the module) under Python 3.10's mock and
            # raises AttributeError. importlib.import_module returns the real module.
            patch.object(
                importlib.import_module("safety.incident_logger"),
                "incident_logger",
                mock_incident,
            ),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(text="something blocked"),
                headers={
                    "X-OpenWebUI-User-Id": "uid-x",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        assert "trusted adult" in resp.json()["message"]["content"]
        mock_incident.log_incident.assert_called_once()  # attempted, but failure swallowed

    def test_safe_message_records_no_incident(self):
        """A safe student message must NOT create an incident."""
        from fastapi.testclient import TestClient
        import api.routes.ollama_proxy as proxy_mod

        client = TestClient(_make_app())
        ollama_resp = httpx.Response(200, json={
            "model": "test-model",
            "message": {"role": "assistant", "content": "2 + 2 = 4."},
            "done": True,
        })
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = _safe_result()
        mock_pipeline.check_output.return_value = _safe_result()

        mock_incident = MagicMock()

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-ok", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-ok")),
            patch.object(proxy_mod, "_forward_request",
                         new_callable=AsyncMock, return_value=ollama_resp),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            # Patch the module object directly. `safety/__init__.py` re-exports
            # the `incident_logger` instance, which shadows the same-named
            # submodule, so the string target "safety.incident_logger.incident_logger"
            # resolves to the instance (not the module) under Python 3.10's mock and
            # raises AttributeError. importlib.import_module returns the real module.
            patch.object(
                importlib.import_module("safety.incident_logger"),
                "incident_logger",
                mock_incident,
            ),
        ):
            resp = client.post(
                "/api/chat",
                json=_chat_body(text="what is 2+2?"),
                headers={
                    "X-OpenWebUI-User-Id": "uid-ok",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        mock_incident.log_incident.assert_not_called()


class TestTagsModelVisibility:
    """Students must only see the public tutor model in /api/tags.

    The Ollama backend also holds the raw backbone plus rollback/backup
    variants; those must never appear in a child's model dropdown. Admins
    still see everything so they can manage models.
    """

    _ALL_MODELS = {
        "models": [
            {"name": "snflwr.ai:latest"},
            {"name": "snflwr.ai:qwen-rollback"},
            {"name": "snflwr-bk-gemma4-e4b:latest"},
            {"name": "gemma4:e4b"},
        ]
    }

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(_make_app())

    def test_student_sees_only_public_model(self):
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json=self._ALL_MODELS),
        ):
            resp = self._client().get(
                "/api/tags", headers={"X-OpenWebUI-User-Role": "user"}
            )
        assert resp.status_code == 200
        names = {m["name"] for m in resp.json()["models"]}
        assert names == {"snflwr.ai:latest"}

    def test_missing_role_header_fails_closed_to_student(self):
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json=self._ALL_MODELS),
        ):
            resp = self._client().get("/api/tags")
        assert resp.status_code == 200
        names = {m["name"] for m in resp.json()["models"]}
        assert names == {"snflwr.ai:latest"}
        assert "gemma4:e4b" not in names

    def test_admin_sees_all_models(self):
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json=self._ALL_MODELS),
        ):
            resp = self._client().get(
                "/api/tags", headers={"X-OpenWebUI-User-Role": "admin"}
            )
        assert resp.status_code == 200
        names = {m["name"] for m in resp.json()["models"]}
        assert names == {
            "snflwr.ai:latest",
            "snflwr.ai:qwen-rollback",
            "snflwr-bk-gemma4-e4b:latest",
            "gemma4:e4b",
        }

    def test_non_200_passed_through_unfiltered(self):
        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            resp = self._client().get(
                "/api/tags", headers={"X-OpenWebUI-User-Role": "user"}
            )
        assert resp.status_code == 503

    def test_filter_helper_keeps_base_and_latest(self):
        from api.routes.ollama_proxy import _filter_tags_for_students
        import json as _j

        payload = _j.dumps({
            "models": [
                {"name": "snflwr.ai"},
                {"name": "snflwr.ai:latest"},
                {"name": "snflwr.ai:qwen-rollback"},
                {"name": "gemma4:e4b"},
            ]
        }).encode()
        out = _j.loads(_filter_tags_for_students(payload))
        names = {m["name"] for m in out["models"]}
        assert names == {"snflwr.ai", "snflwr.ai:latest"}

    def test_filter_helper_tolerates_malformed_payload(self):
        from api.routes.ollama_proxy import _filter_tags_for_students

        # Not JSON → returned unchanged rather than crashing.
        assert _filter_tags_for_students(b"not json{{") == b"not json{{"


class TestForkedFilesDeleted:
    """The OWU router fork and middleware must not exist."""

    def test_router_fork_deleted(self):
        assert not os.path.exists(
            "frontend/open-webui/backend/open_webui/routers/ollama.py"
        ), "Router fork still exists — should be deleted"

    def test_middleware_deleted(self):
        assert not os.path.exists(
            "frontend/open-webui/backend/open_webui/middleware/snflwr.py"
        ), "Middleware still exists — should be deleted"

    def test_middleware_init_deleted(self):
        assert not os.path.exists(
            "frontend/open-webui/backend/open_webui/middleware/__init__.py"
        ), "Middleware __init__.py still exists — should be deleted"
