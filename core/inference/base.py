"""Engine-neutral inference types and errors.

Every TUTORING model call goes through this interface: the student turn in the
proxy, the legacy `/api/chat/send` route, and the guidance enforcer's
gate/confirm/rewrite calls. Callers never see an engine's wire format.

⚠️ The safety classifier and the topic gate do NOT, and should not. This
docstring claimed they did until 2026-09-23, which a `grep` disproves in one
line -- they call `utils/ollama_client` directly. That is deliberate, and
routing them through here would be a regression on two counts:

* this interface gates on the SERVING PLAN, and a safety classifier must run
  even when the plan has disabled tutoring. Safety is not a tutoring feature.
* this interface takes a box-wide GPU admission slot, and the safety models are
  CPU-PINNED (`llama-guard3-cpu`, `PARAMETER num_gpu 0`, 0.0 GB VRAM in
  `/api/ps`). Charging them a GPU slot would starve real tutoring turns for a
  model that is not on the card.

What matters for safety is that the classifier is pinned to `llama-guard3` and
never falls back to the tutor backbone -- a classifier that follows the backbone
degrades silently on a swap. `scripts/postdeploy_smoke.py` asserts the live
model is the configured one and not a fallback.

The types are deliberately small. A driver's job is to translate, not to add
behaviour: policy (what to send, what to do with the answer) stays in the
pedagogy and safety layers where it is tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ChatRequest:
    """One model call, independent of engine."""

    model: str
    messages: list[dict]
    stream: bool = False
    options: dict = field(default_factory=dict)
    think: Optional[bool] = False


@dataclass(frozen=True)
class ChatChunk:
    """One streamed delta. ``done`` marks the final frame, which carries usage."""

    text: str
    done: bool
    usage: Optional[dict] = None


@dataclass(frozen=True)
class ChatResult:
    """A complete answer."""

    text: str
    usage: Optional[dict]
    raw: dict


@dataclass(frozen=True)
class Capabilities:
    """What an engine can do, so callers do not have to know engine names."""

    batching: bool
    prefix_cache: bool
    speculative_decoding: bool
    max_slots: int


@dataclass(frozen=True)
class DriverHealth:
    reachable: bool
    models: tuple[str, ...] = ()
    detail: str = ""


class InferenceError(Exception):
    """Base class for every engine failure the app handles."""


class EngineUnreachable(InferenceError):
    """The engine did not accept a connection."""


class EngineTimeout(InferenceError):
    """The engine accepted the call but did not finish in time."""


class EngineOverloaded(InferenceError):
    """No capacity: the admission queue is full or the wait exceeded its deadline.

    This is the ONE error that must never be turned into a tutoring answer. Under
    load the reveal confirm used to time out and fail closed, so two thirds of
    replies at 20 concurrent students became the canned withholding fallback and
    children saw a brush-off caused purely by queueing (load test 2026-09-17).
    """


class ModelNotAvailable(InferenceError):
    """The requested model (or context length) is not served by this engine."""


@runtime_checkable
class InferenceDriver(Protocol):
    """What every engine driver implements."""

    name: str

    async def chat(self, req: ChatRequest, *, timeout_s: float) -> ChatResult: ...

    def stream(
        self, req: ChatRequest, *, timeout_s: float
    ) -> AsyncIterator[ChatChunk]: ...

    async def health(self) -> DriverHealth: ...

    def capabilities(self) -> Capabilities: ...


def usage_from_openai(payload: Any) -> Optional[dict]:
    """``{"prompt_tokens","completion_tokens"}`` -> ``{"input","output"}``."""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    return {"input": prompt or 0, "output": completion or 0}
