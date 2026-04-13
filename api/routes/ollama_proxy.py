"""
Ollama-compatible pass-through proxy.

OWU's OLLAMA_BASE_URL points at snflwr-api.  Non-chat Ollama API calls
(tags, show, generate, embed, pull, etc.) are forwarded unchanged to the
real Ollama backend configured in system_config.OLLAMA_PROXY_TARGET.
"""

import json as _json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

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


# ---------------------------------------------------------------------------
# Safety pipeline helpers
# ---------------------------------------------------------------------------


def _get_user_from_headers(request: Request) -> tuple:
    """Extract user identity from OWU forwarded headers.

    Returns ``(user_id, role)``.  Fails closed: missing headers yield
    ``(None, "user")`` so the request is treated as a student.
    """
    user_id: Optional[str] = request.headers.get("X-OpenWebUI-User-Id") or None
    role: str = request.headers.get("X-OpenWebUI-User-Role") or "user"
    return user_id, role


async def _get_profile_for_user(user_id: Optional[str]) -> str:
    """Look up the first child profile linked to *user_id*.

    Returns the profile_id string.  Fails closed: any error yields
    ``"safety_required_<user_id>"`` so the safety pipeline still runs.
    """
    if user_id is None:
        return "safety_required_unknown"
    try:
        from core.authentication import auth_manager
        from core.profile_manager import ProfileManager

        pm = ProfileManager(auth_manager.db)
        profiles = pm.get_profiles_by_parent(user_id)
        if profiles:
            return profiles[0].profile_id
        # No profiles found — still run safety with a synthetic profile id
        return f"safety_required_{user_id}"
    except Exception as exc:
        logger.warning(
            "_get_profile_for_user failed for user %s (fail-closed): %s",
            user_id,
            exc,
        )
        return f"safety_required_{user_id}"


def _extract_last_user_message(messages: list) -> str:
    """Return the text of the last user-role message in *messages*.

    Handles both plain-string content and multimodal parts (list of dicts
    with ``type: "text"``).  Returns ``""`` when no user message is found.
    """
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Multimodal format — gather all text parts
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return " ".join(parts)
    return ""


def _ollama_block_response(model: str, block_message: str) -> dict:
    """Build an Ollama-format response dict for a blocked message."""
    return {
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": {"role": "assistant", "content": block_message},
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "eval_count": 0,
    }


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------


async def _stream_chat_from_ollama(
    body: bytes, headers: dict
) -> StreamingResponse | JSONResponse:
    """Open a streaming connection to Ollama and proxy chunks back."""
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    try:
        req = client.build_request(
            "POST",
            url,
            content=body,
            headers=headers,
        )
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


# ---------------------------------------------------------------------------
# Chat endpoint with safety pipeline
# ---------------------------------------------------------------------------


@router.post("/chat")
async def proxy_chat(request: Request) -> Response:
    """POST /api/chat — run safety pipeline for students, pass-through for admins.

    Supports both streaming (``stream=True``) and non-streaming responses.
    """
    try:
        body_bytes = await request.body()
        body = _json.loads(body_bytes)
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    model: str = body.get("model", "")
    messages: list = body.get("messages", [])
    stream: bool = body.get("stream", False)

    user_id, role = _get_user_from_headers(request)

    # Admins bypass the safety pipeline entirely
    if role == "admin":
        logger.debug("Admin user %s — forwarding /api/chat directly", user_id)
        # Suppress extended-thinking so OWU doesn't render a raw <thinking> block
        body["think"] = False
        body_bytes = _json.dumps(body).encode()
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        if stream:
            return await _stream_chat_from_ollama(body_bytes, fwd_headers)
        try:
            upstream = await _forward_request(
                "POST",
                "/api/chat",
                content=body_bytes,
                headers=fwd_headers,
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

    # Student path — run safety pipeline
    profile_id = await _get_profile_for_user(user_id)

    # Resolve age from profile (best-effort; None is acceptable)
    age: Optional[int] = None
    try:
        from core.authentication import auth_manager
        from core.profile_manager import ProfileManager

        pm = ProfileManager(auth_manager.db)
        profile = pm.get_profile(profile_id)
        if profile is not None:
            age = profile.age or None
    except Exception as exc:
        logger.debug("Could not resolve age for profile %s: %s", profile_id, exc)

    text = _extract_last_user_message(messages)

    try:
        from safety.pipeline import safety_pipeline

        result = safety_pipeline.check_input(text=text, age=age, profile_id=profile_id)
    except Exception as exc:
        logger.error("Safety pipeline raised unexpectedly: %s", exc, exc_info=True)
        # Fail closed — block the message
        block_msg = "I'm unable to process that request right now."
        return JSONResponse(content=_ollama_block_response(model, block_msg))

    if not result.is_safe:
        block_message = (
            result.modified_content
            or safety_pipeline.get_safe_response(result)
            or "I'm not able to help with that right now. Let's try something else!"
        )
        logger.info(
            "Safety blocked message for profile %s (category=%s)",
            profile_id,
            result.category,
        )
        return JSONResponse(content=_ollama_block_response(model, block_message))

    # Safe — forward to Ollama
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    if stream:
        return await _stream_chat_from_ollama(body_bytes, fwd_headers)

    try:
        upstream = await _forward_request(
            "POST",
            "/api/chat",
            content=body_bytes,
            headers=fwd_headers,
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


# ---------------------------------------------------------------------------
# Pass-through endpoints
# ---------------------------------------------------------------------------


@router.get("/tags")
async def proxy_tags(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/tags")


@router.post("/show")
async def proxy_show(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/show")


@router.post("/generate")
async def proxy_generate(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/generate")


@router.post("/embed")
async def proxy_embed(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/embed")


@router.post("/embeddings")
async def proxy_embeddings(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/embeddings")


@router.delete("/delete")
async def proxy_delete(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/delete")


@router.post("/pull")
async def proxy_pull(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/pull")


@router.post("/copy")
async def proxy_copy(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/copy")


@router.get("/version")
async def proxy_version(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/version")
