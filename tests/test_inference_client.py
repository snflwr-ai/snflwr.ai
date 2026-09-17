"""The client: plan in, engine calls out, with admission control around them."""

import json

import pytest

from core import serving_plan
from core.inference import client as client_mod
from core.inference.base import (
    ChatChunk,
    ChatRequest,
    ChatResult,
    EngineOverloaded,
)


class FakeDriver:
    name = "fake"

    def __init__(self, text="Hello.", usage=None, error=None):
        self.text = text
        self.usage = usage
        self.error = error
        self.calls = []

    async def chat(self, req, *, timeout_s):
        self.calls.append(("chat", req, timeout_s))
        if self.error:
            raise self.error
        return ChatResult(text=self.text, usage=self.usage, raw={})

    async def stream(self, req, *, timeout_s):
        self.calls.append(("stream", req, timeout_s))
        if self.error:
            raise self.error
        for part in ("Hel", "lo."):
            yield ChatChunk(text=part, done=False)
        yield ChatChunk(text="", done=True, usage=self.usage)

    async def health(self):
        from core.inference.base import DriverHealth

        return DriverHealth(reachable=True, models=("fake",))

    def capabilities(self):
        from core.inference.base import Capabilities

        return Capabilities(batching=False, prefix_cache=False,
                            speculative_decoding=False, max_slots=1)


def _plan(engine="ollama", slots=1, model="snflwr.ai-31b", tutoring=True):
    return serving_plan.ServingPlan(
        engine=engine, tutor_model=model, quality_tier="certified",
        tutoring_enabled=tutoring, num_ctx=16384, max_concurrent_requests=slots,
        reason="test",
    )


def _client(driver, plan=None):
    return client_mod.InferenceClient(plan=plan or _plan(), driver=driver)


@pytest.mark.asyncio
class TestCalls:
    async def test_chat_passes_through_to_the_driver(self):
        driver = FakeDriver(text="Let's work it out.")
        result = await _client(driver).chat(
            ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}]),
            timeout_s=30,
        )
        assert result.text == "Let's work it out."
        assert driver.calls[0][0] == "chat"

    async def test_stream_yields_driver_chunks(self):
        chunks = []
        async for chunk in _client(FakeDriver()).stream(
            ChatRequest(model="m", messages=[], stream=True), timeout_s=30
        ):
            chunks.append(chunk)
        assert "".join(c.text for c in chunks) == "Hello."

    async def test_overload_propagates_rather_than_being_swallowed(self):
        client = client_mod.InferenceClient(
            plan=_plan(slots=1), driver=FakeDriver(error=EngineOverloaded("full"))
        )
        with pytest.raises(EngineOverloaded):
            await client.chat(ChatRequest(model="m", messages=[]), timeout_s=5)


@pytest.mark.asyncio
class TestOllamaShapedHelpers:
    """The proxy speaks Ollama's shape; the client hands it back in that shape
    whichever engine actually served the turn."""

    async def test_buffered_response_is_ollama_shaped(self):
        body = json.dumps({"model": "snflwr.ai-31b",
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        driver = FakeDriver(text="Answer.", usage={"input": 10, "output": 2})
        out = await _client(driver).chat_ollama_bytes(body, timeout_s=30)
        payload = json.loads(out)
        assert payload["message"]["content"] == "Answer."
        assert payload["done"] is True
        assert payload["prompt_eval_count"] == 10

    async def test_streamed_response_is_ollama_ndjson(self):
        body = json.dumps({"model": "snflwr.ai-31b", "messages": [], "stream": True}).encode()
        lines = []
        async for chunk in _client(FakeDriver()).stream_ollama_ndjson(body, timeout_s=30):
            lines.append(chunk)
        texts = [json.loads(line)["message"]["content"] for line in lines]
        assert "".join(texts) == "Hello."
        assert json.loads(lines[-1])["done"] is True

    async def test_invalid_json_body_is_rejected_clearly(self):
        with pytest.raises(ValueError):
            await _client(FakeDriver()).chat_ollama_bytes(b"not json", timeout_s=5)


@pytest.mark.asyncio
class TestAdmissionIsApplied:
    async def test_calls_are_capped_at_the_plan_concurrency(self):
        import asyncio

        driver = FakeDriver()

        async def slow_chat(req, *, timeout_s):
            await asyncio.sleep(0.05)
            return ChatResult(text="x", usage=None, raw={})

        driver.chat = slow_chat  # type: ignore[assignment]
        client = client_mod.InferenceClient(
            plan=_plan(slots=1), driver=driver, queue_wait_s=0.01, max_queue=10
        )
        req = ChatRequest(model="m", messages=[])
        first = asyncio.create_task(client.chat(req, timeout_s=5))
        await asyncio.sleep(0.005)
        with pytest.raises(EngineOverloaded):
            await client.chat(req, timeout_s=5)
        await first

    async def test_one_turn_may_make_several_calls(self):
        """gate + draft + confirm + rewrite inside one admitted turn."""
        client = client_mod.InferenceClient(plan=_plan(slots=1), driver=FakeDriver(),
                                            queue_wait_s=0.01, max_queue=10)
        req = ChatRequest(model="m", messages=[])
        async with client.turn():
            for _ in range(4):
                await client.chat(req, timeout_s=5)


class TestDriverSelection:
    def test_ollama_plan_builds_the_ollama_driver(self, monkeypatch):
        monkeypatch.setattr(client_mod, "get_plan", lambda refresh=False: _plan("ollama"))
        built = client_mod.build_client(refresh=True)
        assert built.driver.name == "ollama"

    def test_vllm_plan_builds_the_vllm_driver_with_the_persona(self, monkeypatch):
        monkeypatch.setattr(client_mod, "get_plan", lambda refresh=False: _plan("vllm", slots=8))
        built = client_mod.build_client(refresh=True)
        assert built.driver.name == "vllm"
        assert built.driver._persona  # from the Modelfile, not empty

    def test_vllm_plan_lets_the_engine_schedule(self, monkeypatch):
        """vLLM batches internally, so the app gate must not serialize it."""
        monkeypatch.setattr(client_mod, "get_plan", lambda refresh=False: _plan("vllm", slots=8))
        built = client_mod.build_client(refresh=True)
        assert built.admission.stats()["max_concurrent"] in (0, 8)
