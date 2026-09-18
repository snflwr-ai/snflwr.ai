"""Remote driver — a snflwr tutor server on another machine serves the turn.

WHAT THIS IS FOR
----------------
Phase 1's quality floor refuses to tutor on hardware that cannot serve a
certified backbone, and points at "a bigger server". This is that server's
client side. A box with no usable GPU keeps everything that makes snflwr safe --
accounts, history, parental controls, the safety pipeline, COPPA handling and
the guidance enforcer all stay local -- and sends only inference turns over the
wire.

WHY IT IS THIN
--------------
The remote runs snflwr's own API, which already speaks the Ollama-shaped
`/api/chat` this app speaks everywhere. So the wire format needs no translation
and this driver delegates to `OllamaDriver`; what it adds is the three things a
network hop needs and a loopback call does not: TLS, a credential, and a way to
ask the far end what it is actually serving.

THE ADVERTISED PLAN IS NOT TRUSTED
----------------------------------
`fetch_plan()` returns what the remote SAYS it serves. The caller
(`core.serving_plan`) checks that (engine, model, num_ctx) against its own
certified table and refuses to tutor if it does not match. That check is
deliberately on this side: a server that has been misconfigured or quietly
downgraded must not be able to hand a child a weaker tutor than the one that
passed the sealed run. The client is the party with an interest in the answer
being true.

WHAT IT CANNOT DO
-----------------
This protects against misconfiguration and silent downgrade, not against a
hostile server -- the remote still does the generating, and a server that lies
about its plan can also lie with its tokens. The mitigation for that is
operational: you run the tutor server, or you choose who does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from core.endpoint_url import validate_endpoint
from core.inference.base import (
    Capabilities,
    ChatChunk,
    ChatRequest,
    ChatResult,
    DriverHealth,
    EngineTimeout,
    EngineUnreachable,
    InferenceError,
)
from core.inference.ollama_driver import OllamaDriver

logger = logging.getLogger(__name__)

PLAN_PATH = "/api/inference/plan"
# The turn route on the tutor server. NOT "/api/chat": on a snflwr box that is
# the session-authenticated student API, not an engine endpoint.
CHAT_PATH = "/api/inference/chat"
HEALTH_PATH = "/api/inference/plan"
PLAN_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class RemotePlan:
    """What the far end says it is serving. Verified by the caller, not here."""

    engine: str
    model: str
    num_ctx: int
    quality_tier: str
    max_concurrent: int
    sealed_on: str = ""


class RemoteDriver:
    """Talks to a snflwr tutor server over the network."""

    name = "remote"

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        client: Optional[httpx.AsyncClient] = None,
    ):
        # A credential and a child's text are about to cross this link, so the
        # off-box TLS rule applies. Raises before anything is sent.
        validate_endpoint(base_url, require_tls_offbox=True)
        self._base_url = base_url.rstrip("/")
        self._token = token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx.AsyncClient(headers=headers)
        # The wire format is identical, so the Ollama driver does the talking --
        # pointed at the tutor server's engine-shaped routes rather than at
        # Ollama's own.
        self._inner = OllamaDriver(
            base_url=self._base_url,
            client=self._client,
            chat_path=CHAT_PATH,
            health_path=HEALTH_PATH,
        )

    @property
    def has_token(self) -> bool:
        """Whether a credential was supplied. NEVER exposes the value itself."""
        return bool(self._token)

    async def fetch_plan(self) -> RemotePlan:
        """Ask the remote what it serves. Unverified -- the caller judges it.

        `core.serving_plan._fetch_remote_plan` does the same fetch synchronously,
        because `compute_plan()` runs at startup outside an event loop. The two
        parse the same payload and must stay in step; if a third caller appears,
        that is the moment to extract one parser rather than keep a third copy.
        """
        try:
            resp = await self._client.get(
                f"{self._base_url}{PLAN_PATH}", timeout=PLAN_TIMEOUT_S
            )
        except httpx.TimeoutException as exc:
            raise EngineTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable(str(exc)) from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise InferenceError(
                "remote tutor server rejected our credential "
                f"({resp.status_code}); check INFERENCE_REMOTE_TOKEN is set "
                "and matches the server"
            )
        if resp.status_code >= 400:
            raise InferenceError(
                f"remote plan endpoint returned {resp.status_code}: {resp.text[:200]}"
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise InferenceError("remote plan endpoint did not return an object")
        try:
            return RemotePlan(
                engine=str(payload["engine"]),
                model=str(payload["model"]),
                num_ctx=int(payload["num_ctx"]),
                quality_tier=str(payload["quality_tier"]),
                max_concurrent=int(payload.get("max_concurrent", 1)),
                sealed_on=str(payload.get("sealed_on", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceError(f"remote plan is not readable: {exc}") from exc

    async def chat(self, req: ChatRequest, *, timeout_s: float) -> ChatResult:
        return await self._inner.chat(req, timeout_s=timeout_s)

    def stream(self, req: ChatRequest, *, timeout_s: float) -> AsyncIterator[ChatChunk]:
        return self._inner.stream(req, timeout_s=timeout_s)

    async def health(self) -> DriverHealth:
        """Reachability via the plan endpoint -- the tutor server has no
        `/api/tags`, and an authenticated 200 there proves both that the server
        is up and that our credential still works."""
        return await self._inner.health()

    def capabilities(self) -> Capabilities:
        """The remote's engine does the batching; this side only forwards.

        `max_slots=0` describes THIS driver, which imposes no limit of its own.
        The concurrency actually applied lives in the plan: `client.build_client`
        gates remote turns through the local `Admission` at the slot count the
        server advertised, so this box is a good citizen rather than unbounded,
        while the server keeps the final say via 429/503.
        """
        return Capabilities(
            batching=True,
            prefix_cache=True,
            speculative_decoding=False,
            max_slots=0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
