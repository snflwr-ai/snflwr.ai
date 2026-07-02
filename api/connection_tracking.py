"""Process-singleton gauge of in-flight HTTP requests, used by the graceful
shutdown drain. Encapsulates the counter + its lock so both the correlation
middleware and the shutdown routine share one clear contract (replaces the
former module-global _active_connections + _connections_lock in api/server.py).
"""

from __future__ import annotations

import asyncio


class ConnectionTracker:
    """Tracks the number of in-flight requests for graceful-shutdown draining."""

    def __init__(self) -> None:
        self._count = 0
        self._lock = asyncio.Lock()

    async def increment(self) -> None:
        async with self._lock:
            self._count += 1

    async def decrement(self) -> None:
        async with self._lock:
            self._count -= 1

    @property
    def count(self) -> int:
        return self._count


connection_tracker = ConnectionTracker()
