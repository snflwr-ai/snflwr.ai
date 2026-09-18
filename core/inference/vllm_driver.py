"""vLLM driver — the OpenAI-protocol engine.

Why vLLM is the scaling path (load test 2026-09-17, 23 GB card, Ollama):
throughput was flat at ~5.7 messages/min from 1 to 8 concurrent requests because
Ollama serves one request at a time. vLLM batches continuously and caches the
shared 7,696-token system prompt across students, which is the difference
between three concurrent learners and tens.

What this driver owes the rest of the app: an answer that is indistinguishable,
in shape and in content, from the Ollama path. The persona and sampling
parameters come from the Modelfile (see `core.inference.modelfile`), because
vLLM serves plain weights and applies nothing of its own.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

import httpx

from core.inference import ollama_shape as shape
from core.inference.base import (
    Capabilities,
    ChatChunk,
    ChatRequest,
    ChatResult,
    DriverHealth,
    EngineOverloaded,
    EngineTimeout,
    EngineUnreachable,
    InferenceError,
    ModelNotAvailable,
)

logger = logging.getLogger(__name__)

# 429/503 mean "no capacity right now"; 404 means the model or route is absent.
_OVERLOAD_STATUS = frozenset({429, 503})
_NOT_AVAILABLE_STATUS = frozenset({404})


class VLLMDriver:
    """Talks OpenAI `/v1/chat/completions` to a vLLM server."""

    name = "vllm"

    def __init__(
        self,
        *,
        base_url: str,
        persona: str,
        sampling: dict,
        max_slots: int = 1,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._persona = persona
        self._sampling = dict(sampling or {})
        self._max_slots = max(1, int(max_slots))
        self._client = client or httpx.AsyncClient()

    # -- internals ---------------------------------------------------------
    def _payload(self, req: ChatRequest) -> dict:
        return shape.request_to_openai(
            req, persona=self._persona, sampling=self._sampling
        )

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        detail = resp.text[:500]
        if resp.status_code in _OVERLOAD_STATUS:
            raise EngineOverloaded(f"vllm returned {resp.status_code}: {detail}")
        if resp.status_code in _NOT_AVAILABLE_STATUS:
            raise ModelNotAvailable(f"vllm returned {resp.status_code}: {detail}")
        raise InferenceError(f"vllm returned {resp.status_code}: {detail}")

    # -- interface ---------------------------------------------------------
    async def chat(self, req: ChatRequest, *, timeout_s: float) -> ChatResult:
        payload = self._payload(req)
        payload["stream"] = False
        payload.pop("stream_options", None)
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/chat/completions", json=payload, timeout=timeout_s
            )
        except httpx.TimeoutException as exc:
            raise EngineTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable(str(exc)) from exc
        self._raise_for_status(resp)
        return shape.result_from_openai(resp.json())

    async def stream(
        self, req: ChatRequest, *, timeout_s: float
    ) -> AsyncIterator[ChatChunk]:
        payload = self._payload(req)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        usage: Optional[dict] = None
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=timeout_s,
            ) as resp:
                self._raise_for_status(resp)
                async for line in resp.aiter_lines():
                    chunk = shape.chunk_from_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk.usage:
                        usage = chunk.usage
                    if chunk.done:
                        # Collapse vLLM's usage frame and [DONE] into ONE final
                        # chunk, so the shape matches Ollama's last NDJSON line.
                        continue
                    yield chunk
        except httpx.TimeoutException as exc:
            raise EngineTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable(str(exc)) from exc
        yield ChatChunk(text="", done=True, usage=usage)

    async def health(self) -> DriverHealth:
        try:
            resp = await self._client.get(f"{self._base_url}/v1/models", timeout=5.0)
        except httpx.HTTPError as exc:
            return DriverHealth(reachable=False, detail=str(exc))
        if resp.status_code != 200:
            return DriverHealth(reachable=False, detail=f"status {resp.status_code}")
        try:
            models = tuple(m.get("id", "") for m in resp.json().get("data", []))
        except ValueError:
            models = ()
        return DriverHealth(reachable=True, models=models)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            batching=True,
            prefix_cache=True,
            speculative_decoding=True,
            max_slots=self._max_slots,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
