"""Chat route: POST /api/chat with the full child-safety pipeline."""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from api.middleware.auth import get_current_session, is_genuine_admin
from api.routes.ollama_proxy import access, blocks, guards, profile, transport
from config import system_config
from core.authentication import AuthSession
from core.coppa_gate import coppa_consent_block_reason
from core.profile_gate import no_profile_block_reason
from utils import observability
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()


def _gate_block(model: str, message: str, *, stream: bool) -> Response:
    """Return an early gate/block response in the format the client requested.

    Open WebUI >=0.10 requests ``/api/chat`` with ``stream=True`` and does not
    render a non-streaming ``JSONResponse`` (single object) — the child sees a
    blank bubble instead of the safe-redirect / 988 text. When a stream was
    requested, deliver the block as a single NDJSON chunk; otherwise a JSON
    object. (The later streaming paths already emit NDJSON; this covers the
    early gates: rate-limit, circuit, license, no-profile, COPPA, input-safety.)
    """
    if stream:
        return Response(
            content=blocks._ollama_block_stream_bytes(model, message),
            media_type="application/x-ndjson",
        )
    return JSONResponse(content=blocks._ollama_block_response(model, message))


async def _pedagogy_reissue(
    original_body_bytes: bytes,
    fwd_headers: dict,
    assistant_text: str,
    nudge: str,
    model: str,
) -> str:
    """Re-issue the chat with the assistant's response + nudge appended.

    Called by the pedagogy enforcer's ``_regenerate`` closure. Fail-open:
    callers (inside ``enforce_guidance``) catch all exceptions from this helper.
    """
    body = _json.loads(original_body_bytes)
    messages = list(body.get("messages", []))
    # Append the revealing assistant turn so the model has full context,
    # then the nudge so it knows to guide rather than give the answer.
    messages.append({"role": "assistant", "content": assistant_text})
    messages.append({"role": "user", "content": nudge})
    payload = _json.dumps(
        {"model": model, "messages": messages, "stream": False, "think": False}
    ).encode()
    upstream = await transport._forward_request(
        "POST", "/api/chat", content=payload, headers=fwd_headers
    )
    return upstream.json().get("message", {}).get("content", "")


async def _pedagogy_oneshot(prompt: str, model: str, fwd_headers: dict) -> str:
    """Single-turn Ollama call for the pedagogy confirm stage.

    Called by the pedagogy enforcer's ``_confirm_generate`` closure. Fail-open:
    callers (inside ``confirm_reveal``) catch all exceptions from this helper.
    """
    payload = _json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
        }
    ).encode()
    upstream = await transport._forward_request(
        "POST", "/api/chat", content=payload, headers=fwd_headers
    )
    return upstream.json().get("message", {}).get("content", "")


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
        user_id, _ = access._get_user_from_headers(request)
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
            return await transport._stream_chat_from_ollama(body_bytes, fwd_headers)
        try:
            upstream = await transport._forward_request(
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

    # ---- Pin the model (students only; admins returned above) ----
    # A student may only ever run the tutor model. The OWUI dropdown already
    # lists only snflwr.ai (the proxy filters /api/tags), but /api/chat otherwise
    # trusts the request body — a crafted call naming the raw backbone would run
    # it unpinned, losing the tutor Modelfile's system/safety prompt (#190). The
    # safety pipeline still runs regardless, so this is defense-in-depth. Coerce
    # any out-of-set model to the default tutor before any downstream use.
    if model not in access._student_visible_models():
        pinned = system_config.OLLAMA_DEFAULT_MODEL or "snflwr.ai"
        if model:
            logger.info(
                "Pinned student model %r -> %r", sanitize_log_value(model), pinned
            )
        model = pinned
        body["model"] = pinned
        body_bytes = _json.dumps(body).encode()

    # ---- Admission control (students only; admins returned above) ----
    _reason = (
        guards.rate_limit_block_reason(user_id)
        or guards.circuit_block_reason(user_id)
        or guards.license_block_reason(user_id)
    )
    if _reason:
        return _gate_block(model, _reason, stream=stream)

    # Student path — run safety pipeline
    profile_id = await profile._get_profile_for_user(user_id)

    no_profile_msg = no_profile_block_reason(profile_id)
    if no_profile_msg:
        logger.warning(
            "Blocked student chat: no learning profile for user %s",
            observability.hash_profile(user_id) if user_id else "unknown",
        )
        return _gate_block(model, no_profile_msg, stream=stream)

    # Resolve age from profile (best-effort; None is acceptable)
    age: Optional[int] = profile._resolve_age(profile_id)

    # Observation-only tracing context. Metadata only — never any chat content.
    # Fail-safe: a tracing error must never change the response.
    _t0 = time.perf_counter()
    _trace: Dict[str, Any] = {
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
        return _gate_block(model, coppa_msg, stream=stream)

    text = blocks._extract_last_user_message(messages)
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
        return _gate_block(model, block_msg, stream=stream)

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
        blocks._record_safety_incident(profile_id, result, text)
        _trace["safety"] = {
            "category": str(result.category),
            "severity": str(result.severity),
            "blocked_layer": "input",
        }
        _emit_trace()
        return _gate_block(model, block_message, stream=stream)

    # Safe — forward to Ollama
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    # TODO(pedagogy): force-buffer homework turns when stream=True so the
    # enforcer can run — requires adding the hook to the buffered-stream path too.
    # Deferred: home deployment uses stream=False; only a small fraction of student
    # turns are homework pushes. Implement after the non-streaming path is validated.
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
            blocks._record_safety_incident(profile_id, out_result, text)
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
                async for chunk in transport._stream_chunks_from_ollama(
                    body_bytes, fwd_headers
                ):
                    collected.append(chunk)
                    if checkpoint_done:
                        continue
                    text = blocks._extract_text_from_ndjson_chunks(collected)
                    if not blocks._first_checkpoint_ready(text):
                        continue
                    res = await _vet(text)
                    if not res.is_safe:
                        # Nothing flushed yet — replace the whole response.
                        _emit_block(res, text)
                        yield blocks._ollama_block_stream_bytes(
                            model, _fallback_for(res)
                        )
                        return
                    for c in collected[flushed:]:
                        yield c
                    flushed = len(collected)
                    checkpoint_done = True

                # Stream done — vet the FULL text before flushing the remainder.
                full = blocks._extract_text_from_ndjson_chunks(collected)
                res = await _vet(full)
                if not res.is_safe:
                    # The un-flushed remainder is un-vetted → withhold it and send
                    # a safe fallback for the rest. Already-flushed content passed
                    # the checkpoint check, so no unsafe token ever reached the child.
                    _emit_block(res, full)
                    yield blocks._ollama_block_stream_bytes(model, _fallback_for(res))
                    return
                for c in collected[flushed:]:
                    yield c
                _trace["blocked"] = False
                _trace["safety"] = {"blocked_layer": None}
                _emit_trace()
            except httpx.ConnectError:
                _trace["safety"] = {"blocked_layer": "error"}
                _emit_trace()
                yield blocks._ollama_block_stream_bytes(
                    model, "The tutor is unavailable right now. Please try again."
                )

        return StreamingResponse(_holdback_stream(), media_type="application/x-ndjson")

    if stream:
        try:
            collected: list[bytes] = []
            async for chunk in transport._stream_chunks_from_ollama(
                body_bytes, fwd_headers
            ):
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
        assistant_text = blocks._extract_text_from_ndjson_chunks(collected)
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
                content=blocks._ollama_block_stream_bytes(model, block_msg),
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
            blocks._record_safety_incident(profile_id, out_result, assistant_text)
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()
            return Response(
                content=blocks._ollama_block_stream_bytes(model, block_msg),
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
        upstream = await transport._forward_request(
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

    # Bound unconditionally so the response-serialization branch below can read it
    # on every path (incl. non-dict upstream_json). Set True only if the pedagogy
    # enforcer rewrote the content.
    _pedagogy_modified = False
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
            return JSONResponse(content=blocks._ollama_block_response(model, block_msg))

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
            blocks._record_safety_incident(profile_id, out_result, assistant_text)
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()
            return JSONResponse(content=blocks._ollama_block_response(model, block_msg))

        # ---- Pedagogy post-processor (fail-OPEN, homework-integrity only) --------
        # Runs only on this buffered non-streaming path; gated off by default.
        # Never blocks a turn: the outer except logs and serves the original text.
        if system_config.GUIDANCE_ENFORCEMENT_ENABLED:
            try:
                from core.pedagogy import enforce_guidance

                async def _regenerate(nudge: str) -> str:
                    return await _pedagogy_reissue(
                        body_bytes, fwd_headers, assistant_text, nudge, model
                    )

                async def _confirm_generate(prompt: str) -> str:
                    return await _pedagogy_oneshot(
                        prompt,
                        system_config.GUIDANCE_ENFORCER_CONFIRM_MODEL or model,
                        fwd_headers,
                    )

                new_text, meta = await enforce_guidance(
                    user_question,
                    assistant_text,
                    _regenerate,
                    confirm_generate=_confirm_generate,
                )
                if new_text != assistant_text and isinstance(
                    upstream_json.get("message"), dict
                ):
                    upstream_json["message"]["content"] = new_text
                    assistant_text = new_text  # noqa: F841 — kept for closure clarity
                    _pedagogy_modified = True
                _trace["pedagogy"] = {"action": meta.action}
            except Exception as exc:  # fail-open: never let pedagogy break a turn
                logger.warning("guidance enforcer errored (fail-open): %s", exc)

    _trace["blocked"] = False
    _trace["safety"] = {"blocked_layer": None}
    _trace["tokens"] = _usage_from(upstream_json)
    _emit_trace()
    # Strip the model's reasoning field before returning: OWUI >=0.10 mishandles
    # `message.thinking` (blank render) and the raw chain-of-thought is unvetted
    # content that must never reach a child. `content` is untouched. (Streaming
    # paths strip per-line in transport._stream_chunks_from_ollama.)
    out_content = upstream.content
    if isinstance(upstream_json, dict) and isinstance(
        upstream_json.get("message"), dict
    ):
        msg = upstream_json["message"]
        # Re-serialize when the enforcer rewrote the content OR when we need to
        # strip the model's reasoning field (OWUI >=0.10 blank-renders it and
        # chain-of-thought must never reach a child unvetted).
        if "thinking" in msg or _pedagogy_modified:
            msg.pop("thinking", None)
            out_content = _json.dumps(upstream_json).encode()
    return Response(
        content=out_content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
