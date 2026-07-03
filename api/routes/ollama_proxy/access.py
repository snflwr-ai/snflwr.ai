"""Access control helpers: user identity, admin gate, student model filtering."""

from __future__ import annotations

import json as _json
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from api.middleware.auth import is_genuine_admin
from config import system_config
from core.authentication import AuthSession
from utils.logger import get_logger

logger = get_logger(__name__)


def _get_user_from_headers(request: Request) -> tuple:
    """Extract user identity from OWU forwarded headers.

    Returns ``(user_id, role)``.  Fails closed: missing headers yield
    ``(None, "user")`` so the request is treated as a student.
    """
    user_id: Optional[str] = request.headers.get("X-OpenWebUI-User-Id") or None
    role: str = request.headers.get("X-OpenWebUI-User-Role") or "user"
    return user_id, role


def _admin_only(request: Request, session: AuthSession) -> Optional[Response]:
    """Gate an endpoint to a genuine admin session. Returns 403 otherwise, else None.

    Used for the raw inference (``/api/generate``, ``/api/embed*``) and
    model-management (``/api/pull|delete|copy``) endpoints, which are NOT part of
    the student flow — Open WebUI drives all user-facing generation through
    ``/api/chat`` (which runs the safety pipeline). Authority requires a genuine
    admin *session*: the internal service key (Open WebUI) is a relay, not an
    admin, so it cannot reach these even by forwarding X-OpenWebUI-User-Role:
    admin. Without this gate a leaked key could reach raw, unfiltered model
    output or mutate the model set.
    """
    if not is_genuine_admin(session):
        logger.info(
            "Blocked non-admin access to %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "This endpoint is restricted to administrators."},
        )
    return None


def _student_visible_models() -> set:
    """Model names a non-admin (student) may see in the chat dropdown.

    Only the canonical tutor model and its ``:latest`` tag — never the
    backbone, rollback, or backup variants that share the same Ollama
    backend but must never be selectable by a child.
    """
    default = (system_config.OLLAMA_DEFAULT_MODEL or "snflwr.ai").strip()
    base = default.split(":", 1)[0]
    return {base, f"{base}:latest"}


def _filter_tags_for_students(payload: bytes) -> bytes:
    """Drop non-public models from an Ollama ``/api/tags`` response body.

    Returns the payload unchanged if it can't be parsed or has no model
    list, so a malformed upstream response never crashes the dropdown.
    """
    try:
        data = _json.loads(payload)
    except (ValueError, _json.JSONDecodeError):
        return payload
    models = data.get("models")
    if not isinstance(models, list):
        return payload
    allowed = _student_visible_models()
    data["models"] = [
        m for m in models if isinstance(m, dict) and m.get("name") in allowed
    ]
    return _json.dumps(data).encode()


# Fields in an Ollama /api/show response that expose the tutor's SYSTEM/safety
# prompt (and the template / sampling config that frame it). Stripped for
# non-admins so a child cannot read — and then attempt to evade — the safety
# instructions embedded in the model's Modelfile.
_SHOW_SENSITIVE_FIELDS = ("modelfile", "system", "template", "parameters")


def _filter_show_for_students(payload: bytes) -> bytes:
    """Drop the prompt-bearing fields from an Ollama ``/api/show`` response body.

    Keeps non-sensitive metadata (details, model_info, capabilities) that Open
    WebUI needs for the model dropdown. Returns the payload unchanged if it can't
    be parsed, so a malformed upstream response never breaks the model-info call.
    """
    try:
        data = _json.loads(payload)
    except (ValueError, _json.JSONDecodeError):
        return payload
    if not isinstance(data, dict):
        return payload
    for field in _SHOW_SENSITIVE_FIELDS:
        data.pop(field, None)
    return _json.dumps(data).encode()
