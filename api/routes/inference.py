"""Tutor-server side of remote inference mode.

Runs on the machine WITH the certified GPU, and serves boxes that have none.
Its only job is to answer one question honestly: what is this server actually
serving? The client takes that answer and checks it against its own certified
table before it will send a child's turn here -- see
`core.serving_plan._remote_plan`.

WHY A SEPARATE CREDENTIAL
-------------------------
`INTERNAL_API_KEY` authenticates as `internal_service` with role `admin`. A thin
client in a classroom is not an admin of the tutor server, so remote inference
gets its own token (`INFERENCE_SERVER_TOKEN`) with exactly one privilege: ask
for the plan and send turns. A server with no such token set is simply not a
tutor server, and says so with 404 rather than advertising a capability it has
not been configured for.

WHY THE PLAN ENDPOINT IS AUTHENTICATED
--------------------------------------
Unlike `/api/thin-client/manifest`, which is deliberately open because it hands
out a launcher URL and a welcome message, this endpoint describes the
deployment: engine, model, context window, capacity. An unauthenticated caller
learns nothing about this machine.
"""

from __future__ import annotations

import hmac
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from api.routes.ollama_proxy import admission
from core import serving_plan
from core.inference import client as inference_client
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


class RemotePlanResponse(BaseModel):
    """What this server serves. Consumed by `RemoteDriver.fetch_plan`."""

    engine: str
    model: str
    num_ctx: int
    quality_tier: str
    max_concurrent: int
    sealed_on: str = ""


def _server_token() -> str:
    """The credential a remote client must present. Never logged."""
    return os.getenv("INFERENCE_SERVER_TOKEN", "").strip()


def require_inference_client(authorization: str = Header(None)) -> None:
    """Admit a remote tutor client, or refuse without leaking why."""
    expected = _server_token()
    if not expected:
        # Not configured as a tutor server. Do not advertise the endpoint.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization.split(" ", 1)[1]
    # Constant-time: a token check that returns early leaks the token a byte at
    # a time to anyone who can measure the response.
    if not hmac.compare_digest(presented, expected):
        logger.warning("remote inference: rejected a client credential")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/plan", response_model=RemotePlanResponse)
async def get_inference_plan(
    authorization: str = Header(None),
) -> RemotePlanResponse:
    """Advertise what this server serves, for a client to verify."""
    require_inference_client(authorization)
    plan = serving_plan.get_plan()

    # A server that cannot tutor locally must not offer to tutor remotely.
    # Reporting its real tier lets the client refuse for a readable reason
    # instead of discovering the problem in a child's reply.
    sealed_on = ""
    if plan.tutor_model:
        match = next(
            (
                e
                for e in serving_plan.CERTIFIED_BACKBONES
                if e.engine == plan.engine and e.model == plan.tutor_model
            ),
            None,
        )
        sealed_on = match.sealed_on if match else ""

    return RemotePlanResponse(
        engine=plan.engine,
        model=plan.tutor_model or "",
        num_ctx=plan.num_ctx,
        quality_tier=plan.quality_tier,
        max_concurrent=plan.max_concurrent_requests,
        sealed_on=sealed_on,
    )


# --------------------------------------------------------------------------
# The turn route.
#
# THIS DELIBERATELY BYPASSES THE PEDAGOGY STACK. The client box already ran the
# safety pipeline, the topic gate, COPPA, the history ledger and the guidance
# enforcer before it got here -- that is the whole point of phase 2's split, and
# the reason the student's data never leaves that box. Running any of it again
# here would double-moderate the turn and, worse, would treat the client
# enforcer's own gate / confirm / rewrite sub-calls as fresh student turns.
#
# So this endpoint is an ENGINE, not a tutor: bytes in, bytes out, at the same
# Ollama-shaped contract `core.inference.client` speaks everywhere. What makes
# it safe is not pipeline depth, it is that only a holder of
# INFERENCE_SERVER_TOKEN can reach it.
# --------------------------------------------------------------------------

_TURN_TIMEOUT_S = 600.0


def _require_servable_plan() -> None:
    """A server that cannot tutor locally must not serve turns remotely."""
    plan = serving_plan.get_plan()
    if not plan.tutoring_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"this server is not serving a tutor: {plan.reason}",
        )


@router.post(
    "/chat",
    dependencies=[
        # Auth first: an unauthenticated caller must not consume a slot.
        Depends(require_inference_client),
        # Then capacity. This is the SERVER's admission gate -- the client does
        # not serialize, so this is the only thing standing between a classroom
        # of thin clients and an overloaded card. It is a dependency rather than
        # a block in the handler so that teardown runs after a streamed response
        # has been fully sent, not when the handler returns.
        Depends(admission.inference_slot),
    ],
)
async def serve_turn(request: Request):
    """Serve one Ollama-shaped turn for a remote snflwr client."""
    _require_servable_plan()
    body = await request.body()
    try:
        parsed = json.loads(body)
        stream = (
            bool(parsed.get("stream", False)) if isinstance(parsed, dict) else False
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="body must be JSON"
        ) from None

    engine_client = inference_client.get_client()
    if stream:
        return StreamingResponse(
            engine_client.stream_ollama_ndjson(body, timeout_s=_TURN_TIMEOUT_S),
            media_type="application/x-ndjson",
        )
    payload = await engine_client.chat_ollama_bytes(body, timeout_s=_TURN_TIMEOUT_S)
    return JSONResponse(content=json.loads(payload))
