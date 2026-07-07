"""Integration tests for the pedagogy post-processor hook in proxy_chat.

Verifies:
- Flag ON + safe output: revealing response is replaced with the clean re-issue text.
- Flag OFF: response is byte-identical (same content) to the revealing text — no-op.

Mock strategy: _forward_request is called up to 3 times when the enforcer runs
(original chat, confirm call, re-issue).  Providing a side_effect list lets us
control each call independently without a live Ollama instance.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers shared with test_ollama_proxy.py
# ---------------------------------------------------------------------------


def _make_app():
    """Build a TestClient-ready FastAPI app with auth bypassed via override.

    Mimics the pattern from test_ollama_proxy._make_app; uses the internal
    service identity (Open WebUI relay) so OWU-forwarded headers drive user_id.
    """
    from fastapi import FastAPI

    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service",
        role="user",
        session_token="test-token",
        email="internal@snflwr.ai",
    )
    return app


def _safe_result():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(
        is_safe=True,
        severity=Severity.NONE,
        category=Category.VALID,
        reason="",
    )


def _ollama_chat_resp(text: str) -> httpx.Response:
    """Simulate an Ollama non-streaming /api/chat response."""
    return httpx.Response(
        200,
        json={
            "model": "snflwr.ai",
            "message": {"role": "assistant", "content": text},
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 20,
        },
    )


def _ollama_confirm_resp(revealed: bool = True) -> httpx.Response:
    """Simulate an Ollama confirm response (revealed true/false)."""
    verdict = "true" if revealed else "false"
    return httpx.Response(
        200,
        json={
            "model": "snflwr.ai",
            "message": {"role": "assistant", "content": '{"revealed": %s}' % verdict},
            "done": True,
        },
    )


# A question that triggers is_homework_request (matches "just give me the answer")
_HOMEWORK_Q = "Just give me the answer to 7 times 8."

# A revealing response (the mocked confirm stage flags it as a reveal)
_REVEALING = "The answer is 56."

# A clean guiding response (the mocked confirm stage says NOT revealed)
_CLEAN = "What is 7 times 4? Think about how that helps you get to 7 times 8."

# Common patches applied to every test
_COMMON_PATCHES = [
    (
        "api.routes.ollama_proxy.access._get_user_from_headers",
        {"return_value": ("uid-pedagogy", "user")},
    ),
    (
        "api.routes.ollama_proxy.profile._get_profile_for_user",
        {"new": AsyncMock(return_value="profile-pedagogy")},
    ),
]


class TestPedagogyProxyHook:
    """Proxy integration tests for the guidance-enforcement hook."""

    def test_flag_on_reveals_answer_replaced_with_clean(self):
        """Flag ON: the proxy returns the clean re-issue text, not the revealing one.

        _forward_request is expected 4 times:
          1. Original chat        → revealing response
          2. Confirm (original)   → {"revealed": true}
          3. Re-issue (regenerate)→ clean response
          4. Confirm (retry)      → {"revealed": false}  (retry is clean → serve it)
        """
        from fastapi.testclient import TestClient

        from config import system_config

        client = TestClient(_make_app())

        safe = _safe_result()
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.return_value = safe

        # Four sequential Ollama calls from the proxy + enforcer
        mock_fwd = AsyncMock(
            side_effect=[
                _ollama_chat_resp(_REVEALING),        # 1. original turn
                _ollama_confirm_resp(revealed=True),  # 2. confirm (original)
                _ollama_chat_resp(_CLEAN),            # 3. re-issue (regenerate)
                _ollama_confirm_resp(revealed=False), # 4. confirm (retry) -> clean
            ]
        )

        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", True),
            patch(
                "api.routes.ollama_proxy.access._get_user_from_headers",
                return_value=("uid-pedagogy", "user"),
            ),
            patch(
                "api.routes.ollama_proxy.profile._get_profile_for_user",
                new=AsyncMock(return_value="profile-pedagogy"),
            ),
            patch(
                "api.routes.ollama_proxy.transport._forward_request",
                new=mock_fwd,
            ),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "model": "snflwr.ai",
                    "stream": False,
                    "messages": [{"role": "user", "content": _HOMEWORK_Q}],
                },
                headers={
                    "X-OpenWebUI-User-Id": "uid-pedagogy",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"]["content"] == _CLEAN, (
            f"Expected clean text, got: {data['message']['content']!r}"
        )
        # Enforcer ran all four calls (original, confirm, re-issue, retry re-check)
        assert mock_fwd.call_count == 4

    def test_flag_off_response_is_unchanged(self):
        """Flag OFF: the proxy returns the revealing text byte-for-byte (no enforcer).

        Only one _forward_request call should be made (original chat only).
        """
        from fastapi.testclient import TestClient

        from config import system_config

        client = TestClient(_make_app())

        safe = _safe_result()
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.return_value = safe

        # Only one Ollama call expected
        mock_fwd = AsyncMock(return_value=_ollama_chat_resp(_REVEALING))

        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", False),
            patch(
                "api.routes.ollama_proxy.access._get_user_from_headers",
                return_value=("uid-pedagogy", "user"),
            ),
            patch(
                "api.routes.ollama_proxy.profile._get_profile_for_user",
                new=AsyncMock(return_value="profile-pedagogy"),
            ),
            patch(
                "api.routes.ollama_proxy.transport._forward_request",
                new=mock_fwd,
            ),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "model": "snflwr.ai",
                    "stream": False,
                    "messages": [{"role": "user", "content": _HOMEWORK_Q}],
                },
                headers={
                    "X-OpenWebUI-User-Id": "uid-pedagogy",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"]["content"] == _REVEALING, (
            f"Expected revealing text unchanged, got: {data['message']['content']!r}"
        )
        # Enforcer did not run — exactly one Ollama call
        assert mock_fwd.call_count == 1

    def test_stream_on_skips_enforcer(self):
        """stream=True + CHAT_STREAMING_ENABLED=True → enforcer never runs.

        The pedagogy block lives only on the buffered non-streaming path. When
        streaming is enabled the proxy takes the hold-back streaming branch and
        must never reach the guidance-enforcement block.
        """
        import asyncio
        from fastapi.testclient import TestClient
        from config import system_config
        from unittest.mock import patch, AsyncMock, MagicMock

        client = TestClient(_make_app())

        safe = _safe_result()
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.return_value = safe

        # A minimal NDJSON stream: one done chunk with empty content.
        done_chunk = (
            b'{"model":"snflwr.ai","message":{"role":"assistant","content":""},'
            b'"done":true}\n'
        )

        async def _fake_stream(*_args, **_kwargs):
            yield done_chunk

        mock_enforce = AsyncMock()

        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", True),
            patch.object(system_config, "CHAT_STREAMING_ENABLED", True),
            patch(
                "api.routes.ollama_proxy.access._get_user_from_headers",
                return_value=("uid-pedagogy", "user"),
            ),
            patch(
                "api.routes.ollama_proxy.profile._get_profile_for_user",
                new=AsyncMock(return_value="profile-pedagogy"),
            ),
            patch(
                "api.routes.ollama_proxy.transport._stream_chunks_from_ollama",
                side_effect=_fake_stream,
            ),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
            patch("core.pedagogy.enforce_guidance", mock_enforce),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "model": "snflwr.ai",
                    "stream": True,
                    "messages": [{"role": "user", "content": _HOMEWORK_Q}],
                },
                headers={
                    "X-OpenWebUI-User-Id": "uid-pedagogy",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        # Response must be the streaming content-type
        assert resp.headers["content-type"].startswith("application/x-ndjson"), (
            f"Expected streaming response, got: {resp.headers['content-type']!r}"
        )
        # Enforcer must not have run
        assert mock_enforce.call_count == 0, (
            f"enforce_guidance called {mock_enforce.call_count} time(s); expected 0"
        )

    def test_flag_on_unsafe_rewrite_discarded_original_served(self):
        """Flag ON: when the rewrite fails the safety re-check, the ORIGINAL text
        is served to the child — never the unvetted rewrite.

        check_output is configured safe for the original text (_REVEALING) but
        UNSAFE for the rewrite (_CLEAN), simulating a classifier that rejects the
        enforcer's re-issued answer. The proxy must fall back to the original.
        """
        from fastapi.testclient import TestClient
        from config import system_config
        from safety.pipeline import Category, SafetyResult, Severity

        client = TestClient(_make_app())

        safe = _safe_result()
        unsafe = SafetyResult(
            is_safe=False,
            severity=Severity.MAJOR,
            category=Category.VIOLENCE,
            reason="rewrite deemed unsafe by classifier",
        )

        call_count = {"n": 0}

        def _check_output_side_effect(text, age=None, profile_id=None, context=None):
            call_count["n"] += 1
            # First call: original assistant text → safe.
            # Second call: rewrite text → unsafe.
            if text == _CLEAN:
                return unsafe
            return safe

        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.side_effect = _check_output_side_effect
        mock_pipeline.get_safe_response.return_value = "safe fallback"

        # Four Ollama calls: original + confirm(original) + re-issue + confirm(retry).
        # The retry re-check passes (clean per confirm) so the enforcer RETURNS the
        # rewrite; the proxy's check_output then deems it unsafe and discards it.
        mock_fwd = AsyncMock(
            side_effect=[
                _ollama_chat_resp(_REVEALING),        # 1. original turn
                _ollama_confirm_resp(revealed=True),  # 2. confirm (original)
                _ollama_chat_resp(_CLEAN),            # 3. re-issue (unsafe rewrite)
                _ollama_confirm_resp(revealed=False), # 4. confirm (retry) -> clean
            ]
        )

        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", True),
            patch(
                "api.routes.ollama_proxy.access._get_user_from_headers",
                return_value=("uid-pedagogy", "user"),
            ),
            patch(
                "api.routes.ollama_proxy.profile._get_profile_for_user",
                new=AsyncMock(return_value="profile-pedagogy"),
            ),
            patch(
                "api.routes.ollama_proxy.transport._forward_request",
                new=mock_fwd,
            ),
            patch("safety.pipeline.safety_pipeline", mock_pipeline),
        ):
            resp = client.post(
                "/api/chat",
                json={
                    "model": "snflwr.ai",
                    "stream": False,
                    "messages": [{"role": "user", "content": _HOMEWORK_Q}],
                },
                headers={
                    "X-OpenWebUI-User-Id": "uid-pedagogy",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"]["content"] == _REVEALING, (
            f"Expected original text to be served (rewrite was unsafe), "
            f"got: {data['message']['content']!r}"
        )
        # Enforcer ran all four calls (original→confirm→reissue→retry re-check)
        assert mock_fwd.call_count == 4
        # check_output was called at least twice: once for original, once for rewrite
        assert call_count["n"] >= 2
