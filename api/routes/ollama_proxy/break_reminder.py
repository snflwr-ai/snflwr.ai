"""Three-hour break reminder for the Open WebUI proxy path.

WHY THIS EXISTS
---------------
California SB 243 (Bus. & Prof. Code §22602(c)(2)) requires an operator, for a
user it knows is a minor, to

    "Provide by default a clear and conspicuous notification to the user at
    least every three hours for continuing companion chatbot interactions that
    reminds the user to take a break and that the companion chatbot is
    artificially generated and not human."

Whether snflwr is a "companion chatbot" under that statute is arguable; the
posture is to comply regardless (the statute carries a private right of action).
Every snflwr student is a known minor. The persistent, non-dismissible
disclosure banner (``scripts/owui_connect.py``) says "this is AI" but never
says "take a break", and the proxy path has no session cap (the native route
stops at ``max_session_hours``; the proxy does not). So the reminder is
enforced here, per child.

HOW
---
State per ``profile_id``: when the current continuous stretch started being
counted from (``last``) and when the child was last seen (``seen``). A gap
longer than ``idle_reset_seconds`` is itself a break and starts a new stretch.
Session start is not a reminder turn — the banner is on screen then. Once
``interval_seconds`` have passed since the stretch start or the last reminder,
the next reply carries the reminder at its TOP, where it is conspicuous.

Shared cache (Redis) when available so multi-worker deploys agree; any cache
error falls back to in-process state rather than silencing the reminder.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

STATUTORY_INTERVAL_SECONDS = 3 * 3600
DEFAULT_IDLE_RESET_SECONDS = 30 * 60

_NAMESPACE = "snflwr_breakreminder"

REMINDER_TEXT = (
    "⏰ Break reminder: you've been chatting for a while, so this is a good time "
    "to stand up, stretch, or get a drink of water. Remember, I'm an AI, not a "
    "human."
)


def _interval_from_env() -> int:
    """Interval in seconds, never longer than the statutory three hours."""
    try:
        value = int(
            os.getenv("BREAK_REMINDER_INTERVAL_S", str(STATUTORY_INTERVAL_SECONDS))
        )
    except ValueError:
        return STATUTORY_INTERVAL_SECONDS
    if value <= 0:
        return STATUTORY_INTERVAL_SECONDS
    return min(value, STATUTORY_INTERVAL_SECONDS)


def with_reminder(text: str) -> str:
    """Return ``text`` with the reminder placed first."""
    return f"{REMINDER_TEXT}\n\n{text}" if text else REMINDER_TEXT


class BreakReminder:
    """Decides, per child, whether this reply must carry the break reminder."""

    def __init__(
        self,
        interval_seconds: Optional[int] = None,
        idle_reset_seconds: int = DEFAULT_IDLE_RESET_SECONDS,
        cache: Any = None,
        use_shared: bool = True,
    ):
        self.interval_seconds = (
            interval_seconds if interval_seconds is not None else _interval_from_env()
        )
        self.idle_reset_seconds = idle_reset_seconds
        self._cache = cache
        # False = in-process state only (tests; never reach a live Redis).
        self._use_shared = use_shared
        self._local: Dict[str, Dict[str, float]] = {}
        self._now = time.time

    # -- storage -----------------------------------------------------------

    def _shared(self):
        if self._cache is not None:
            return self._cache
        if not self._use_shared:
            return None
        try:
            from utils.cache import cache as shared

            if getattr(shared, "enabled", False) and not getattr(
                shared, "is_degraded", False
            ):
                return shared
        except Exception:  # pragma: no cover - import guard
            pass
        # No Redis: the SQLite file every worker shares, so the three-hour clock
        # is per CHILD, not per worker process.
        from utils import shared_state

        return shared_state.get_shared_state()

    def _load(self, profile_id: str) -> Optional[Dict[str, float]]:
        backend = self._shared()
        if backend is not None:
            try:
                raw = backend.get(profile_id, namespace=_NAMESPACE)
                if raw is None:
                    return self._local.get(profile_id)
                return json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except Exception as exc:
                logger.debug("Break-reminder cache read failed: %s", exc)
        return self._local.get(profile_id)

    def _save(self, profile_id: str, state: Dict[str, float]) -> None:
        # Always keep a local copy: if the shared cache fails mid-stretch the
        # clock keeps running instead of restarting (which would delay the
        # reminder past three hours).
        self._local[profile_id] = state
        if len(self._local) > 4096:
            cutoff = self._now() - self.idle_reset_seconds
            for key in [k for k, v in self._local.items() if v["seen"] < cutoff]:
                del self._local[key]
        backend = self._shared()
        if backend is not None:
            try:
                backend.set(
                    profile_id,
                    json.dumps(state),
                    self.interval_seconds + self.idle_reset_seconds,
                    namespace=_NAMESPACE,
                )
            except Exception as exc:
                logger.debug("Break-reminder cache write failed: %s", exc)

    # -- public API --------------------------------------------------------

    def due(self, profile_id: str) -> bool:
        """Record activity for this child; True if this reply must carry the
        reminder. Never raises."""
        try:
            now = self._now()
            state = self._load(profile_id)
            if state is None or now - state.get("seen", 0) > self.idle_reset_seconds:
                self._save(profile_id, {"last": now, "seen": now})
                return False
            if now - state.get("last", now) >= self.interval_seconds:
                self._save(profile_id, {"last": now, "seen": now})
                return True
            self._save(profile_id, {"last": state["last"], "seen": now})
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Break-reminder check failed for a profile: %s", exc)
            return False


# Module-level instance, mirroring history_ledger.
break_reminder = BreakReminder()


def due(profile_id: str) -> bool:
    """See :meth:`BreakReminder.due`. Resolves the singleton at call time so
    tests can swap it."""
    return break_reminder.due(profile_id)
