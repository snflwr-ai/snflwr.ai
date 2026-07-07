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


def _ollama_confirm_resp() -> httpx.Response:
    """Simulate an Ollama response that says the tutor revealed the answer."""
    return httpx.Response(
        200,
        json={
            "model": "snflwr.ai",
            "message": {"role": "assistant", "content": '{"revealed": true}'},
            "done": True,
        },
    )


# A question that triggers is_homework_request (matches "just give me the answer")
_HOMEWORK_Q = "Just give me the answer to 7 times 8."

# A response that triggers heuristic_reveals (matches "the answer is")
_REVEALING = "The answer is 56."

# A clean response that does NOT trigger heuristic_reveals
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

        _forward_request is expected 3 times:
          1. Original chat → revealing response
          2. Confirm call  → {"revealed": true}
          3. Re-issue call → clean response
        """
        from fastapi.testclient import TestClient

        from config import system_config

        client = TestClient(_make_app())

        safe = _safe_result()
        mock_pipeline = MagicMock()
        mock_pipeline.check_input.return_value = safe
        mock_pipeline.check_output.return_value = safe

        # Three sequential Ollama calls from the proxy + enforcer
        mock_fwd = AsyncMock(
            side_effect=[
                _ollama_chat_resp(_REVEALING),   # 1. original turn
                _ollama_confirm_resp(),           # 2. confirm stage
                _ollama_chat_resp(_CLEAN),        # 3. re-issue
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
        # Enforcer ran all three calls
        assert mock_fwd.call_count == 3

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
