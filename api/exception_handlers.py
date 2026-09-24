"""FastAPI exception handlers, registered via register_exception_handlers(app).

Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from utils.logger import get_logger

logger = get_logger(__name__)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation errors so 422s are diagnosable in the server log.

    NOTE: Pydantic v2 error entries for `value_error` carry the raw
    `ValueError` instance in `ctx.error`, which is not JSON-serializable.
    Passing `exc.errors()` straight to `JSONResponse` raises
    `TypeError: Object of type ValueError is not JSON serializable`,
    which then propagates to the generic exception handler and turns
    every 422 into a 500 with the bland "An internal error occurred"
    message — hiding the real cause from API clients.

    `jsonable_encoder` recursively coerces non-JSON types (including
    exception instances) to strings, matching what FastAPI's default
    validation handler does. Apply it to BOTH the detail and the whole
    content dict so any future non-serializable field also round-trips.
    """
    errors = exc.errors()
    logger.warning(
        f"422 Unprocessable Entity: {request.method} {request.url.path} — {errors}"
    )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {
                "detail": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
    )


async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def generic_exception_handler(request, exc):
    """
    Generic exception handler to prevent stack trace leakage.
    Logs the full error internally but returns a safe message to users.
    """
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


async def inference_busy_handler(request: Request, exc: Exception) -> Response:
    """Render "no capacity right now" in the shape the client asked for.

    A busy box is an operational fact, not a teaching moment: this must never be
    the homework-withholding fallback, and the turn is not recorded as served.
    """
    from api.routes.ollama_proxy import blocks

    # IMPORTED, not re-typed. This handler used to carry its own copy of the
    # text, byte-identical to chat._BUSY_MESSAGE -- safe only because two
    # constants happened to be equal. Editing _BUSY_MESSAGE would have left
    # this path serving the OLD string, which then matches no entry in
    # scripts/latency_bar.py:_SENTINELS, so every busy reply would score as a
    # real latency measurement again. That is the precise drift
    # tests/test_latency_bar_sentinels.py exists to prevent, routed around by
    # a copy the test could not see.
    from api.routes.ollama_proxy.chat import _BUSY_MESSAGE

    model = getattr(exc, "model", "") or "snflwr.ai"
    stream = bool(getattr(exc, "stream", False))
    message = _BUSY_MESSAGE
    logger.warning("Inference busy — served a wait message instead of a tutor turn")
    if stream:
        return Response(
            content=blocks._ollama_block_stream_bytes(model, message),
            media_type="application/x-ndjson",
        )
    return JSONResponse(content=blocks._ollama_block_response(model, message))


def register_exception_handlers(app: FastAPI) -> None:
    """Register the module's exception handlers on the given app."""
    app.exception_handler(RequestValidationError)(validation_exception_handler)
    app.exception_handler(HTTPException)(http_exception_handler)
    from api.routes.ollama_proxy.admission import InferenceBusy

    app.exception_handler(InferenceBusy)(inference_busy_handler)
    app.exception_handler(Exception)(generic_exception_handler)
