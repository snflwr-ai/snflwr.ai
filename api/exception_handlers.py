"""FastAPI exception handlers, registered via register_exception_handlers(app).

Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
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


def register_exception_handlers(app: FastAPI) -> None:
    """Register the module's exception handlers on the given app."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
