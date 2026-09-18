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
import os

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from core import serving_plan
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
