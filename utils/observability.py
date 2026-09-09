"""Metadata-only Langfuse observability for the chat proxy.

Hard rule: this module NEVER receives or sends chat content. `trace_chat_turn`
has no parameter that can carry prompt/response text. It sends only operational
metadata (latency, model, token counts, safety verdict, age-band) plus a one-way
hash of the profile id for per-child grouping. Default-off and fail-safe: any
error is swallowed so tracing can never break or slow a chat turn.
"""

import hashlib
import hmac
from typing import Optional

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

_client = None
_init_failed = False


_init_error: Optional[str] = None  # None | "dependency_missing" | "init_failed"


def age_band(age) -> str:
    """Bucket an exact age into a coarse band (privacy-preserving)."""
    if not isinstance(age, int):
        return "unknown"
    if age < 13:
        return "<13"
    if age < 18:
        return "13-17"
    return "18+"


def hash_profile(profile_id: Optional[str]) -> str:
    """One-way HMAC-SHA256 of the profile id (salted). Stable, not reversible."""
    if not profile_id:
        return "anon"
    salt = (system_config.LANGFUSE_HASH_SALT or "snflwr-default-salt").encode()
    return hmac.new(salt, str(profile_id).encode(), hashlib.sha256).hexdigest()[:32]


def _get_client():
    """Lazily build (and memoize) the Langfuse client. Returns None if disabled
    or if keys are missing / init has already failed."""
    global _client, _init_failed, _init_error
    if _init_failed:
        return None
    if _client is not None:
        return _client
    if not (
        system_config.LANGFUSE_ENABLED
        and system_config.LANGFUSE_PUBLIC_KEY
        and system_config.LANGFUSE_SECRET_KEY
    ):
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=system_config.LANGFUSE_PUBLIC_KEY,
            secret_key=system_config.LANGFUSE_SECRET_KEY,
            host=system_config.LANGFUSE_HOST,
        )
        return _client
    except ImportError as exc:
        # Categorically different from the failures below. A missing import means
        # the IMAGE WAS BUILT WITHOUT the package — it cannot recover on its own
        # and needs a human. This exact case (langfuse present in
        # requirements.txt, absent from requirements.lock, Dockerfile installing
        # only from the lock) ran unnoticed because it logged identically to a
        # transient bad key. ERROR, and it names the fix.
        _init_failed = True
        _init_error = "dependency_missing"
        logger.error(
            "Langfuse is NOT INSTALLED, so tracing is disabled even though it is "
            "configured and enabled. The image was built without it — check that "
            "the package is present in requirements.lock (docker/Dockerfile "
            "installs from the lock with --require-hashes). Underlying error: %s",
            exc,
        )
        return None
    except Exception as exc:  # bad keys, unreachable host — transient, may recover
        # Deliberately broad: tracing must NEVER break a child's tutoring turn.
        _init_failed = True
        _init_error = "init_failed"
        logger.warning("Langfuse init failed; tracing disabled: %s", exc)
        return None


def trace_chat_turn(
    *,
    model: str,
    age_band: str,
    profile_hash: str,
    blocked: bool,
    safety: dict,
    latency_ms: dict,
    tokens: Optional[dict] = None,
) -> None:
    """Emit one metadata-only trace for a chat turn. Never raises.

    NOTE: there is deliberately NO parameter for prompt/response text.
    """
    if not system_config.LANGFUSE_ENABLED:
        return
    try:
        client = _get_client()
        if client is None:
            return
        trace = client.trace(
            name="chat-turn",
            user_id=profile_hash,
            metadata={
                "age_band": age_band,
                "blocked": blocked,
                "safety": safety,
            },
            tags=["blocked"] if blocked else ["allowed"],
        )
        trace.generation(
            name="tutor",
            model=model,
            usage=tokens or None,
            level="WARNING" if blocked else "DEFAULT",
            metadata={"latency_ms": latency_ms, "safety": safety},
        )
    except Exception as exc:  # fail-safe: tracing must never break chat
        logger.debug("trace_chat_turn failed (ignored): %s", exc)


def tracing_status() -> dict:
    """Whether tracing is actually live, in a form /health can surface.

    Exists because "tracing is off" and "tracing is on but nothing happened"
    were indistinguishable from the outside, which is how a missing dependency
    survived in the built image. Never includes credentials.

    state:
      disabled           — not switched on; nothing is expected
      misconfigured      — enabled but public/secret key missing
      dependency_missing — enabled and configured, but the package is not
                           installed: THE IMAGE IS BUILT WRONG
      init_failed        — enabled and installed, but the client would not start
                           (bad keys, unreachable host) — may recover
      active             — client constructed
      idle               — configured and ready; client not built yet
    """
    if not system_config.LANGFUSE_ENABLED:
        return {"state": "disabled", "host": None}
    if not (system_config.LANGFUSE_PUBLIC_KEY and system_config.LANGFUSE_SECRET_KEY):
        return {"state": "misconfigured", "host": system_config.LANGFUSE_HOST}
    if _init_error is not None:
        return {"state": _init_error, "host": system_config.LANGFUSE_HOST}
    if _client is not None:
        return {"state": "active", "host": system_config.LANGFUSE_HOST}
    return {"state": "idle", "host": system_config.LANGFUSE_HOST}
