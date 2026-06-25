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
    global _client, _init_failed
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
    except Exception as exc:  # bad keys, unreachable, missing dep
        _init_failed = True
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
