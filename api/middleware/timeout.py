"""RequestTimeoutMiddleware — enforce a global per-request timeout.

Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import system_config
from utils.logger import get_correlation_id, get_logger

logger = get_logger(__name__)


# Request Timeout Middleware
class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce global request timeouts.

    Prevents runaway requests from consuming resources indefinitely.
    Default timeout: 60 seconds (configurable via REQUEST_TIMEOUT_SECONDS)
    """

    def __init__(self, app, timeout_seconds: float = 60.0):
        super().__init__(app)
        self.timeout = getattr(
            system_config, "REQUEST_TIMEOUT_SECONDS", timeout_seconds
        )

    async def dispatch(self, request, call_next):
        # Skip timeout for WebSocket connections and streaming endpoints
        if request.url.path.startswith("/api/ws"):
            return await call_next(request)

        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except asyncio.TimeoutError:
            request_id = get_correlation_id() or "unknown"
            logger.error(
                f"Request timeout after {self.timeout}s",
                extra={"request_id": request_id, "path": request.url.path},
            )
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "Request timeout",
                    "request_id": request_id,
                    "timeout_seconds": self.timeout,
                },
            )
