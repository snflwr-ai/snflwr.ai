"""Engine-neutral inference types and errors.

Every model call in the product goes through this interface: the student turn in
the proxy, the guidance enforcer's gate/confirm/rewrite calls, the safety
classifier and the topic gate. Callers never see an engine's wire format.

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
