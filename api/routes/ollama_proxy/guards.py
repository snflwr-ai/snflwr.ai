"""Pure admission-control predicates for student chat: rate limit, backend
circuit, license. Each returns a block-message string or None. Extracted
verbatim from proxy_chat (no behavior change)."""

from __future__ import annotations

from typing import Optional

from config import system_config
from utils.circuit_breaker import ollama_circuit
from utils.logger import get_logger
from utils.rate_limiter import rate_limiter

logger = get_logger(__name__)


def rate_limit_block_reason(user_id: Optional[str]) -> Optional[str]:
    if system_config.CHAT_RATE_LIMIT_PER_MINUTE <= 0:
        return None
    # An identity-less relay request (no forwarded user id) can't reach a tutor
    # turn anyway — the no-profile gate blocks it downstream. Skip the limiter so
    # these don't all collide in one shared "unknown" bucket and collaterally
    # block legitimate students during a transient OWUI header drop.
    if not user_id:
        return None
    allowed, info = rate_limiter.check_rate_limit(
        identifier=user_id,
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
        return slow_msg
    return None


def circuit_block_reason(user_id: Optional[str]) -> Optional[str]:
    # Non-consuming, self-healing read: only short-circuit with the friendly
    # message while the circuit is open AND still inside its recovery window.
    # Once the window elapses we return None so the request reaches the transport
    # layer, whose can_execute() drives the OPEN→HALF_OPEN recovery probe and
    # records the outcome. (is_open / time_until_retry don't mutate breaker state,
    # so this shortcut never consumes a half-open probe slot.)
    if ollama_circuit.is_open and ollama_circuit.time_until_retry() > 0:
        logger.warning(
            "Ollama circuit OPEN — fast-failing student chat for %s", user_id
        )
        busy_msg = (
            "The tutor is taking a quick break and will be back in a moment. "
            "Please try again shortly. 🌻"
        )
        return busy_msg
    return None


def license_block_reason(user_id: Optional[str]) -> Optional[str]:
    if not system_config.LICENSE_ENFORCED:
        return None
    import time as _time

    from core import licensing

    lic = licensing.current_state(int(_time.time()))
    if not lic.allowed:
        logger.info("License gate blocked student %s (state=%s)", user_id, lic.state)
        msg = (
            "A snflwr.ai subscription is needed to use the tutor. "
            "Open Settings → Billing to subscribe or sign in."
        )
        return msg
    return None
