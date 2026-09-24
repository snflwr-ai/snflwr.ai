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


# The disclosure kinds whose incidents are MAJOR, and therefore the only ones
# that raise a parent alert (escalation routes major/critical). Defined once
# because two call sites now depend on it agreeing -- duplicating the rule is
# how it drifts, and a drift here means either two alerts or none.
ALERTING_DISCLOSURE_KINDS = ("suicidal_ideation", "predatory_contact")


def _record_safety_incident(
    profile_id, result, content_snippet: str, send_alert: bool = True
) -> None:
    """Best-effort human-in-the-loop escalation for a blocked student message.

    Records a DB incident and — for major/critical severities such as a
    self-harm disclosure — queues a parent alert via the incident logger.
    Without this, a child's crisis message shows the 988 safe-response but
    never notifies a trusted adult, and no incident is recorded for review.

    Students reach the model through this proxy (not api/routes/chat.py), so the
    escalation has to live here too. Fail-safe by design: any error is swallowed
    so the child's safe response is always delivered.

    ⚠️ `send_alert=False` records the incident WITHOUT alerting, and exists for
    exactly one case: the turn was blocked AND a disclosure was detected, so
    `_record_disclosure_incident` has already alerted with the correct framing.
    Both rows are still written -- this row remains the record of why the reply
    was replaced -- but a parent gets ONE message, not two, and not one that
    describes their child as having requested harmful content.
    """
    try:
        from safety.incident_logger import incident_logger

        incident_logger.log_incident(
            send_alert=send_alert,
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


def _record_disclosure_incident(
    profile_id,
    kind: str,
    matched: str,
    content_snippet: str,
    blocked: bool = False,
) -> bool:
    """Escalate a child's risk DISCLOSURE without blocking their turn.

    Distinct from _record_safety_incident, which is only reached when a message is
    blocked. A disclosure must not be blocked -- the tutor already redirects 15 of
    17 such turns to a trusted adult, and canned text would be worse -- but it must
    still reach escalation.py so a parent is told. Measured before this existed:
    1 of 12 disclosures escalated, and analytics carries no transcript, so the rest
    were invisible to every adult permanently.

    ⚠️ `blocked=True` is NOT a contradiction of the above. The caller now runs the
    disclosure detector on blocked turns too, so a disclosure the harm classifier
    happened to catch is recorded as a DISCLOSURE rather than only as
    `exploitation` -- which reads as the child requesting harmful content, the
    opposite of what happened. Both rows are written for such a turn: this one
    says what the child was doing, the safety row says why the reply was replaced.

    Severity is MAJOR for ideation and predatory contact so the parent alert fires;
    escalation.py routes major/critical. Fail-safe: any error is swallowed, because
    a failure here must never cost the child their answer.
    """
    try:
        from safety.incident_logger import incident_logger

        # "minor", not "moderate": log_incident accepts only minor/major/critical
        # and REJECTED "moderate", so bullying and disordered-eating disclosures
        # were never recorded and no parent could ever see them.
        severity = "major" if kind in ALERTING_DISCLOSURE_KINDS else "minor"
        incident_logger.log_incident(
            profile_id=profile_id or "unknown",
            session_id=None,
            incident_type=f"disclosure_{kind}",
            severity=severity,
            content_snippet=(content_snippet or "")[:200],
            metadata={
                "source": "ollama_proxy",
                "stage": "disclosure_detector",
                "disclosure_kind": kind,
                "matched": matched,
                # Usually False: a disclosure is escalated WITHOUT blocking, so
                # the child received the tutor's own reply.
                #
                # True means the harm classifier blocked this turn as well, so
                # the child got a canned refusal. Recorded because a reviewer
                # needs to know which of the two a parent alert describes -- and
                # because a blocked disclosure is the case where the child was
                # both reaching out AND refused, which is worth seeing.
                "blocked": blocked,
            },
        )
        # Whether a parent was alerted by THIS row. The caller uses it to avoid
        # a second alert for the same turn -- and returning the fact, rather
        # than re-deriving the severity rule at the call site, is what keeps
        # the two in agreement.
        return kind in ALERTING_DISCLOSURE_KINDS
    except Exception as exc:  # never let escalation break the child's response
        logger.error(
            "Failed to record disclosure incident (non-fatal): %s", exc, exc_info=True
        )
        # Nothing was alerted, so the caller must NOT suppress the safety alert.
        return False


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


def _ollama_stream_bytes_for_text(
    model: str, text: str, usage: dict | None = None
) -> bytes:
    """Build a single-chunk NDJSON body carrying a normal assistant reply.

    A streamed turn is buffered whole so the pedagogy enforcer can run on it, and
    the enforcer may REWRITE the answer -- at which point the original chunk
    sequence no longer matches what should be served. Re-emitting one chunk keeps
    the wire format the client expects while carrying the final text.

    Distinct from ``_ollama_block_stream_bytes`` only in intent: that one serves a
    safety block, this one serves the tutor's own answer, and keeping them apart
    stops a reader mistaking a normal reply for a blocked one.
    """
    payload = _ollama_block_response(model, text)
    if usage:
        payload["prompt_eval_count"] = usage.get("input", 0)
        payload["eval_count"] = usage.get("output", 0)
    return (_json.dumps(payload) + "\n").encode()


def _ollama_content_chunk_bytes(model: str, text: str) -> bytes:
    """One NON-final NDJSON chunk carrying ``text`` -- used to put a notice
    (the break reminder) ahead of a streamed reply without ending the stream."""
    payload = _ollama_block_response(model, text)
    payload["done"] = False
    for key in ("done_reason", "total_duration", "eval_count"):
        payload.pop(key, None)
    return (_json.dumps(payload) + "\n").encode()


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


def _strip_age_scaffolding_from_ndjson_chunks(chunks: list[bytes]) -> list[bytes]:
    """Return ``chunks`` with a leading age-range tag removed from the assembled
    assistant content.

    The streaming paths re-emit the upstream NDJSON bytes verbatim, so the only
    place to remove narrated scaffolding is in the chunks themselves. The tag is
    always at the very start of the reply but may be split across several
    chunks, so the assembled text decides how many characters to drop and the
    leading chunks are then rewritten in order.

    Lines that don't parse are passed through untouched and still consume no
    budget, matching ``_strip_thinking_from_ndjson_line``: this never fails
    closed on content, it only declines to edit shape it doesn't recognise.
    """
    from core.response_scaffolding import leading_scaffolding_len

    text = _extract_text_from_ndjson_chunks(chunks)
    remaining = leading_scaffolding_len(text)
    # `strip_scaffolding` keeps a tag-only reply rather than emptying it; mirror
    # that here so the two helpers cannot disagree about the same message.
    if not remaining or not text[remaining:].strip():
        return chunks

    out: list[bytes] = []
    for line in chunks:
        if remaining <= 0:
            out.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            obj = _json.loads(stripped)
        except (_json.JSONDecodeError, ValueError):
            out.append(line)
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content:
            out.append(line)
            continue
        drop = min(remaining, len(content))
        msg["content"] = content[drop:]
        remaining -= drop
        out.append(_json.dumps(obj).encode() + b"\n")
    return out


def _first_checkpoint_ready(text: str) -> bool:
    """True once enough answer text has accumulated to run the first check_output:
    a sentence boundary (after a little content) or the char cap."""
    if len(text) >= _FIRST_CHECKPOINT_CHARS:
        return True
    return len(text) >= 12 and any(p in text for p in ".!?")
