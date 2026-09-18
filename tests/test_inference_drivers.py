"""Driver contract: both engines behave identically to every caller above them.

The same suite runs against the Ollama driver and the vLLM driver through a fake
HTTP server. Anything that differs here would differ for a child, because the
proxy, the safety blocks and the guidance enforcer sit on top of this interface.
"""

import json

import httpx
import pytest

from core.inference import ollama_driver, vllm_driver
from core.inference.base import (
    ChatRequest,
    EngineOverloaded,
    EngineTimeout,
    EngineUnreachable,
    ModelNotAvailable,
)

REQ = ChatRequest(
    model="snflwr.ai-31b",
    messages=[{"role": "user", "content": "What is 2 + 2?"}],
    options={"num_predict": 64},
)

OPENAI_OK = {
    "choices": [{"message": {"role": "assistant", "content": "Let's work it out."},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 7700, "completion_tokens": 42},
}

OLLAMA_OK = {
    "model": "snflwr.ai-31b",
    "message": {"role": "assistant", "content": "Let's work it out."},
    "done": True,
    "prompt_eval_count": 7700,
    "eval_count": 42,
}


def _vllm(handler):
    driver = vllm_driver.VLLMDriver(base_url="http://vllm:8000", persona="P", sampling={})
    driver._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return driver


def _ollama(handler):
    driver = ollama_driver.OllamaDriver(base_url="http://ollama:11434")
    driver._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return driver


def _driver(kind, handler):
    return _vllm(handler) if kind == "vllm" else _ollama(handler)


def _ok_handler(kind):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=OPENAI_OK if kind == "vllm" else OLLAMA_OK)
    return handler


ENGINES = ["vllm", "ollama"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ENGINES)
class TestContract:
    async def test_chat_returns_text_and_usage(self, kind):
        driver = _driver(kind, _ok_handler(kind))
        result = await driver.chat(REQ, timeout_s=30)
        assert result.text == "Let's work it out."
        assert result.usage == {"input": 7700, "output": 42}

    async def test_connection_refused_maps_to_engine_unreachable(self, kind):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(EngineUnreachable):
            await _driver(kind, handler).chat(REQ, timeout_s=5)

    async def test_read_timeout_maps_to_engine_timeout(self, kind):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(EngineTimeout):
            await _driver(kind, handler).chat(REQ, timeout_s=5)

    async def test_server_overload_maps_to_engine_overloaded(self, kind):
        def handler(request):
            return httpx.Response(503, text="server overloaded")

        with pytest.raises(EngineOverloaded):
            await _driver(kind, handler).chat(REQ, timeout_s=5)

    async def test_unknown_model_maps_to_model_not_available(self, kind):
        def handler(request):
            return httpx.Response(404, text="model not found")

        with pytest.raises(ModelNotAvailable):
            await _driver(kind, handler).chat(REQ, timeout_s=5)

    async def test_streaming_yields_the_same_text_as_buffered(self, kind):
        if kind == "vllm":
            frames = [
                'data: {"choices":[{"delta":{"content":"Let\'s "}}]}',
                'data: {"choices":[{"delta":{"content":"work "}}]}',
                'data: {"choices":[{"delta":{"content":"it out."}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":7700,"completion_tokens":42}}',
                "data: [DONE]",
            ]
            payload = "\n\n".join(frames).encode()
        else:
            lines = [
                {"message": {"content": "Let's "}, "done": False},
                {"message": {"content": "work "}, "done": False},
                {"message": {"content": "it out."}, "done": False},
                {"message": {"content": ""}, "done": True,
                 "prompt_eval_count": 7700, "eval_count": 42},
            ]
            payload = b"".join((json.dumps(x) + "\n").encode() for x in lines)

        def handler(request):
            return httpx.Response(200, content=payload)

        chunks = []
        async for chunk in _driver(kind, handler).stream(REQ, timeout_s=30):
            chunks.append(chunk)
        assert "".join(c.text for c in chunks) == "Let's work it out."
        assert chunks[-1].done is True
        assert chunks[-1].usage == {"input": 7700, "output": 42}

    async def test_health_reports_reachability(self, kind):
        driver = _driver(kind, _ok_handler(kind))

        def models_handler(request):
            if kind == "vllm":
                return httpx.Response(200, json={"data": [{"id": "snflwr-31b"}]})
            return httpx.Response(200, json={"models": [{"name": "snflwr.ai-31b"}]})

        driver._client = httpx.AsyncClient(transport=httpx.MockTransport(models_handler))
        health = await driver.health()
        assert health.reachable is True
        assert health.models


@pytest.mark.asyncio
class TestVLLMSpecifics:
    async def test_persona_and_sampling_are_sent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=OPENAI_OK)

        driver = vllm_driver.VLLMDriver(base_url="http://vllm:8000",
                                        persona="PERSONA TEXT",
                                        sampling={"temperature": 0.7, "stop": ["Student:"]})
        driver._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await driver.chat(REQ, timeout_s=30)
        assert seen["messages"][0] == {"role": "system", "content": "PERSONA TEXT"}
        assert seen["temperature"] == 0.7
        assert seen["stop"] == ["Student:"]
        assert seen["chat_template_kwargs"] == {"thinking": False}

    async def test_posts_to_the_openai_endpoint(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=OPENAI_OK)

        driver = _vllm(handler)
        await driver.chat(REQ, timeout_s=30)
        assert seen["url"].endswith("/v1/chat/completions")

    async def test_capabilities_advertise_batching(self):
        assert _vllm(_ok_handler("vllm")).capabilities().batching is True


@pytest.mark.asyncio
class TestOllamaSpecifics:
    async def test_posts_to_the_ollama_endpoint_with_the_body_unchanged(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=OLLAMA_OK)

        await _ollama(handler).chat(REQ, timeout_s=30)
        assert seen["url"].endswith("/api/chat")
        # Ollama reads the persona from the Modelfile: we must NOT inject one.
        assert all(m["role"] != "system" for m in seen["body"]["messages"])
        assert seen["body"]["options"]["num_predict"] == 64

    async def test_capabilities_say_it_does_not_batch(self):
        """Measured: throughput is flat from 1 to 8 concurrent requests."""
        assert _ollama(_ok_handler("ollama")).capabilities().batching is False
        assert _ollama(_ok_handler("ollama")).capabilities().max_slots == 1
