"""CorrelationIDMiddleware — assign/propagate X-Request-ID and track in-flight
requests for graceful shutdown. Extracted verbatim from api/server.py except the
connection counter, which now goes through api.connection_tracking (see B1).
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from api.connection_tracking import connection_tracker
from utils.logger import correlation_id_var, set_correlation_id


# Request Correlation ID Middleware
class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add correlation IDs to all requests for distributed tracing.

    - Generates or propagates X-Request-ID header
    - Stores in context variable for access in handlers and logs
    - Integrates with utils.logger for automatic log correlation
    - Returns correlation ID in response headers
    """

    async def dispatch(self, request, call_next):
        # Get or generate correlation ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in context variable (integrates with logger)
        token = set_correlation_id(request_id)

        # Track active connections for graceful shutdown
        await connection_tracker.increment()

        try:
            # Process request
            response = await call_next(request)

            # Add correlation ID to response
            response.headers["X-Request-ID"] = request_id

            return response
        finally:
            # Reset context
            correlation_id_var.reset(token)

            # Decrement active connections
            await connection_tracker.decrement()
