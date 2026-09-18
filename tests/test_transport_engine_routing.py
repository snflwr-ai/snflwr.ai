"""The transport routes to whichever engine the serving plan chose.

The proxy above it never learns which engine answered: an Ollama-shaped request
goes in and an Ollama-shaped response comes back either way. That is what keeps
`blocks.py`, the history ledger, the guidance enforcer and the sealed tutoring
measurements valid across an engine swap.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.ollama_proxy import transport
from core import serving_plan
from core.inference.base import ChatChunk, ChatResult


def _plan(engine):
    return serving_plan.ServingPlan(
        engine=engine, tutor_model="snflwr.ai-31b", quality_tier="certified",
        tutoring_enabled=True, num_ctx=16384, max_concurrent_requests=1, reason="test",
    )


def _body(stream=False):
    return json.dumps({
        "model": "snflwr.ai-31b",
        "messages": [{"role": "user", "content": "What is 2 + 2?"}],
        "stream": stream,
    }).encode()


class _FakeDriver:
    """Stands in for a vLLM server; the client above it is the real one."""

    name = "vllm"

    def __init__(self, text):
        self.text = text

    async def chat(self, req, *, timeout_s):
        return ChatResult(text=self.text, usage={"input": 9, "output": 4}, raw={})

    async def stream(self, req, *, timeout_s):
        yield ChatChunk(text=self.text, done=False)
        yield ChatChunk(text="", done=True, usage={"input": 9, "output": 4})

    async def health(self):
        from core.inference.base import DriverHealth

        return DriverHealth(reachable=True)

    def capabilities(self):
        from core.inference.base import Capabilities

        return Capabilities(batching=True, prefix_cache=True,
                            speculative_decoding=False, max_slots=8)


def _fake_client(text="Four-ish. What do you think?"):
    from core.inference.client import InferenceClient

    return InferenceClient(plan=_plan("vllm"), driver=_FakeDriver(text))


@pytest.mark.asyncio
class TestVLLMRouting:
    async def test_buffered_turn_is_served_by_the_engine_and_shaped_for_the_proxy(self):
        with (
            patch("core.serving_plan.get_plan", return_value=_plan("vllm")),
            patch("core.inference.client.get_client", return_value=_fake_client()),
        ):
            resp = await transport._forward_request("POST", "/api/chat", content=_body())
        payload = resp.json()
        assert payload["message"]["content"] == "Four-ish. What do you think?"
        assert payload["done"] is True
        assert payload["prompt_eval_count"] == 9

    async def test_streamed_turn_yields_ollama_ndjson_lines(self):
        with (
            patch("core.serving_plan.get_plan", return_value=_plan("vllm")),
            patch("core.inference.client.get_client", return_value=_fake_client()),
        ):
            lines = [
                chunk async for chunk in
                transport._stream_chunks_from_ollama(_body(stream=True), {})
            ]
        texts = [json.loads(line)["message"]["content"] for line in lines]
        assert "".join(texts) == "Four-ish. What do you think?"
        assert json.loads(lines[-1])["done"] is True

    async def test_non_chat_paths_still_go_to_ollama(self):
        """/api/tags, /api/show and friends have no OpenAI equivalent."""
        with (
            patch("core.serving_plan.get_plan", return_value=_plan("vllm")),
            patch("core.inference.client.get_client", return_value=_fake_client()),
            patch("httpx.AsyncClient.request",
                  new=AsyncMock(return_value=MagicMock(status_code=200))) as sent,
        ):
            await transport._forward_request("GET", "/api/tags")
        sent.assert_awaited()


@pytest.mark.asyncio
class TestOllamaRoutingIsUnchanged:
    async def test_chat_still_goes_straight_to_ollama(self):
        sent = AsyncMock(return_value=MagicMock(status_code=200, text=""))
        with (
            patch("core.serving_plan.get_plan", return_value=_plan("ollama")),
            patch("httpx.AsyncClient.request", new=sent),
        ):
            await transport._forward_request("POST", "/api/chat", content=_body())
        sent.assert_awaited()
        assert "/api/chat" in str(sent.await_args.args[1])


class TestCertifiedContextWindow:
    """The sealed run was measured at num_ctx 16384; the Modelfile pins 8192.

    Serving 8192 would leave ~500 tokens after the 7,696-token system prompt,
    which is how enforcement rewrites came to end mid-sentence. A deployment
    must serve the window that was certified, not the one the model file
    happens to carry.
    """

    def test_chat_bodies_carry_the_plans_context_window(self):
        with patch("core.serving_plan.get_plan", return_value=_plan("ollama")):
            out = transport._inject_context_length("/api/chat", _body())
        assert json.loads(out)["options"]["num_ctx"] == 16384

    def test_an_explicit_request_value_wins(self):
        """The eval harnesses set their own; they must not be overridden."""
        body = json.dumps({"model": "m", "messages": [], "options": {"num_ctx": 4096}}).encode()
        with patch("core.serving_plan.get_plan", return_value=_plan("ollama")):
            out = transport._inject_context_length("/api/chat", body)
        assert json.loads(out)["options"]["num_ctx"] == 4096

    def test_nothing_is_injected_when_tutoring_is_disabled(self):
        disabled = serving_plan.ServingPlan(
            engine="ollama", tutor_model=None, quality_tier="unsupported",
            tutoring_enabled=False, num_ctx=0, max_concurrent_requests=1, reason="test",
        )
        with patch("core.serving_plan.get_plan", return_value=disabled):
            out = transport._inject_context_length("/api/chat", _body())
        assert "options" not in json.loads(out)

    def test_other_paths_are_untouched(self):
        with patch("core.serving_plan.get_plan", return_value=_plan("ollama")):
            assert transport._inject_context_length("/api/tags", _body()) == _body()

    def test_a_broken_plan_leaves_the_body_alone(self):
        with patch("core.serving_plan.get_plan", side_effect=RuntimeError("boom")):
            assert transport._inject_context_length("/api/chat", _body()) == _body()
