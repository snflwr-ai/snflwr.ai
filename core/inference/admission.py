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

THE SLOT COUNT IS A PROMISE ABOUT THE BOX, NOT ABOUT A PROCESS
--------------------------------------------------------------
Until 2026-09-20 capacity was an `asyncio.Semaphore`, which is per process. The
API runs EIGHT worker processes (measured with `docker top`), so a plan
advertising `slots=1` really admitted eight concurrent turns onto a card that
holds one model -- and up to 40 on the k8s manifest. Exactly the shape of the
login rate limit that advertised 5/min and admitted ~20 across four workers
(#272): a per-process counter enforcing a per-box promise.

So a reservation is also taken in a small SQLite table beside the app data, the
same way the rate limiter shares its windows. The local semaphore is kept in
front of it because it already gives fair queueing and the re-entrancy above;
the shared table is what makes `slots=N` true for the machine.

FAIL-OPEN, DELIBERATELY. If the shared table cannot be opened or written, this
degrades to the per-process behaviour it had before rather than refusing to
tutor: an over-admitted turn is slow, a refused one is a child told to go away.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Optional

from core.inference.base import EngineOverloaded

logger = logging.getLogger(__name__)

# Depth of nested admitted calls for the CURRENT task. contextvars (not a plain
# attribute) because concurrent turns are separate tasks in one event loop.
_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "inference_depth", default=0
)


# How long a reservation may live before another worker may reap it. A crashed
# worker cannot release its own row, so without this one crash would wedge the
# box until restart. It must exceed the longest LEGITIMATE turn, or a slow turn
# gets its row reaped and the box over-admits -- which is the old behaviour, not
# a new failure. The tutor read timeout is the bound that matters.
_SLOT_TTL_S = float(os.getenv("INFERENCE_SLOT_TTL_S", "330"))


def _slot_db_path() -> Optional[str]:
    """Where the cross-process reservations live, or None to stay per-process.

    ``INFERENCE_SLOT_DB=memory`` disables sharing (single-process tests and
    tooling); any other value is used as the path. Mirrors the rate limiter's
    ``SNFLWR_RATE_LIMIT_DB``, including the failure mode: if config is
    unavailable we degrade to per-process rather than guess a path.
    """
    override = os.getenv("INFERENCE_SLOT_DB")
    if override:
        return None if override == "memory" else override
    try:
        from config import system_config  # noqa: PLC0415 - avoid an import cycle

        return str(system_config.APP_DATA_DIR / "inference_slots.db")
    except Exception:  # noqa: BLE001 - tooling contexts have no config
        return None


class _SharedSlots:
    """Box-wide slot reservations in SQLite. Every method fails open."""

    def __init__(self, path: Optional[str]):
        self._path = path
        self._ready = False
        if path:
            try:
                self._connect().close()
                self._ready = True
            except Exception as exc:  # noqa: BLE001 - see FAIL-OPEN above
                logger.warning(
                    "inference admission: shared slot table unavailable (%s); "
                    "capacity will be enforced per process only",
                    exc,
                )

    @property
    def enabled(self) -> bool:
        return self._ready

    def _connect(self):
        con = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute(
            "CREATE TABLE IF NOT EXISTS inflight ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  pid INTEGER NOT NULL,"
            "  acquired_at REAL NOT NULL)"
        )
        return con

    def reserve(self, max_concurrent: int) -> Optional[int]:
        """Take a slot if the BOX has one free. Returns a row id, or None."""
        if not self._ready:
            return None
        now = time.time()
        try:
            con = self._connect()
            try:
                # One transaction: reap, count, insert. BEGIN IMMEDIATE so two
                # workers cannot both read "0 in flight" and both insert.
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "DELETE FROM inflight WHERE acquired_at < ?", (now - _SLOT_TTL_S,)
                )
                (count,) = con.execute("SELECT COUNT(*) FROM inflight").fetchone()
                if count >= max_concurrent:
                    con.execute("ROLLBACK")
                    return None
                cur = con.execute(
                    "INSERT INTO inflight (pid, acquired_at) VALUES (?, ?)",
                    (os.getpid(), now),
                )
                con.execute("COMMIT")
                return int(cur.lastrowid)
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - fail open
            logger.warning("inference admission: reserve failed (%s); admitting", exc)
            return -1  # sentinel: admitted without a row, release is a no-op

    def release(self, row_id: Optional[int]) -> None:
        if not self._ready or row_id is None or row_id < 0:
            return
        try:
            con = self._connect()
            try:
                con.execute("DELETE FROM inflight WHERE id = ?", (row_id,))
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - a lost row is reaped by TTL
            logger.warning("inference admission: release failed (%s)", exc)


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
        # Box-wide half of the promise. Only meaningful when a limit exists at
        # all: with max_concurrent <= 0 the engine schedules itself (vLLM).
        self._shared = _SharedSlots(_slot_db_path()) if self._max_concurrent else None
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
            "cross_process": bool(self._shared and self._shared.enabled),
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

        # A local slot is not capacity: seven sibling workers each hold their
        # own. Reserve against the BOX, inside whatever wait budget is left, and
        # hand the local slot back if the box is genuinely full so the caller
        # gets the busy message rather than a place in an invisible queue.
        row_id = None
        if self._shared and self._shared.enabled:
            deadline = started + self._queue_wait_s
            while True:
                row_id = await asyncio.to_thread(
                    self._shared.reserve, self._max_concurrent
                )
                if row_id is not None:
                    break
                if time.perf_counter() >= deadline:
                    self._rejected += 1
                    self._sem.release()
                    logger.warning(
                        "inference admission: box at capacity (%d slots) after "
                        "%.1fs; refusing rather than queueing invisibly",
                        self._max_concurrent,
                        self._queue_wait_s,
                    )
                    raise EngineOverloaded(
                        f"no inference capacity within {self._queue_wait_s:.0f}s"
                    )
                await asyncio.sleep(0.2)

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
            if self._shared and row_id is not None:
                await asyncio.to_thread(self._shared.release, row_id)
            self._sem.release()
