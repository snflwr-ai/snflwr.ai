"""The enforcer must fire on a STREAMED turn -- the one the real client sends.

Open WebUI's /api/chat payload model declares `stream: bool | None = True` and
forwards it, so stream=True is the DEFAULT shape of a real request. Both
streaming branches used to return before the pedagogy block, which meant homework
protection was inert for every child using the product: every reveal figure ever
measured came from the non-streaming path that nothing actually used.

These tests drive the real route with stream=True.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_HOMEWORK_Q = "Just give me the answer to 7 times 8."
_REVEALING = "The answer is 56."
_CLEAN = "What is 7 times 4? Think about how that helps you get to 7 times 8."


def _app():
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


def _safe():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(
        is_safe=True, severity=Severity.NONE, category=Category.VALID, reason=""
    )


def _ndjson(text: str) -> list[bytes]:
    """Ollama streams a reply across several chunks, then a final done chunk."""
    mid = len(text) // 2
    out = []
    for part in (text[:mid], text[mid:]):
        out.append(
            (
                json.dumps(
                    {
                        "model": "snflwr.ai",
                        "message": {"role": "assistant", "content": part},
                        "done": False,
                    }
                )
                + "\n"
            ).encode()
        )
    out.append(
        (
            json.dumps(
                {
                    "model": "snflwr.ai",
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "prompt_eval_count": 11,
                    "eval_count": 22,
                }
            )
            + "\n"
        ).encode()
    )
    return out


def _confirm(revealed: bool) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "snflwr.ai",
            "message": {"role": "assistant", "content": '{"revealed": %s}'
                        % ("true" if revealed else "false")},
            "done": True,
        },
    )


def _served(resp) -> str:
    """Concatenate message.content across the NDJSON body we returned."""
    parts = []
    for line in resp.content.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        parts.append(obj.get("message", {}).get("content", ""))
    return "".join(parts)


def _run(stream_chunks, forward_responses, *, enforcement=True):
    from fastapi.testclient import TestClient

    from config import system_config

    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe()
    pipeline.check_output.return_value = _safe()

    async def _chunks(body, headers):
        for c in stream_chunks:
            yield c

    with (
        patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", enforcement),
        patch.object(system_config, "CHAT_STREAMING_ENABLED", False),
        patch("api.routes.ollama_proxy.access._get_user_from_headers",
              return_value=("uid-s", "user")),
        patch("api.routes.ollama_proxy.profile._get_profile_for_user",
              new=AsyncMock(return_value="profile-s")),
        patch("api.routes.ollama_proxy.transport._stream_chunks_from_ollama", _chunks),
        patch("api.routes.ollama_proxy.transport._forward_request",
              new=AsyncMock(side_effect=forward_responses)),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        return TestClient(_app()).post(
            "/api/chat",
            json={"model": "snflwr.ai", "stream": True,
                  "messages": [{"role": "user", "content": _HOMEWORK_Q}]},
            headers={"X-OpenWebUI-User-Id": "uid-s", "X-OpenWebUI-User-Role": "user"},
        )


def _confirm_run(revealed: bool) -> list:
    """Every response ONE confirm stage consumes, sized from the live ensemble.

    The confirm is an OR-ensemble: a clean verdict costs one call per member
    (all must agree), a revealed verdict short-circuits on the first. Derived
    from `_CONFIRM_ENSEMBLE` so adding or removing a member does not silently
    desync these scripted responses -- which is exactly how they broke when the
    second member shipped.
    """
    from core.pedagogy.reveal_detection import _CONFIRM_ENSEMBLE

    n = 1 if revealed else max(1, len(_CONFIRM_ENSEMBLE))
    return [_confirm(revealed) for _ in range(n)]


class TestEnforcerFiresOnStreamedTurns:
    def test_revealing_streamed_answer_is_replaced(self):
        # confirm(original)->revealed, re-issue->clean, confirm(retry)->clean
        r = _run(_ndjson(_REVEALING),
                 [*_confirm_run(True), httpx.Response(200, json={
                     "model": "snflwr.ai", "done": True,
                     "message": {"role": "assistant", "content": _CLEAN}}),
                  *_confirm_run(False)])
        assert r.status_code == 200
        assert "application/x-ndjson" in r.headers["content-type"]
        served = _served(r)
        assert served == _CLEAN, f"enforcer did not fire on a streamed turn: {served!r}"
        assert "56" not in served

    def test_wire_format_stays_ndjson(self):
        r = _run(_ndjson(_REVEALING),
                 [*_confirm_run(True), httpx.Response(200, json={
                     "model": "snflwr.ai", "done": True,
                     "message": {"role": "assistant", "content": _CLEAN}}),
                  *_confirm_run(False)])
        # A JSONResponse here renders as a blank bubble in Open WebUI.
        assert "application/x-ndjson" in r.headers["content-type"]
        for line in r.content.splitlines():
            if line.strip():
                json.loads(line)  # every line must be a standalone JSON object

    def test_clean_streamed_answer_is_served_unchanged(self):
        r = _run(_ndjson(_CLEAN), _confirm_run(False))
        assert _served(r) == _CLEAN

    def test_enforcement_disabled_leaves_the_stream_alone(self):
        r = _run(_ndjson(_REVEALING), [], enforcement=False)
        assert _served(r) == _REVEALING

    def test_token_usage_survives_buffering(self):
        r = _run(_ndjson(_CLEAN), _confirm_run(False))
        obj = [json.loads(l) for l in r.content.splitlines() if l.strip()][-1]
        assert obj.get("prompt_eval_count") == 11
        assert obj.get("eval_count") == 22

    def test_scaffolding_is_stripped_on_the_streamed_path_too(self):
        tagged = "[Student age range: 11-13]\n\n" + _CLEAN
        r = _run(_ndjson(tagged), _confirm_run(False))
        assert "Student age range" not in _served(r)


class TestProgressivePathBuffersHomework:
    """The progressive (hold-back) path flushes the first sentence before the
    answer is finished, so the enforcer would have nothing left to rewrite. A
    homework turn must be buffered instead."""

    def _run_holdback(self, question, chunks, forwards):
        from fastapi.testclient import TestClient

        from config import system_config

        pipeline = MagicMock()
        pipeline.check_input.return_value = _safe()
        pipeline.check_output.return_value = _safe()

        async def _chunks(body, headers):
            for c in chunks:
                yield c

        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", True),
            patch.object(system_config, "CHAT_STREAMING_ENABLED", True),  # progressive ON
            patch("api.routes.ollama_proxy.access._get_user_from_headers",
                  return_value=("uid-h", "user")),
            patch("api.routes.ollama_proxy.profile._get_profile_for_user",
                  new=AsyncMock(return_value="profile-h")),
            patch("api.routes.ollama_proxy.transport._stream_chunks_from_ollama", _chunks),
            patch("api.routes.ollama_proxy.transport._forward_request",
                  new=AsyncMock(side_effect=forwards)),
            patch("safety.pipeline.safety_pipeline", pipeline),
        ):
            return TestClient(_app()).post(
                "/api/chat",
                json={"model": "snflwr.ai", "stream": True,
                      "messages": [{"role": "user", "content": question}]},
                headers={"X-OpenWebUI-User-Id": "uid-h",
                         "X-OpenWebUI-User-Role": "user"},
            )

    def test_homework_turn_is_buffered_and_enforced(self):
        r = self._run_holdback(
            _HOMEWORK_Q, _ndjson(_REVEALING),
            [_confirm(True),
             httpx.Response(200, json={"model": "snflwr.ai", "done": True,
                                       "message": {"role": "assistant", "content": _CLEAN}}),
             *_confirm_run(False)])
        served = _served(r)
        assert served == _CLEAN, (
            "a homework turn took the progressive path and escaped the enforcer: "
            f"{served!r}")
        assert "56" not in served

    def test_ordinary_turn_still_streams_progressively(self):
        # Not a homework demand -> the progressive path is left alone, so the
        # buffering above cannot quietly disable streaming for everyone.
        r = self._run_holdback(
            "Why is the sky blue?", _ndjson("Light scatters in the atmosphere."), [])
        assert r.status_code == 200
        assert "application/x-ndjson" in r.headers["content-type"]
