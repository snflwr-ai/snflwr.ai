"""Ollama driver — today's engine, behind the same interface as vLLM.

This driver deliberately changes nothing about how Ollama is called: same
endpoint, same body, same NDJSON streaming, same GPU placement injection. It
exists so that callers stop importing an engine directly, and so the vLLM
driver has something to be identical to.

One property is worth stating because the serving plan depends on it: Ollama
serves ONE request at a time on a single-GPU box. Measured 2026-09-17,
throughput was flat at ~5.7 messages/min whether 1, 2, 4 or 8 requests were in
flight, and the extra requests simply queued. So `capabilities().max_slots` is 1
and admission control lives in the app for this engine.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

import httpx

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

_OVERLOAD_STATUS = frozenset({429, 503})
_NOT_AVAILABLE_STATUS = frozenset({404})


def _body(req: ChatRequest) -> dict:
    body: dict = {
        "model": req.model,
        "messages": list(req.messages),
        "stream": bool(req.stream),
    }
    if req.think is not None:
        body["think"] = req.think
    if req.options:
        body["options"] = dict(req.options)
    return body


def _usage_from_ollama(payload: dict) -> Optional[dict]:
    prompt = payload.get("prompt_eval_count")
    completion = payload.get("eval_count")
    if prompt is None and completion is None:
        return None
    return {"input": prompt or 0, "output": completion or 0}


class OllamaDriver:
    """Talks Ollama `/api/chat`."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        client: Optional[httpx.AsyncClient] = None,
        chat_path: str = "/api/chat",
        health_path: str = "/api/tags",
    ):
        # The paths are parameters because the remote driver speaks this exact
        # wire format to a snflwr tutor server rather than to Ollama itself,
        # where `/api/chat` is the session-authenticated student API and the
        # engine-shaped route lives under `/api/inference`.
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._chat_path = chat_path
        self._health_path = health_path

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        detail = resp.text[:500]
        if resp.status_code in _OVERLOAD_STATUS:
            raise EngineOverloaded(f"ollama returned {resp.status_code}: {detail}")
        if resp.status_code in _NOT_AVAILABLE_STATUS:
            raise ModelNotAvailable(f"ollama returned {resp.status_code}: {detail}")
        raise InferenceError(f"ollama returned {resp.status_code}: {detail}")

    async def chat(self, req: ChatRequest, *, timeout_s: float) -> ChatResult:
        body = _body(req)
        body["stream"] = False
        try:
            resp = await self._client.post(
                f"{self._base_url}{self._chat_path}", json=body, timeout=timeout_s
            )
        except httpx.TimeoutException as exc:
            raise EngineTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable(str(exc)) from exc
        self._raise_for_status(resp)
        payload = resp.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        text = message.get("content", "") if isinstance(message, dict) else ""
        return ChatResult(text=text, usage=_usage_from_ollama(payload), raw=payload)

    async def stream(
        self, req: ChatRequest, *, timeout_s: float
    ) -> AsyncIterator[ChatChunk]:
        body = _body(req)
        body["stream"] = True
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}{self._chat_path}",
                json=body,
                timeout=timeout_s,
            ) as resp:
                self._raise_for_status(resp)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    message = payload.get("message") or {}
                    yield ChatChunk(
                        text=(
                            message.get("content", "")
                            if isinstance(message, dict)
                            else ""
                        ),
                        done=bool(payload.get("done")),
                        usage=_usage_from_ollama(payload),
                    )
        except httpx.TimeoutException as exc:
            raise EngineTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable(str(exc)) from exc

    async def health(self) -> DriverHealth:
        try:
            resp = await self._client.get(
                f"{self._base_url}{self._health_path}", timeout=5.0
            )
        except httpx.HTTPError as exc:
            return DriverHealth(reachable=False, detail=str(exc))
        if resp.status_code != 200:
            return DriverHealth(reachable=False, detail=f"status {resp.status_code}")
        try:
            models = tuple(m.get("name", "") for m in resp.json().get("models", []))
        except ValueError:
            models = ()
        return DriverHealth(reachable=True, models=models)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            batching=False,
            prefix_cache=False,
            speculative_decoding=False,
            max_slots=1,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
