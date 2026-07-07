"""Block response helpers: message extraction, block formatting, safety incident recording."""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger(__name__)

# Hold-back streaming: how much text to accumulate before the FIRST output-safety
# check. A sentence boundary triggers it sooner (fast first flush); the char cap
# guarantees a long unbroken stream still gets vetted promptly.
_FIRST_CHECKPOINT_CHARS = 160


def _extract_last_user_message(messages: list) -> str:
    """Return the text of the last user-role message in *messages*.

    Handles both plain-string content and multimodal parts (list of dicts
    with ``type: "text"``).  Returns ``""`` when no user message is found.
    """
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Multimodal format — gather all text parts
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return " ".join(parts)
    return ""


def _message_text(msg: dict) -> str:
    """Text of a single message (plain string or multimodal text parts)."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _all_user_messages_text(messages: list) -> str:
    """Concatenate the text of EVERY user-role message.

    The input safety scan must see all student-authored turns, not only the last:
    a jailbreak placed in an earlier user turn (or any non-final message) would
    otherwise slip past ``check_input``.
    """
    return "\n".join(
        _message_text(m)
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    )


def _ollama_block_response(model: str, block_message: str) -> dict:
    """Build an Ollama-format response dict for a blocked message."""
    return {
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": {"role": "assistant", "content": block_message},
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "eval_count": 0,
    }


def _record_safety_incident(profile_id, result, content_snippet: str) -> None:
    """Best-effort human-in-the-loop escalation for a blocked student message.

    Records a DB incident and — for major/critical severities such as a
    self-harm disclosure — queues a parent alert via the incident logger.
    Without this, a child's crisis message shows the 988 safe-response but
    never notifies a trusted adult, and no incident is recorded for review.

    Students reach the model through this proxy (not api/routes/chat.py), so the
    escalation has to live here too. Fail-safe by design: any error is swallowed
    so the child's safe response is always delivered.
    """
    try:
        from safety.incident_logger import incident_logger

        incident_logger.log_incident(
            profile_id=profile_id or "unknown",
            session_id=None,
            incident_type=result.category.value,
            severity=result.severity.value,
            content_snippet=(content_snippet or "")[:200],
            metadata={
                "source": "ollama_proxy",
                "stage": getattr(result, "stage", None),
                "triggered_keywords": list(
                    getattr(result, "triggered_keywords", ()) or ()
                ),
            },
        )
    except Exception as exc:  # never let escalation break the child's response
        logger.error(
            "Failed to record safety incident (non-fatal): %s", exc, exc_info=True
        )


def _extract_text_from_ndjson_chunks(chunks: list[bytes]) -> str:
    """Concatenate the ``message.content`` fields from a list of Ollama NDJSON chunks."""
    parts: list[str] = []
    buffer = b"".join(chunks)
    for line in buffer.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts)


def _ollama_block_stream_bytes(model: str, block_message: str) -> bytes:
    """Build a single-chunk NDJSON stream body that delivers a safe-fallback block."""
    chunk = _json.dumps(_ollama_block_response(model, block_message)) + "\n"
    return chunk.encode()


def _strip_thinking_from_ndjson_line(line: bytes) -> bytes:
    """Return one Ollama NDJSON *line* with any ``message.thinking`` removed.

    The tutor is a reasoning model: each streamed message may carry a
    ``thinking`` (chain-of-thought) field alongside ``content``. Two reasons to
    drop it before it reaches Open WebUI:

    1. **Rendering** — OWUI >=0.10 added a reasoning display that mishandles the
       ``thinking`` field on our proxied stream and renders a *blank* answer.
    2. **Safety** — the raw chain-of-thought is unvetted text (output-safety
       only inspects ``content``) and must never be shown to a child.

    The model keeps reasoning (tutoring quality is unchanged); only what the
    client sees is trimmed. ``content`` is left byte-for-byte intact, so
    ``_extract_text_from_ndjson_chunks`` and output vetting are unaffected.

    Lines that don't parse, or that carry no ``thinking``, are returned exactly
    as received — this never fails closed on *content*, only passes through
    shape it doesn't recognise.
    """
    stripped = line.strip()
    if not stripped:
        return line
    try:
        obj = _json.loads(stripped)
    except (_json.JSONDecodeError, ValueError):
        return line
    msg = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(msg, dict) or "thinking" not in msg:
        return line
    msg.pop("thinking", None)
    return _json.dumps(obj).encode()


def _first_checkpoint_ready(text: str) -> bool:
    """True once enough answer text has accumulated to run the first check_output:
    a sentence boundary (after a little content) or the char cap."""
    if len(text) >= _FIRST_CHECKPOINT_CHARS:
        return True
    return len(text) >= 12 and any(p in text for p in ".!?")
