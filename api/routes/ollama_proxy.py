"""
Ollama-compatible pass-through proxy.

OWU's OLLAMA_BASE_URL points at snflwr-api.  Non-chat Ollama API calls
(tags, show, generate, embed, pull, etc.) are forwarded unchanged to the
real Ollama backend configured in system_config.OLLAMA_PROXY_TARGET.
"""

import json as _json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from api.middleware.auth import get_current_session
from core.authentication import AuthSession
from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

# Every Ollama-compatible proxy route requires a Bearer token. The expected
# caller is Open WebUI, which carries INTERNAL_API_KEY via OLLAMA_API_KEY env.
# Without this gate, anyone able to reach the internal port can forge
# X-OpenWebUI-User-Role: admin and bypass the safety pipeline (audit C2).
router = APIRouter(prefix="/api", dependencies=[Depends(get_current_session)])

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


def _student_visible_models() -> set:
    """Model names a non-admin (student) may see in the chat dropdown.

    Only the canonical tutor model and its ``:latest`` tag — never the
    backbone, rollback, or backup variants that share the same Ollama
    backend but must never be selectable by a child.
    """
    default = (system_config.OLLAMA_DEFAULT_MODEL or "snflwr.ai").strip()
    base = default.split(":", 1)[0]
    return {base, f"{base}:latest"}


def _filter_tags_for_students(payload: bytes) -> bytes:
    """Drop non-public models from an Ollama ``/api/tags`` response body.

    Returns the payload unchanged if it can't be parsed or has no model
    list, so a malformed upstream response never crashes the dropdown.
    """
    try:
        data = _json.loads(payload)
    except (ValueError, _json.JSONDecodeError):
        return payload
    models = data.get("models")
    if not isinstance(models, list):
        return payload
    allowed = _student_visible_models()
    data["models"] = [
        m for m in models if isinstance(m, dict) and m.get("name") in allowed
    ]
    return _json.dumps(data).encode()


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


def _record_safety_incident(profile_id, result, content_snippet: str) -> None:
    """Best-effort human-in-the-loop escalation for a blocked student message.

    Records a DB incident and — for major/critical severities such as a
    self-harm disclosure — queues a parent alert via the incident logger.
    Without this, a child's crisis message shows the 988 safe-response but
    never notifies a trusted adult, and no incident is recorded for review.

    Students reach the model through this proxy (not api/routes/chat.py), so the
    escalation has to live here too. Fail-safe by design: any error is swallowed
    so the child's safe response is always delivered.
    """
    try:
        from safety.incident_logger import incident_logger

        incident_logger.log_incident(
            profile_id=profile_id or "unknown",
            session_id=None,
            incident_type=result.category.value,
            severity=result.severity.value,
            content_snippet=(content_snippet or "")[:200],
            metadata={
                "source": "ollama_proxy",
                "stage": getattr(result, "stage", None),
                "triggered_keywords": list(
                    getattr(result, "triggered_keywords", ()) or ()
                ),
            },
        )
    except Exception as exc:  # never let escalation break the child's response
        logger.error(
            "Failed to record safety incident (non-fatal): %s", exc, exc_info=True
        )


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------


async def _stream_chunks_from_ollama(body: bytes, headers: dict):
    """Open a streaming connection to Ollama and yield raw NDJSON chunks.

    Separated from the response builder so the chat handler can buffer chunks
    through ``check_output`` before forwarding them to the client.
    """
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    req = client.build_request("POST", url, content=body, headers=headers)
    resp = await client.send(req, stream=True)
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await resp.aclose()
        await client.aclose()


def _extract_text_from_ndjson_chunks(chunks: list[bytes]) -> str:
    """Concatenate the ``message.content`` fields from a list of Ollama NDJSON chunks."""
    parts: list[str] = []
    buffer = b"".join(chunks)
    for line in buffer.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts)


def _ollama_block_stream_bytes(model: str, block_message: str) -> bytes:
    """Build a single-chunk NDJSON stream body that delivers a safe-fallback block."""
    chunk = _json.dumps(_ollama_block_response(model, block_message)) + "\n"
    return chunk.encode()


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


# ---------------------------------------------------------------------------
# Chat endpoint with safety pipeline
# ---------------------------------------------------------------------------


@router.post("/chat")
async def proxy_chat(
    request: Request,
    session: AuthSession = Depends(get_current_session),
) -> Response:
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

    # Resolve identity. The X-OpenWebUI-User-* headers are only trustworthy when
    # the caller authenticated with the internal API key (i.e. Open WebUI, which
    # stamps the role from its own authenticated user). Any other caller holds a
    # real user session, so we MUST use the authenticated session role — never a
    # client-supplied header — to decide the safety-pipeline bypass (else a
    # non-admin session-holder could send X-OpenWebUI-User-Role: admin and skip
    # child-safety entirely).
    if session.user_id == "internal_service":
        user_id, role = _get_user_from_headers(request)
    else:
        user_id, role = session.user_id, session.role

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

    # License gate — students must hold a valid subscription/trial token.
    # Fail-safe: any licensing problem => gated, never a crash. Admins already
    # returned above and are never gated.
    from config import system_config

    if system_config.LICENSE_ENFORCED:
        import time as _time
        from core import licensing

        lic = licensing.current_state(int(_time.time()))
        if not lic.allowed:
            logger.info(
                "License gate blocked student %s (state=%s)", user_id, lic.state
            )
            msg = (
                "A snflwr.ai subscription is needed to use the tutor. "
                "Open Settings → Billing to subscribe or sign in."
            )
            return JSONResponse(content=_ollama_block_response(model, msg))

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

    # COPPA gate — an under-13 profile may only tutor once per-child parental
    # consent has been verified (coppa_verified=1). Consent is set per-profile
    # by /api/parental-consent/verify; it is never granted at profile creation
    # (finding C1). Only block on a positively-resolved unverified under-13
    # profile; unknown/synthetic profiles fall through to the safety pipeline.
    try:
        from core.authentication import auth_manager as _am

        rows = _am.db.execute_query(
            "SELECT age, coppa_verified FROM child_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        if rows:
            r0 = rows[0]
            _age = r0["age"] if isinstance(r0, dict) else r0[0]
            _verified = r0["coppa_verified"] if isinstance(r0, dict) else r0[1]
            if _age is not None and _age < 13 and not _verified:
                logger.info(
                    "COPPA gate blocked under-13 profile %s (consent not verified)",
                    profile_id,
                )
                msg = (
                    "A parent needs to confirm permission before this account can "
                    "chat. Please ask a parent to complete the consent step in "
                    "Settings."
                )
                return JSONResponse(content=_ollama_block_response(model, msg))
    except Exception as exc:  # never break chat on a COPPA-lookup error
        logger.debug("COPPA gate lookup failed for %s (allowing): %s", profile_id, exc)

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
        _record_safety_incident(profile_id, result, text)
        return JSONResponse(content=_ollama_block_response(model, block_message))

    # Safe — forward to Ollama
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    if stream:
        try:
            collected: list[bytes] = []
            async for chunk in _stream_chunks_from_ollama(body_bytes, fwd_headers):
                collected.append(chunk)
        except httpx.ConnectError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Ollama backend unreachable"},
            )

        # Output safety pipeline runs on the full assembled assistant message.
        # Fail-closed: any unsafe content replaces the stream with a single
        # safe-fallback NDJSON chunk so harmful text never reaches the child.
        assistant_text = _extract_text_from_ndjson_chunks(collected)
        try:
            from safety.pipeline import safety_pipeline

            out_result = safety_pipeline.check_output(
                text=assistant_text, age=age, profile_id=profile_id
            )
        except Exception as exc:
            logger.error(
                "check_output raised on streaming path: %s", exc, exc_info=True
            )
            block_msg = "I'm unable to process that request right now."
            return Response(
                content=_ollama_block_stream_bytes(model, block_msg),
                media_type="application/x-ndjson",
            )

        if not out_result.is_safe:
            block_msg = (
                out_result.modified_content
                or safety_pipeline.get_safe_response(out_result)
                or "I'm not able to share that. Let's try something else!"
            )
            logger.info(
                "Output safety blocked streamed response for profile %s (category=%s)",
                profile_id,
                out_result.category,
            )
            _record_safety_incident(profile_id, out_result, assistant_text)
            return Response(
                content=_ollama_block_stream_bytes(model, block_msg),
                media_type="application/x-ndjson",
            )

        return Response(
            content=b"".join(collected),
            media_type="application/x-ndjson",
        )

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

    # Output safety pipeline on the non-streaming response.
    try:
        upstream_json = upstream.json()
    except (ValueError, _json.JSONDecodeError):
        upstream_json = None

    if isinstance(upstream_json, dict):
        msg = upstream_json.get("message")
        assistant_text = msg.get("content", "") if isinstance(msg, dict) else ""
        try:
            from safety.pipeline import safety_pipeline

            out_result = safety_pipeline.check_output(
                text=assistant_text, age=age, profile_id=profile_id
            )
        except Exception as exc:
            logger.error(
                "check_output raised on non-streaming path: %s", exc, exc_info=True
            )
            block_msg = "I'm unable to process that request right now."
            return JSONResponse(content=_ollama_block_response(model, block_msg))

        if not out_result.is_safe:
            block_msg = (
                out_result.modified_content
                or safety_pipeline.get_safe_response(out_result)
                or "I'm not able to share that. Let's try something else!"
            )
            logger.info(
                "Output safety blocked response for profile %s (category=%s)",
                profile_id,
                out_result.category,
            )
            _record_safety_incident(profile_id, out_result, assistant_text)
            return JSONResponse(content=_ollama_block_response(model, block_msg))

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
    """GET /api/tags — admins see every model; students only the tutor model.

    Filtering the backbone/rollback/backup variants out for students happens
    here at the proxy, so the guarantee holds regardless of Open WebUI's own
    model-access config and survives a fresh open-webui-data volume. Fails
    closed: a request without an admin role header is treated as a student.
    """
    response = await _proxy_to_ollama(request, "/api/tags")
    _, role = _get_user_from_headers(request)
    if role == "admin" or response.status_code != 200:
        return response
    return Response(
        content=_filter_tags_for_students(response.body),
        status_code=response.status_code,
        media_type=response.media_type or "application/json",
    )


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


# ---------------------------------------------------------------------------
# Proxy health check — verifies round-trip to Ollama
# ---------------------------------------------------------------------------


@router.get("/health")
async def proxy_health() -> JSONResponse:
    """Verify the proxy can reach the Ollama backend."""
    try:
        resp = await _forward_request("GET", "/api/version")
        ollama_version = resp.json() if resp.status_code == 200 else None
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "detail": "Ollama backend unreachable",
                "target": system_config.OLLAMA_PROXY_TARGET,
            },
        )
    return JSONResponse(
        content={
            "status": "healthy",
            "ollama": ollama_version,
            "target": system_config.OLLAMA_PROXY_TARGET,
        },
    )
