"""Hold one inference slot for the whole turn, including streamed ones.

This is a FastAPI dependency rather than a block inside the handler for one
reason: dependency teardown runs after the response has been fully sent, so a
streamed answer keeps its slot until the last token instead of releasing it the
moment the handler returns a `StreamingResponse`.

When there is no capacity the dependency raises `InferenceBusy`, which the app's
exception handler renders as a child-safe "ask me again in a moment" message in
whichever shape the client asked for. It is deliberately NOT the withholding
fallback: that text teaches, and a queue is not a teaching moment (measured
2026-09-17 — 40 of 60 replies at 20 students were withholding fallbacks caused
purely by queue time).
"""

from __future__ import annotations

import json

from fastapi import Request

from core.inference import client as inference_client
from core.inference.base import EngineOverloaded
from utils.logger import get_logger

logger = get_logger(__name__)


class InferenceBusy(Exception):
    """No inference capacity for this turn."""

    def __init__(self, *, model: str = "", stream: bool = False):
        super().__init__("inference capacity exhausted")
        self.model = model
        self.stream = stream


async def _request_shape(request: Request) -> tuple[str, bool]:
    """(model, stream) from the body, best-effort — the body stays cached."""
    try:
        body = json.loads(await request.body())
    except Exception:  # noqa: BLE001 - a body we cannot read still gets a block
        return "", False
    if not isinstance(body, dict):
        return "", False
    return str(body.get("model") or ""), bool(body.get("stream", False))


async def inference_slot(request: Request):
    """Admit this turn, or raise `InferenceBusy`."""
    client = inference_client.get_client()
    turn = client.turn()
    try:
        await turn.__aenter__()
    except EngineOverloaded:
        model, stream = await _request_shape(request)
        logger.warning("inference busy: turn rejected before it started")
        raise InferenceBusy(model=model, stream=stream) from None
    try:
        yield
    finally:
        await turn.__aexit__(None, None, None)
