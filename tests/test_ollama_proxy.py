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

        async def _fake_stream(body, headers):
            from fastapi.responses import StreamingResponse

            async def gen():
                yield b'{"done":false}\n'
                yield b'{"done":true}\n'

            return StreamingResponse(gen(), media_type="application/x-ndjson")

        with (
            patch.object(proxy_mod, "_get_user_from_headers",
                         return_value=("uid-stream", "user")),
            patch.object(proxy_mod, "_get_profile_for_user",
                         new=AsyncMock(return_value="profile-stream")),
            patch.object(proxy_mod, "_stream_chat_from_ollama",
                         side_effect=_fake_stream),
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
