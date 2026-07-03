"""Pass-through routes: forward non-chat Ollama API calls with access control."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from api.middleware.auth import get_current_session, is_genuine_admin
from api.routes.ollama_proxy import access, transport
from config import system_config
from core.authentication import AuthSession
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/tags")
async def proxy_tags(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    """GET /api/tags — genuine admins see every model; everyone else (incl. all
    Open WebUI relay traffic) only the tutor model.

    Filtering the backbone/rollback/backup variants out for students happens
    here at the proxy, so the guarantee holds regardless of Open WebUI's own
    model-access config and survives a fresh open-webui-data volume. Fails
    closed: only a genuine admin *session* sees the full list — the internal
    service key (relay) cannot unlock it with a forwarded admin header.
    """
    response = await transport._proxy_to_ollama(request, "/api/tags")
    if is_genuine_admin(session) or response.status_code != 200:
        return response
    return Response(
        content=access._filter_tags_for_students(response.body),
        status_code=response.status_code,
        media_type=response.media_type or "application/json",
    )


@router.post("/show")
async def proxy_show(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    """POST /api/show — model details. Ollama echoes the full Modelfile here,
    including the tutor's SYSTEM / safety prompt, TEMPLATE, and PARAMETERs.

    A genuine admin *session* gets the response verbatim; everyone else — every
    Open WebUI relay (``internal_service``) and thus every student — gets the
    prompt-bearing fields stripped, keeping only non-sensitive metadata for the
    model dropdown. Mirrors ``proxy_tags``; fails closed (only a genuine admin
    session unlocks the full body, never a forwarded admin header).
    """
    response = await transport._proxy_to_ollama(request, "/api/show")
    if is_genuine_admin(session) or response.status_code != 200:
        return response
    return Response(
        content=access._filter_show_for_students(response.body),
        status_code=response.status_code,
        media_type=response.media_type or "application/json",
    )


@router.post("/generate")
async def proxy_generate(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: raw completion bypasses the /api/chat safety pipeline and would
    # return UNFILTERED model output to a child. Students use /api/chat only.
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/generate")


@router.post("/embed")
async def proxy_embed(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/embed")


@router.post("/embeddings")
async def proxy_embeddings(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/embeddings")


@router.delete("/delete")
async def proxy_delete(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: mutates the installed model set (destructive).
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/delete")


@router.post("/pull")
async def proxy_pull(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: could introduce an unvetted (uncensored) model.
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/pull")


@router.post("/copy")
async def proxy_copy(
    request: Request, session: AuthSession = Depends(get_current_session)
) -> Response:
    # Admin-only: mutates the installed model set.
    blocked = access._admin_only(request, session)
    if blocked is not None:
        return blocked
    return await transport._proxy_to_ollama(request, "/api/copy")


@router.get("/version")
async def proxy_version(request: Request) -> Response:
    # Session-gated (router dependency) but intentionally NOT admin-gated: Open
    # WebUI calls /api/version as the relay (non-admin) for its Ollama-connection
    # health check, and the body is just the Ollama version string — non-sensitive,
    # unlike /api/show (which leaks the Modelfile). Admin-gating it would break
    # OWUI's connection indicator for no security gain.
    return await transport._proxy_to_ollama(request, "/api/version")


# ---------------------------------------------------------------------------
# Proxy health check — verifies round-trip to Ollama
# ---------------------------------------------------------------------------


@router.get("/health")
async def proxy_health() -> JSONResponse:
    """Verify the proxy can reach the Ollama backend."""
    try:
        resp = await transport._forward_request("GET", "/api/version")
        ollama_version = resp.json() if resp.status_code == 200 else None
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "detail": "Ollama backend unreachable",
                "target": system_config.OLLAMA_PROXY_TARGET,
            },
        )
    return JSONResponse(
        content={
            "status": "healthy",
            "ollama": ollama_version,
            "target": system_config.OLLAMA_PROXY_TARGET,
        },
    )
