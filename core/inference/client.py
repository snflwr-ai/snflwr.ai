"""The engine-agnostic client every model call goes through.

Responsibilities, in order of importance:

1. **Pick the driver from the serving plan** — nobody else decides which engine
   is in use, so there is one place to read and one place to change.
2. **Admission control** — one turn takes one slot; nested calls inside the turn
   (the enforcer's gate, confirm and rewrite) share it. Over capacity, callers
   get `EngineOverloaded` and the proxy turns that into a busy message, never
   into a degraded answer (see `core.inference.admission`).
3. **Speak the proxy's language** — the proxy, safety blocks, history ledger and
   enforcer all read Ollama-shaped JSON. `chat_ollama_bytes` and
   `stream_ollama_ndjson` hand back exactly that, whichever engine served it.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from config import system_config
from core.inference import modelfile
from core.inference import ollama_shape as shape
from core.inference.admission import Admission
from core.inference.base import ChatChunk, ChatRequest, ChatResult, DriverHealth
from core.inference.ollama_driver import OllamaDriver
from core.inference.vllm_driver import VLLMDriver
from core.serving_plan import ServingPlan, get_plan

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_WAIT_S = 20.0
DEFAULT_MAX_QUEUE = 64


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric %s=%r", name, raw)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r", name, raw)
        return default


class InferenceClient:
    """One engine, one admission gate, two call shapes."""

    def __init__(
        self,
        *,
        plan: ServingPlan,
        driver,
        queue_wait_s: Optional[float] = None,
        max_queue: Optional[int] = None,
    ):
        self.plan = plan
        self.driver = driver
        # vLLM schedules its own batches; the app gate would only get in the way.
        max_concurrent = 0 if plan.engine == "vllm" else plan.max_concurrent_requests
        self.admission = Admission(
            max_concurrent=max_concurrent,
            queue_wait_s=(
                queue_wait_s
                if queue_wait_s is not None
                else _env_float("INFERENCE_QUEUE_WAIT_S", DEFAULT_QUEUE_WAIT_S)
            ),
            max_queue=(
                max_queue
                if max_queue is not None
                else _env_int("INFERENCE_MAX_QUEUE", DEFAULT_MAX_QUEUE)
            ),
        )

    # -- turn scope --------------------------------------------------------
    @asynccontextmanager
    async def turn(self):
        """Hold one slot for a whole student turn (3-5 model calls)."""
        async with self.admission.slot():
            yield

    # -- engine-neutral calls ---------------------------------------------
    async def chat(self, req: ChatRequest, *, timeout_s: float) -> ChatResult:
        async with self.admission.slot():
            return await self.driver.chat(req, timeout_s=timeout_s)

    async def stream(
        self, req: ChatRequest, *, timeout_s: float
    ) -> AsyncIterator[ChatChunk]:
        async with self.admission.slot():
            async for chunk in self.driver.stream(req, timeout_s=timeout_s):
                yield chunk

    # -- Ollama-shaped calls (what the proxy uses) -------------------------
    @staticmethod
    def _parse(body_bytes: bytes) -> dict:
        try:
            body = json.loads(body_bytes)
        except ValueError as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    async def chat_ollama_bytes(self, body_bytes: bytes, *, timeout_s: float) -> bytes:
        body = self._parse(body_bytes)
        req = shape.request_from_ollama(body)
        result = await self.chat(req, timeout_s=timeout_s)
        return json.dumps(shape.result_to_ollama(result, model=req.model)).encode()

    async def stream_ollama_ndjson(
        self, body_bytes: bytes, *, timeout_s: float
    ) -> AsyncIterator[bytes]:
        body = self._parse(body_bytes)
        req = shape.request_from_ollama(body)
        async for chunk in self.stream(req, timeout_s=timeout_s):
            yield shape.chunk_to_ollama_ndjson(chunk, model=req.model)

    async def health(self) -> DriverHealth:
        return await self.driver.health()

    def stats(self) -> dict:
        return {
            "engine": self.driver.name,
            "plan": self.plan.as_dict(),
            "admission": self.admission.stats(),
            "capabilities": vars(self.driver.capabilities()),
        }


def build_client(refresh: bool = False) -> InferenceClient:
    """Construct the client this deployment's hardware calls for."""
    plan = get_plan(refresh=refresh)
    if plan.engine == "vllm":
        spec = modelfile.get()
        driver = VLLMDriver(
            base_url=os.getenv("VLLM_BASE_URL", "http://vllm:8000"),
            persona=spec.persona,
            sampling=spec.sampling,
            max_slots=plan.max_concurrent_requests,
        )
    else:
        driver = OllamaDriver(base_url=system_config.OLLAMA_HOST)
    logger.info("inference client: %s", plan.summary_line())
    return InferenceClient(plan=plan, driver=driver)


_CLIENT: Optional[InferenceClient] = None


def get_client(refresh: bool = False) -> InferenceClient:
    global _CLIENT
    if _CLIENT is None or refresh:
        _CLIENT = build_client(refresh=refresh)
    return _CLIENT
