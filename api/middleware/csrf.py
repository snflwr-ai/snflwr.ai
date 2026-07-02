"""CSRFMiddleware — validate CSRF tokens on state-changing requests.

Thin wrapper over api.middleware.csrf_protection.validate_csrf_token.
Extracted verbatim from api/server.py (no logic change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.middleware.csrf_protection import validate_csrf_token


# CSRF Protection Middleware
class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware to validate CSRF tokens on state-changing requests"""

    async def dispatch(self, request, call_next):
        # Validate CSRF token before processing request.
        # BaseHTTPMiddleware does not propagate HTTPException to FastAPI's
        # exception handlers, so we must catch and return a JSONResponse.
        try:
            await validate_csrf_token(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "detail": exc.detail,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Process request
        response = await call_next(request)
        return response
