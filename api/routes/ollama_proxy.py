"""
Ollama-compatible pass-through proxy.

OWU's OLLAMA_BASE_URL points at snflwr-api.  Non-chat Ollama API calls
(tags, show, generate, embed, pull, etc.) are forwarded unchanged to the
real Ollama backend configured in system_config.OLLAMA_PROXY_TARGET.
"""

import asyncio
import json as _json
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from api.middleware.auth import get_current_session, is_genuine_admin
from config import system_config
from core.authentication import AuthSession
from core.coppa_gate import coppa_consent_block_reason
from utils import observability
from utils.circuit_breaker import ollama_circuit
from utils.logger import get_logger
from utils.rate_limiter import rate_limiter

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


def _admin_only(request: Request, session: AuthSession) -> Optional[Response]:
    """Gate an endpoint to a genuine admin session. Returns 403 otherwise, else None.

    Used for the raw inference (``/api/generate``, ``/api/embed*``) and
    model-management (``/api/pull|delete|copy``) endpoints, which are NOT part of
    the student flow — Open WebUI drives all user-facing generation through
    ``/api/chat`` (which runs the safety pipeline). Authority requires a genuine
    admin *session*: the internal service key (Open WebUI) is a relay, not an
    admin, so it cannot reach these even by forwarding X-OpenWebUI-User-Role:
    admin. Without this gate a leaked key could reach raw, unfiltered model
    output or mutate the model set.
    """
    if not is_genuine_admin(session):
        logger.info(
            "Blocked non-admin access to %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "This endpoint is restricted to administrators."},
        )
    return None


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


# Fields in an Ollama /api/show response that expose the tutor's SYSTEM/safety
# prompt (and the template / sampling config that frame it). Stripped for
# non-admins so a child cannot read — and then attempt to evade — the safety
# instructions embedded in the model's Modelfile.
_SHOW_SENSITIVE_FIELDS = ("modelfile", "system", "template", "parameters")


def _filter_show_for_students(payload: bytes) -> bytes:
    """Drop the prompt-bearing fields from an Ollama ``/api/show`` response body.

    Keeps non-sensitive metadata (details, model_info, capabilities) that Open
    WebUI needs for the model dropdown. Returns the payload unchanged if it can't
    be parsed, so a malformed upstream response never breaks the model-info call.
    """
    try:
        data = _json.loads(payload)
    except (ValueError, _json.JSONDecodeError):
        return payload
    if not isinstance(data, dict):
        return payload
    for field in _SHOW_SENSITIVE_FIELDS:
        data.pop(field, None)
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


# Hold-back streaming: how much text to accumulate before the FIRST output-safety
# check. A sentence boundary triggers it sooner (fast first flush); the char cap
# guarantees a long unbroken stream still gets vetted promptly.
_FIRST_CHECKPOINT_CHARS = 160


def _first_checkpoint_ready(text: str) -> bool:
    """True once enough answer text has accumulated to run the first check_output:
    a sentence boundary (after a little content) or the char cap."""
    if len(text) >= _FIRST_CHECKPOINT_CHARS:
        return True
    return len(text) >= 12 and any(p in text for p in ".!?")


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

    # Resolve IDENTITY (which user) — the X-OpenWebUI-User-* headers carry the
    # forwarded user only when the caller is Open WebUI (the internal key). This
    # is used purely to look up the right child profile below.
    if session.user_id == "internal_service":
        user_id, _ = _get_user_from_headers(request)
    else:
        user_id = session.user_id

    # Safety-pipeline bypass is an AUTHORITY decision, never an identity one: it
    # requires a genuine admin *session*. The internal service key is a relay,
    # not an admin, so a forwarded X-OpenWebUI-User-Role: admin can NOT skip
    # child safety (parity with chat.py). A leaked key therefore can't bypass.
    if is_genuine_admin(session):
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

    # ---- Admission control (students only; admins returned above) ----
    # One child must not be able to flood the single-GPU backend. Each chat turn
    # is several inferences, so bounce here BEFORE any of that work.
    if system_config.CHAT_RATE_LIMIT_PER_MINUTE > 0:
        allowed, info = rate_limiter.check_rate_limit(
            identifier=user_id or "unknown",
            max_requests=system_config.CHAT_RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
            limit_type="chat",
        )
        if not allowed:
            logger.info(
                "Chat rate limit hit for %s (retry_after=%ss)",
                user_id,
                info.get("retry_after"),
            )
            slow_msg = (
                "You're sending messages a little too fast — take a breath and "
                "try again in a moment. 🌻"
            )
            return JSONResponse(content=_ollama_block_response(model, slow_msg))

    # If the Ollama backend is unhealthy, fail fast instead of piling onto a
    # struggling GPU. The shared circuit is tripped by the safety classifier's
    # own llama-guard calls (OllamaClient -> ollama_circuit), so this reflects
    # real backend health.
    if ollama_circuit.is_open:
        logger.warning(
            "Ollama circuit OPEN — fast-failing student chat for %s", user_id
        )
        busy_msg = (
            "The tutor is taking a quick break and will be back in a moment. "
            "Please try again shortly. 🌻"
        )
        return JSONResponse(content=_ollama_block_response(model, busy_msg))

    # License gate — students must hold a valid subscription/trial token.
    # Fail-safe: any licensing problem => gated, never a crash. Admins already
    # returned above and are never gated. (system_config is imported at module top.)
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

    # Observation-only tracing context. Metadata only — never any chat content.
    # Fail-safe: a tracing error must never change the response.
    _t0 = time.perf_counter()
    _trace = {
        "model": model,
        "age_band": observability.age_band(age),
        "profile_hash": observability.hash_profile(profile_id),
        "blocked": True,
        "safety": {},
        "latency_ms": {},
        "tokens": None,
    }

    def _emit_trace():
        _trace["latency_ms"]["total"] = round((time.perf_counter() - _t0) * 1000, 2)
        try:
            observability.trace_chat_turn(**_trace)
        except Exception:  # belt-and-suspenders; wrapper is already fail-safe
            pass

    def _usage_from(payload):
        if not isinstance(payload, dict):
            return None
        pin = payload.get("prompt_eval_count")
        out = payload.get("eval_count")
        if pin is None and out is None:
            return None
        return {"input": pin or 0, "output": out or 0}

    # COPPA gate — an under-13 profile may only tutor once per-child parental
    # consent has been verified. Shared with the native chat route via
    # core.coppa_gate so the two model-reaching paths can't drift (finding C1);
    # fail-closed semantics live in the helper.
    coppa_msg = coppa_consent_block_reason(profile_id, fallback_age=age)
    if coppa_msg is not None:
        _trace["safety"] = {"blocked_layer": "coppa"}
        _emit_trace()
        return JSONResponse(content=_ollama_block_response(model, coppa_msg))

    text = _extract_last_user_message(messages)
    # Captured separately because `text` is shadowed inside the streaming _vet()
    # closure; passed as `context` to check_output so the answer inherits the
    # question's educational context (e.g. a biology question about "drugs").
    user_question = text

    try:
        from safety.pipeline import safety_pipeline

        result = safety_pipeline.check_input(text=text, age=age, profile_id=profile_id)
    except Exception as exc:
        logger.error("Safety pipeline raised unexpectedly: %s", exc, exc_info=True)
        # Fail closed — block the message
        block_msg = "I'm unable to process that request right now."
        _trace["safety"] = {"blocked_layer": "input"}
        _emit_trace()
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
        _trace["safety"] = {
            "category": str(result.category),
            "severity": str(result.severity),
            "blocked_layer": "input",
        }
        _emit_trace()
        return JSONResponse(content=_ollama_block_response(model, block_message))

    # Safe — forward to Ollama
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    if stream and system_config.CHAT_STREAMING_ENABLED:
        # Hold-back streaming: flush each part only AFTER check_output has vetted
        # the text-so-far, so the child never receives an un-vetted token. Two
        # checkpoints (first sentence, then the remainder at stream end) keep it
        # to ~+1 output-classifier call vs the buffered path. Off by default
        # (system_config.CHAT_STREAMING_ENABLED) — enabled only where the GPU has
        # headroom; the buffered path below is the single-GPU default.
        from safety.pipeline import safety_pipeline

        async def _vet(text: str):
            # check_output is sync (CPU + a blocking classifier call) — run it off
            # the event loop so it doesn't stall other concurrent requests.
            return await asyncio.to_thread(
                safety_pipeline.check_output,
                text=text,
                age=age,
                profile_id=profile_id,
                context=user_question,
            )

        def _fallback_for(out_result) -> str:
            return (
                out_result.modified_content
                or safety_pipeline.get_safe_response(out_result)
                or "I'm not able to share that. Let's try something else!"
            )

        def _emit_block(out_result, text) -> None:
            _record_safety_incident(profile_id, out_result, text)
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()

        async def _holdback_stream():
            collected: list[bytes] = []
            flushed = 0
            checkpoint_done = False
            try:
                async for chunk in _stream_chunks_from_ollama(body_bytes, fwd_headers):
                    collected.append(chunk)
                    if checkpoint_done:
                        continue
                    text = _extract_text_from_ndjson_chunks(collected)
                    if not _first_checkpoint_ready(text):
                        continue
                    res = await _vet(text)
                    if not res.is_safe:
                        # Nothing flushed yet — replace the whole response.
                        _emit_block(res, text)
                        yield _ollama_block_stream_bytes(model, _fallback_for(res))
                        return
                    for c in collected[flushed:]:
                        yield c
                    flushed = len(collected)
                    checkpoint_done = True

                # Stream done — vet the FULL text before flushing the remainder.
                full = _extract_text_from_ndjson_chunks(collected)
                res = await _vet(full)
                if not res.is_safe:
                    # The un-flushed remainder is un-vetted → withhold it and send
                    # a safe fallback for the rest. Already-flushed content passed
                    # the checkpoint check, so no unsafe token ever reached the child.
                    _emit_block(res, full)
                    yield _ollama_block_stream_bytes(model, _fallback_for(res))
                    return
                for c in collected[flushed:]:
                    yield c
                _trace["blocked"] = False
                _trace["safety"] = {"blocked_layer": None}
                _emit_trace()
            except httpx.ConnectError:
                _trace["safety"] = {"blocked_layer": "error"}
                _emit_trace()
                yield _ollama_block_stream_bytes(
                    model, "The tutor is unavailable right now. Please try again."
                )

        return StreamingResponse(_holdback_stream(), media_type="application/x-ndjson")

    if stream:
        try:
            collected: list[bytes] = []
            async for chunk in _stream_chunks_from_ollama(body_bytes, fwd_headers):
                collected.append(chunk)
        except httpx.ConnectError:
            _trace["safety"] = {"blocked_layer": "error"}
            _emit_trace()
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
                text=assistant_text,
                age=age,
                profile_id=profile_id,
                context=user_question,
            )
        except Exception as exc:
            logger.error(
                "check_output raised on streaming path: %s", exc, exc_info=True
            )
            block_msg = "I'm unable to process that request right now."
            _trace["safety"] = {"blocked_layer": "output"}
            _emit_trace()
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
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()
            return Response(
                content=_ollama_block_stream_bytes(model, block_msg),
                media_type="application/x-ndjson",
            )

        _trace["blocked"] = False
        _trace["safety"] = {"blocked_layer": None}
        _emit_trace()
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
        _trace["safety"] = {"blocked_layer": "error"}
        _emit_trace()
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
                text=assistant_text,
                age=age,
                profile_id=profile_id,
                context=user_question,
            )
        except Exception as exc:
            logger.error(
                "check_output raised on non-streaming path: %s", exc, exc_info=True
            )
            block_msg = "I'm unable to process that request right now."
            _trace["safety"] = {"blocked_layer": "output"}
            _emit_trace()
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
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()
            return JSONResponse(content=_ollama_block_response(model, block_msg))

    _trace["blocked"] = False
    _trace["safety"] = {"blocked_layer": None}
    _trace["tokens"] = _usage_from(upstream_json)
    _emit_trace()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# Pass-through endpoints
# ---------------------------------------------------------------------------


@router.get("/tags")
async def proxy_tags(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    """GET /api/tags — genuine admins see every model; everyone else (incl. all
    Open WebUI relay traffic) only the tutor model.

    Filtering the backbone/rollback/backup variants out for students happens
    here at the proxy, so the guarantee holds regardless of Open WebUI's own
    model-access config and survives a fresh open-webui-data volume. Fails
    closed: only a genuine admin *session* sees the full list — the internal
    service key (relay) cannot unlock it with a forwarded admin header.
    """
    response = await _proxy_to_ollama(request, "/api/tags")
    if is_genuine_admin(session) or response.status_code != 200:
        return response
    return Response(
        content=_filter_tags_for_students(response.body),
        status_code=response.status_code,
        media_type=response.media_type or "application/json",
    )


@router.post("/show")
async def proxy_show(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    """POST /api/show — model details. Ollama echoes the full Modelfile here,
    including the tutor's SYSTEM / safety prompt, TEMPLATE, and PARAMETERs.

    A genuine admin *session* gets the response verbatim; everyone else — every
    Open WebUI relay (``internal_service``) and thus every student — gets the
    prompt-bearing fields stripped, keeping only non-sensitive metadata for the
    model dropdown. Mirrors ``proxy_tags``; fails closed (only a genuine admin
    session unlocks the full body, never a forwarded admin header).
    """
    response = await _proxy_to_ollama(request, "/api/show")
    if is_genuine_admin(session) or response.status_code != 200:
        return response
    return Response(
        content=_filter_show_for_students(response.body),
        status_code=response.status_code,
        media_type=response.media_type or "application/json",
    )


@router.post("/generate")
async def proxy_generate(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: raw completion bypasses the /api/chat safety pipeline and would
    # return UNFILTERED model output to a child. Students use /api/chat only.
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/generate")


@router.post("/embed")
async def proxy_embed(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/embed")


@router.post("/embeddings")
async def proxy_embeddings(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/embeddings")


@router.delete("/delete")
async def proxy_delete(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: mutates the installed model set (destructive).
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/delete")


@router.post("/pull")
async def proxy_pull(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: could introduce an unvetted (uncensored) model.
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/pull")


@router.post("/copy")
async def proxy_copy(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: mutates the installed model set.
    blocked = _admin_only(request, session)
    if blocked is not None:
        return blocked
    return await _proxy_to_ollama(request, "/api/copy")


@router.get("/version")
async def proxy_version(request: Request) -> Response:
    # Session-gated (router dependency) but intentionally NOT admin-gated: Open
    # WebUI calls /api/version as the relay (non-admin) for its Ollama-connection
    # health check, and the body is just the Ollama version string — non-sensitive,
    # unlike /api/show (which leaks the Modelfile). Admin-gating it would break
    # OWUI's connection indicator for no security gain.
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
