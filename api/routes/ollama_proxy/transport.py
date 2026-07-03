"""Transport helpers: forward requests to Ollama and stream responses."""

from __future__ import annotations

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

_OLLAMA_READ_TIMEOUT = 300.0  # seconds — matches OLLAMA_TIMEOUT default


async def _forward_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Send *method* + *path* to the real Ollama backend and return the raw response."""
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}{path}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT)
    ) as client:
        return await client.request(method, url, **kwargs)


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

    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    req = client.build_request("POST", url, content=body, headers=headers)
    resp = await client.send(req, stream=True)
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
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    try:
        req = client.build_request("POST", url, content=body, headers=headers)
        resp = await client.send(req, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return JSONResponse(
            status_code=503,
            content={"detail": "Ollama backend unreachable"},
        )

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
