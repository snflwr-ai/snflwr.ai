"""
Ollama-compatible pass-through proxy.

OWU's OLLAMA_BASE_URL points at snflwr-api.  Non-chat Ollama API calls
(tags, show, generate, embed, pull, etc.) are forwarded unchanged to the
real Ollama backend configured in system_config.OLLAMA_PROXY_TARGET.
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
import httpx

from config import system_config

router = APIRouter(prefix="/api")

_OLLAMA_READ_TIMEOUT = 300.0  # seconds — matches OLLAMA_TIMEOUT default


async def _forward_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Send *method* + *path* to the real Ollama backend and return the raw response."""
    url = f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, read=_OLLAMA_READ_TIMEOUT)) as client:
        return await client.request(method, url, **kwargs)


async def _proxy_to_ollama(request: Request, path: str) -> Response:
    """Generic handler: reads request body, forwards to Ollama, returns response.

    Returns HTTP 503 when Ollama is unreachable.
    """
    body = await request.body()
    try:
        upstream = await _forward_request(
            request.method,
            path,
            content=body,
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length")
            },
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Ollama backend unreachable"},
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


# ---------------------------------------------------------------------------
# Pass-through endpoints
# ---------------------------------------------------------------------------

@router.get("/tags")
async def proxy_tags(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/tags")


@router.post("/show")
async def proxy_show(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/show")


@router.post("/generate")
async def proxy_generate(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/generate")


@router.post("/embed")
async def proxy_embed(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/embed")


@router.post("/embeddings")
async def proxy_embeddings(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/embeddings")


@router.delete("/delete")
async def proxy_delete(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/delete")


@router.post("/pull")
async def proxy_pull(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/pull")


@router.post("/copy")
async def proxy_copy(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/copy")


@router.get("/version")
async def proxy_version(request: Request) -> Response:
    return await _proxy_to_ollama(request, "/api/version")
