"""Admission control for model calls.

THE DEFECT THIS EXISTS FOR
--------------------------
Load test 2026-09-17, single 23 GB card, full tutoring pipeline:

    students   p50      p90      canned fallbacks
    3          16.3 s   29.0 s   0 / 24
    12         78.5 s  116.7 s   3 / 48
    20        127.7 s  156.8 s  40 / 60

Nothing about the tutor got worse at 20 students. What happened is that the
reveal confirm's 30 s budget was spent WAITING IN A QUEUE behind other students,
the enforcer failed closed, and the child received the withholding fallback —
a brush-off produced by queueing, not by anything they asked.

So the rule is: when there is no capacity, say so. A child sees "lots of
learners are asking questions right now"; they never see a worse answer.

RE-ENTRANCY IS LOAD-BEARING
---------------------------
One homework turn makes three to five model calls (gate, draft, confirm, and
often a rewrite plus a second confirm). The turn takes a slot ONCE; nested calls
inside it run free. Without that, a box with one slot would deadlock on its own
confirm call, which is the very bug we are fixing.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import asynccontextmanager

from core.inference.base import EngineOverloaded

logger = logging.getLogger(__name__)

# Depth of nested admitted calls for the CURRENT task. contextvars (not a plain
# attribute) because concurrent turns are separate tasks in one event loop.
_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "inference_depth", default=0
)


class Admission:
    """A bounded slot pool with a waiting deadline."""

    def __init__(self, *, max_concurrent: int, queue_wait_s: float, max_queue: int):
        # max_concurrent <= 0 means "the engine schedules itself" (vLLM).
        self._max_concurrent = max(0, int(max_concurrent))
        self._queue_wait_s = float(queue_wait_s)
        self._max_queue = max(0, int(max_queue))
        self._sem = (
            asyncio.Semaphore(self._max_concurrent) if self._max_concurrent else None
        )
        self._waiting = 0
        self._in_flight = 0
        self._admitted = 0
        self._rejected = 0
        self._wait_time_total = 0.0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def stats(self) -> dict:
        return {
            "max_concurrent": self._max_concurrent,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "admitted": self._admitted,
            "rejected": self._rejected,
            "avg_wait_s": (
                round(self._wait_time_total / self._admitted, 3)
                if self._admitted
                else 0.0
            ),
        }

    @asynccontextmanager
    async def slot(self):
        """Acquire capacity for one turn. Nested acquisitions are free."""
        if self._sem is None or _DEPTH.get() > 0:
            token = _DEPTH.set(_DEPTH.get() + 1)
            try:
                yield
            finally:
                _DEPTH.reset(token)
            return

        # "Waiting" must mean GENUINELY QUEUED. Counting a caller that is about
        # to acquire a free slot inflated the depth for a moment, and with a
        # queue limit of 1 the next caller was rejected as full while capacity
        # was in fact available -- a race this machine always won and CI lost.
        contended = self._sem.locked()

        if contended and self._max_queue and self._waiting >= self._max_queue:
            self._rejected += 1
            logger.warning(
                "inference admission: queue full (%d waiting, %d in flight)",
                self._waiting,
                self._in_flight,
            )
            raise EngineOverloaded("inference queue is full")

        if contended:
            self._waiting += 1
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._queue_wait_s)
        except asyncio.TimeoutError as exc:
            self._rejected += 1
            logger.warning(
                "inference admission: waited %.1fs for a slot, giving up "
                "(%d waiting, %d in flight)",
                self._queue_wait_s,
                self._waiting,
                self._in_flight,
            )
            raise EngineOverloaded(
                f"no inference capacity within {self._queue_wait_s:.0f}s"
            ) from exc
        finally:
            if contended:
                self._waiting -= 1

        waited = time.perf_counter() - started
        self._wait_time_total += waited
        self._admitted += 1
        self._in_flight += 1
        token = _DEPTH.set(_DEPTH.get() + 1)
        try:
            yield
        finally:
            _DEPTH.reset(token)
            self._in_flight -= 1
            self._sem.release()
