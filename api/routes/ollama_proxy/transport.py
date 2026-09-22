"""Transport helpers: forward requests to Ollama and stream responses."""

from __future__ import annotations

import json as _json

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from config import system_config
from core import gpu_placement, serving_plan
from core.inference import client as inference_client
from utils.circuit_breaker import ollama_circuit
from utils.logger import get_logger

logger = get_logger(__name__)

_OLLAMA_READ_TIMEOUT = 300.0  # seconds — matches OLLAMA_TIMEOUT default


def _inject_context_length(path: str, content):
    """Serve the context window the serving plan certifies.

    The sealed tutoring run was measured at num_ctx 16384. The Modelfile pins
    8192, and nothing in the request path set it, so a deployment would quietly
    serve a DIFFERENT configuration than the one that passed -- and at 8192 the
    7,696-token system prompt leaves ~500 tokens, which is how enforcement
    rewrites came to end mid-sentence (done_reason=length).

    Fail-open: any problem leaves the body untouched.
    """
    if path not in ("/api/chat", "/api/generate") or not content:
        return content
    try:
        plan = serving_plan.get_plan()
        if not plan.tutoring_enabled or plan.num_ctx <= 0:
            return content
        body = _json.loads(content)
        if not isinstance(body, dict):
            return content
        opts = body.get("options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        # An explicit per-request value wins: the eval harnesses set their own.
        opts.setdefault("num_ctx", plan.num_ctx)
        body["options"] = opts
        return _json.dumps(body).encode()
    except Exception as exc:  # noqa: BLE001 - never fail a child's turn over this
        logger.warning("transport: context-length injection skipped (%s)", exc)
        return content


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


# Ollama's 500 body when a model cannot be placed on the GPU. Two daemons share
# this card (snflwr's, container-internal, and a host-level co-tenant), and
# NEITHER can see the other's models in /api/ps -- so fit cannot be predicted,
# only detected.
_GPU_OOM_MARKERS = (
    "cudaMalloc failed",
    "out of memory",
    "unable to allocate",
    "failed to allocate",
    "llama-server process has terminated",
)


def _looks_like_gpu_oom(resp: httpx.Response) -> bool:
    """True when a 500 is the card being full rather than a real backend fault."""
    if resp.status_code != 500:
        return False
    try:
        body = resp.text[:2000].lower()
    except Exception:  # noqa: BLE001 - a body we cannot read is not a known OOM
        return False
    return any(m.lower() in body for m in _GPU_OOM_MARKERS)


def _force_cpu(content):
    """Rewrite an outgoing body to pin ``num_gpu`` to 0."""
    try:
        body = _json.loads(content)
        if not isinstance(body, dict):
            return content
        opts = body.get("options")
        body["options"] = {**(opts if isinstance(opts, dict) else {}), "num_gpu": 0}
        return _json.dumps(body).encode()
    except Exception:  # noqa: BLE001
        return content


def _engine_is_vllm() -> bool:
    """True when the serving plan puts an OpenAI-protocol engine behind us.

    Kept as a function (not a module constant) so a re-detect takes effect and
    so tests can substitute the plan. Fail-safe: any error means "carry on with
    Ollama", which is the path every existing test and the sealed tutoring
    result were measured on.
    """
    try:
        return serving_plan.get_plan().engine == "vllm"
    except Exception as exc:  # noqa: BLE001
        logger.warning("transport: serving plan unavailable (%s); using ollama", exc)
        return False


def _upstream() -> tuple[str, dict]:
    """(base_url, extra headers) for this hop -- the ONE place that decides.

    Until now all three request builders in this module pasted
    ``system_config.OLLAMA_PROXY_TARGET`` inline, so remote inference was
    architecturally complete and physically impossible: `RemoteDriver` verified
    a server that no child's turn could reach, and the proxy forwarded to the
    local Ollama regardless of what the plan said.

    EVERY path routes remotely when the plan is remote, not just /api/chat. The
    remote runs snflwr's own API, and a thin client has no local model -- so
    keeping /api/tags or /api/show local would serve an empty model list to the
    UI while chat worked, which is a confusing half-configured state.

    The target now comes from the PLAN, which is the object that verified it --
    its TLS, its credential, and that the (engine, model, num_ctx) triple it
    advertises has a sealed tutoring run. Reading the URL from the environment
    here instead would let traffic go somewhere the plan never checked, which is
    the guard-one-thing-operate-on-another shape this wiring exists to remove.

    FAIL-CLOSED ON REMOTE, and this is the important part. A thin client with no
    usable GPU has no local model to fall back to, so silently forwarding to
    ``localhost:11434`` would turn "the tutor server is down" into "the tutor
    answered oddly". When the plan is remote and not serving, this raises, and
    every caller in this module already maps a transport error to a graceful
    503. An honest outage beats an unmeasured tutor.

    Non-remote engines are untouched: local Ollama and vLLM keep the exact path
    the sealed run was measured on.
    """
    try:
        plan = serving_plan.get_plan()
    except Exception as exc:  # noqa: BLE001 - never let planning break a turn
        logger.warning("transport: serving plan unavailable (%s); using ollama", exc)
        return system_config.OLLAMA_PROXY_TARGET.rstrip("/"), {}

    if plan.engine != "remote":
        return system_config.OLLAMA_PROXY_TARGET.rstrip("/"), {}

    base = str((plan.engine_args or {}).get("base_url") or "").rstrip("/")
    if not plan.tutoring_enabled or not base:
        # `remote_reachable` distinguishes "could not ask" from "asked and the
        # answer was not certified"; both mean do not serve, and the plan's
        # own `reason` already says which, so it is logged verbatim rather
        # than re-derived here.
        raise httpx.ConnectError(
            f"remote tutor server is not serving a certified backbone: {plan.reason}"
        )
    # The token is read HERE and never stored on the plan. A plan object gets
    # logged, returned by /health and compared in tests; a bearer token on it
    # would leak through all three. The URL is safe to carry, the credential is
    # not.
    token = serving_plan.remote_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return base, headers


async def _forward_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Send *method* + *path* to the real Ollama backend and return the raw response.

    Gated by ``ollama_circuit``: when the backend has been failing, ``can_execute``
    fast-fails (raising ``ConnectError``, which every caller already maps to a
    graceful 503) instead of hanging on a dead backend. Each completed round-trip
    records success and each transport error records failure, so the breaker
    actually reflects backend health (and self-heals via its half-open probe).
    """
    # /api/chat is the only path with an OpenAI equivalent; /api/tags, /api/show
    # and the rest stay on Ollama, which is also where model metadata lives.
    if path == "/api/chat" and "content" in kwargs and _engine_is_vllm():
        engine_client = inference_client.get_client()
        payload = await engine_client.chat_ollama_bytes(
            kwargs["content"], timeout_s=_OLLAMA_READ_TIMEOUT
        )
        return httpx.Response(
            200, content=payload, headers={"content-type": "application/json"}
        )

    if not ollama_circuit.can_execute():
        raise httpx.ConnectError("Ollama circuit breaker open")
    if "content" in kwargs:
        kwargs["content"] = _inject_gpu_placement(
            path, _inject_context_length(path, kwargs["content"])
        )
    _base, _auth = _upstream()
    url = f"{_base}{path}"
    if _auth:
        kwargs["headers"] = {**(kwargs.get("headers") or {}), **_auth}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT)
        ) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        ollama_circuit.record_failure(exc)
        raise
    ollama_circuit.record_success()

    # The card is shared with a co-tenant ollama daemon that this one cannot see.
    # When the co-tenant holds the GPU, a load here dies with
    #   cudaMalloc failed: out of memory
    # and ollama answers 500. Measured 2026-09-14: the child then received a
    # canned "I need to rephrase my response", because the safety classifier --
    # correctly failing closed on an empty body -- had nothing to check.
    #
    # Fit cannot be PREDICTED (neither daemon's /api/ps shows the other's
    # models), so it is DETECTED: retry once pinned to the CPU. A slower answer
    # beats no answer, and this is the same trade already made for the safety
    # classifier.
    if _looks_like_gpu_oom(resp) and "content" in kwargs:
        logger.warning("transport: GPU out of memory for %s; retrying on CPU", path)
        retry_kwargs = dict(kwargs)
        retry_kwargs["content"] = _force_cpu(kwargs["content"])
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT)
            ) as client:
                resp = await client.request(method, url, **retry_kwargs)
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

    if _engine_is_vllm():
        engine_client = inference_client.get_client()
        async for line in engine_client.stream_ollama_ndjson(
            body, timeout_s=_OLLAMA_READ_TIMEOUT
        ):
            yield line
        return

    if not ollama_circuit.can_execute():
        raise httpx.ConnectError("Ollama circuit breaker open")
    _base, _auth = _upstream()
    url = f"{_base}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    # STUDENT path. Same placement injection as _forward_request; this helper
    # builds its own request so it bypasses that choke point. This is the one
    # that decides whether a child waits ~2 s or ~14 s for an answer.
    req = client.build_request(
        "POST",
        url,
        content=_inject_gpu_placement(
            "/api/chat", _inject_context_length("/api/chat", body)
        ),
        headers=headers,
    )
    try:
        resp = await client.send(req, stream=True)
    except httpx.TransportError as exc:
        await client.aclose()
        ollama_circuit.record_failure(exc)
        raise

    # Same GPU-OOM fallback as _forward_request, repeated HERE because this
    # helper builds its own request and so bypasses that choke point -- which is
    # exactly how the first version of this fix missed the STUDENT path and
    # covered only the one children do not use.
    #
    # A 500 arrives before any chunk, so the retry is clean: nothing has been
    # yielded yet and the caller cannot tell the difference.
    if resp.status_code == 500:
        try:
            await resp.aread()
        except Exception:  # noqa: BLE001 - an unreadable body is not a known OOM
            pass
        if _looks_like_gpu_oom(resp):
            logger.warning(
                "transport: GPU out of memory on the stream; retrying on CPU"
            )
            await resp.aclose()
            retry_req = client.build_request(
                "POST", url, content=_force_cpu(body), headers=headers
            )
            try:
                resp = await client.send(retry_req, stream=True)
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
    _base, _auth = _upstream()
    url = f"{_base}/api/chat"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT))
    try:
        # Same placement injection as _forward_request. These streaming helpers
        # build their own request, so they bypass that choke point entirely —
        # and _stream_chunks_from_ollama is the STUDENT path, the one that
        # actually matters for tutor latency.
        req = client.build_request(
            "POST",
            url,
            content=_inject_gpu_placement(
                "/api/chat", _inject_context_length("/api/chat", body)
            ),
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
