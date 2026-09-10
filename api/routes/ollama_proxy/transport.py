"""Transport helpers: forward requests to Ollama and stream responses."""

from __future__ import annotations

import json as _json

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from config import system_config
from core import gpu_placement
from utils.circuit_breaker import ollama_circuit
from utils.logger import get_logger

logger = get_logger(__name__)

_OLLAMA_READ_TIMEOUT = 300.0  # seconds — matches OLLAMA_TIMEOUT default


def _inject_gpu_placement(path: str, content):
    """Add ``options.num_gpu`` to an outgoing /api/chat or /api/generate body.

    Applied HERE rather than at each call site because every proxy path — the
    student turn, the streaming path, the guidance-enforcer regeneration and the
    pedagogy one-shot — converges on this transport. Editing four call sites
    would leave the next one to be written uncovered.

    See core.gpu_placement: the tutor takes the GPU when it is free, and backs
    off to CPU for a cooldown after each swap it causes, so it cannot thrash
    against IronClaw's brain. Fail-open — any problem returns the body untouched,
    which is exactly current behaviour.
    """
    if path not in ("/api/chat", "/api/generate") or not content:
        return content
    try:
        body = _json.loads(content)
        if not isinstance(body, dict) or not body.get("model"):
            return content
        opts = body.get("options")
        if not isinstance(opts, dict):
            opts = {}
        body["options"] = gpu_placement.apply_to_options(opts, body["model"])
        return _json.dumps(body).encode()
    except Exception as exc:  # noqa: BLE001 - never fail a child's turn over placement
        logger.warning("transport: GPU placement injection skipped (%s)", exc)
        return content


async def _forward_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Send *method* + *path* to the real Ollama backend and return the raw response.

    Gated by ``ollama_circuit``: when the backend has been failing, ``can_execute``
    fast-fails (raising ``ConnectError``, which every caller already maps to a
    graceful 503) instead of hanging on a dead backend. Each completed round-trip
    records success and each transport error records failure, so the breaker
    actually reflects backend health (and self-heals via its half-open probe).
    """
    if not ollama_circuit.can_execute():
        raise httpx.ConnectError("Ollama circuit breaker open")
    if "content" in kwargs:
        kwargs["content"] = _inject_gpu_placement(path, kwargs["content"])
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT)
        ) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        ollama_circuit.record_failure(exc)
        raise
    ollama_circuit.record_success()
    return resp


async def _proxy_to_ollama(request: Request, path: str) -> Response:
    """Generic handler: reads request body, forwards to Ollama, returns response.

    Returns HTTP 503 when Ollama is unreachable.
    """
    body = await request.body()
    try:
        upstream = await _forward_request(
            request.method,
            path,
            content=body,
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length")
            },
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Ollama backend unreachable"},
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


async def _stream_chunks_from_ollama(body: bytes, headers: dict):
    """Open a streaming connection to Ollama and yield NDJSON chunks, one line at
    a time, with the model's ``message.thinking`` field stripped out.

    Separated from the response builder so the chat handler can buffer chunks
    through ``check_output`` before forwarding them to the client.

    Yields whole NDJSON lines (not raw byte chunks): Ollama's ``aiter_bytes``
    boundaries are arbitrary and can split a JSON object mid-line, so we buffer
    to newline boundaries before stripping ``thinking`` per line (see
    ``blocks._strip_thinking_from_ndjson_line``). ``content`` is untouched, so
    downstream vetting and text extraction are unaffected.
    """
    from api.routes.ollama_proxy.blocks import _strip_thinking_from_ndjson_line

    if not ollama_circuit.can_execute():
        raise httpx.ConnectError("Ollama circuit breaker open")
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    # STUDENT path. Same placement injection as _forward_request; this helper
    # builds its own request so it bypasses that choke point. This is the one
    # that decides whether a child waits ~2 s or ~14 s for an answer.
    req = client.build_request(
        "POST",
        url,
        content=_inject_gpu_placement("/api/chat", body),
        headers=headers,
    )
    try:
        resp = await client.send(req, stream=True)
    except httpx.TransportError as exc:
        await client.aclose()
        ollama_circuit.record_failure(exc)
        raise
    ollama_circuit.record_success()
    buffer = b""
    try:
        async for chunk in resp.aiter_bytes():
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield _strip_thinking_from_ndjson_line(line) + b"\n"
        # Flush any trailing line that arrived without a closing newline.
        if buffer.strip():
            yield _strip_thinking_from_ndjson_line(buffer)
    finally:
        await resp.aclose()
        await client.aclose()


async def _stream_chat_from_ollama(
    body: bytes, headers: dict
) -> StreamingResponse | JSONResponse:
    """Stream Ollama chat response back to the client without inspection.

    Used by the admin pass-through path; student traffic uses
    ``_stream_chunks_from_ollama`` + ``check_output`` instead.
    """
    if not ollama_circuit.can_execute():
        return JSONResponse(
            status_code=503,
            content={"detail": "Ollama backend unreachable"},
        )
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    try:
        # Same placement injection as _forward_request. These streaming helpers
        # build their own request, so they bypass that choke point entirely —
        # and _stream_chunks_from_ollama is the STUDENT path, the one that
        # actually matters for tutor latency.
        req = client.build_request(
            "POST",
            url,
            content=_inject_gpu_placement("/api/chat", body),
            headers=headers,
        )
        resp = await client.send(req, stream=True)
    except httpx.TransportError as exc:
        await client.aclose()
        ollama_circuit.record_failure(exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Ollama backend unreachable"},
        )
    ollama_circuit.record_success()

    async def _yield_chunks():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _yield_chunks(),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/x-ndjson"),
    )
