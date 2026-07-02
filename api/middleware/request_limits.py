"""RequestSizeLimitMiddleware — reject oversized request bodies.

Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import system_config
from utils.logger import get_correlation_id, get_logger

logger = get_logger(__name__)

# Exact definition moved verbatim from api/server.py:119
MAX_REQUEST_SIZE = getattr(system_config, "MAX_REQUEST_SIZE_MB", 10) * 1024 * 1024


# Request Body Size Limit Middleware
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce request body size limits.

    Prevents denial-of-service attacks via oversized request bodies.
    Default limit: 10MB (configurable via MAX_REQUEST_SIZE_MB)
    """

    async def dispatch(self, request, call_next):
        # Check Content-Length header if present
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_SIZE:
                    request_id = get_correlation_id() or "unknown"
                    logger.warning(
                        f"Request body too large: {size} bytes (limit: {MAX_REQUEST_SIZE})",
                        extra={"request_id": request_id},
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request body too large",
                            "max_size_bytes": MAX_REQUEST_SIZE,
                            "received_bytes": size,
                        },
                    )
            except ValueError:
                logger.warning(f"Malformed Content-Length header: {content_length}")
                return JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length header"}
                )
        elif request.method in ("POST", "PUT", "PATCH"):
            # No Content-Length header (e.g. chunked transfer encoding).
            # Read body with size cap to prevent unbounded memory use.
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_REQUEST_SIZE:
                    logger.warning(
                        f"Chunked request body exceeded limit: >{MAX_REQUEST_SIZE} bytes"
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request body too large",
                            "max_size_bytes": MAX_REQUEST_SIZE,
                        },
                    )
            # Re-inject the body so downstream handlers can read it
            request._body = body

        return await call_next(request)
