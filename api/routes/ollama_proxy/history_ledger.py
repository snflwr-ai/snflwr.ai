"""Cross-session history ledger for the Open WebUI proxy path.

WHY THIS EXISTS
---------------
Open WebUI stores chats in its OWN database and resends a reopened
conversation's full message array with every request. The proxy clipped that
array by TURN COUNT only (``safety_config.max_conversation_turns_for_age``),
never by age — so a student reopening yesterday's chat fed prior-session content
straight back into generation.

NY S9051B §1801 lists as an unsafe AI companion feature the use of information

    "CONCERNING THE USER'S MENTAL OR PHYSICAL HEALTH OR WELL-BEING, OR MATTERS
    PERSONAL TO THE USER, ACQUIRED FROM THE USER MORE THAN TWELVE HOURS
    PREVIOUSLY OR IN ANY PREVIOUS USER SESSION"

The native route (``api/routes/chat.py``) already satisfies this structurally:
it builds history from ``_get_or_create_conversation_id(session_id, …)``, so
history cannot outlive a session, and sessions cap at ``max_session_hours`` (4).
The proxy had no equivalent, and the proxy is the path students actually use.

WHY A CONTENT LEDGER RATHER THAN A SESSION ANCHOR
-------------------------------------------------
The proxy has nothing to anchor on. Its ``AuthSession`` is the parent /
internal-service credential, not a student tutoring session, so
``sessions.started_at`` and ``questions_asked`` are unavailable here. Open WebUI
forwards only ``X-OpenWebUI-User-Id`` / ``-Role`` — no chat id — and the
messages carry no timestamps. So the ledger keys on ``profile_id`` and remembers
the content HASHES of messages this proxy has actually served inside the TTL
window. Anything the client sends that the ledger has not seen recently is
foreign and gets dropped.

Enforcing this at the proxy rather than by configuring Open WebUI (temporary
chats, history off) is deliberate: OWUI config is admin-flippable and invisible
to the test suite, and this codebase's guarantee is that safety "cannot be
bypassed from the frontend".

FAIL-CLOSED
-----------
Every error path yields LESS context, never more. A backend outage, a cold
worker, or an unparseable message means the model sees only the current turn.
The cost is an occasionally amnesiac tutor; the alternative is leaking a prior
session's personal content into a prompt.

NOTE FOR OPERATORS: past the TTL the model legitimately will not recall earlier
turns even though Open WebUI still renders the old bubbles client-side. That is
correct behavior, not a bug — and it is what makes the persona's "I don't keep
anything between sessions" true.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Union

from utils.logger import get_logger

logger = get_logger(__name__)

# Mirrors ``SESSION_CONFIG["max_session_hours"]`` (4), which is the window the
# native path already enforces, and sits well under S9051B's 12-hour ceiling.
DEFAULT_TTL_SECONDS = 4 * 3600

_NAMESPACE = "snflwr_histledger"


def _ttl_from_env() -> int:
    """TTL in seconds, clamped to the statutory 12-hour ceiling."""
    try:
        value = int(os.getenv("HISTORY_LEDGER_TTL_S", str(DEFAULT_TTL_SECONDS)))
    except ValueError:
        return DEFAULT_TTL_SECONDS
    if value <= 0:
        return DEFAULT_TTL_SECONDS
    return min(value, 12 * 3600)


def message_hash(message: Dict[str, Any]) -> str:
    """Stable hash of (role, content) for one chat message.

    Content may be a plain string or, for multimodal turns, a list of parts —
    hence canonical JSON rather than ``str()``. Role is part of the digest so a
    student cannot replay the model's own words back as a user turn.

    Leading/trailing whitespace on string content is normalized away. The ledger
    depends on Open WebUI resending what we returned, and OWUI reassembles a
    streamed response itself, so surrounding whitespace is not guaranteed to
    survive the round trip byte-for-byte. Two messages differing only in
    surrounding whitespace are the same message; internal differences still
    change the digest, so this does not weaken the integrity check.
    """
    role = str(message.get("role", ""))
    content = message.get("content", "")
    if isinstance(content, str):
        content = content.strip()
    try:
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(content)
    return hashlib.sha256(f"{role}\x00{payload}".encode("utf-8")).hexdigest()


class HistoryLedger:
    """Remembers which messages this proxy served, per child, for a TTL window.

    Backed by Redis when available (so the window is shared across workers) and
    by an in-process dict otherwise. The in-process fallback is per-worker: a
    multi-worker deployment without Redis will occasionally drop history that
    another worker served. That degrades context, never safety.
    """

    def __init__(self, ttl_seconds: Optional[int] = None, cache: Any = None):
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else _ttl_from_env()
        self._cache = cache
        self._local: Dict[str, float] = {}
        self._now = time.time
        self._warned = False

    # -- storage -----------------------------------------------------------

    def _redis(self):
        """The shared cache when it is usable, else None (use local storage)."""
        if self._cache is not None:
            return self._cache
        try:
            from utils.cache import cache as shared

            if getattr(shared, "enabled", False) and not getattr(
                shared, "is_degraded", False
            ):
                return shared
        except Exception:  # pragma: no cover - import guard
            pass
        return None

    def _key(self, profile_id: str, digest: str) -> str:
        return f"{profile_id}:{digest}"

    def _seen(self, profile_id: str, digest: str) -> bool:
        """True if this exact message was served for this child inside the TTL.

        Raises on backend failure so ``filter_history`` can fail closed for the
        whole request rather than silently treating errors as "not seen" and
        producing a half-filtered list.
        """
        backend = self._redis()
        if backend is not None:
            return bool(backend.exists(self._key(profile_id, digest), _NAMESPACE))

        self._prune()
        expiry = self._local.get(self._key(profile_id, digest))
        return expiry is not None and expiry > self._now()

    def _remember(self, profile_id: str, digest: str) -> None:
        backend = self._redis()
        if backend is not None:
            backend.set(self._key(profile_id, digest), 1, self.ttl_seconds, _NAMESPACE)
            return
        self._local[self._key(profile_id, digest)] = self._now() + self.ttl_seconds

    def _prune(self) -> None:
        """Drop expired local entries. Cheap and bounded — the local map only
        grows to (turns x children) inside one TTL window."""
        if len(self._local) < 2048:
            return
        now = self._now()
        for key in [k for k, exp in self._local.items() if exp <= now]:
            del self._local[key]

    # -- public API --------------------------------------------------------

    def filter_history(
        self, profile_id: str, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Return only the messages safe to forward for *profile_id*.

        The final message is always kept — it is what the student just typed.
        Everything before it is validated by walking BACKWARDS and stopping at
        the first message the ledger does not recognize. Stopping (rather than
        skipping) matters: it means a client-edited or injected message drops
        everything older than itself instead of riding along inside an otherwise
        known run.
        """
        if not messages:
            return []

        current = messages[-1]
        try:
            kept: List[Dict[str, Any]] = []
            for message in reversed(messages[:-1]):
                if not self._seen(profile_id, message_hash(message)):
                    break
                kept.append(message)
            kept.reverse()
            return kept + [current]
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "History ledger unavailable (%s) — forwarding current turn "
                    "only. Failing closed: prior-session content must not reach "
                    "the model.",
                    exc,
                )
            return [current]

    def record_turn(
        self,
        profile_id: str,
        user_message: Dict[str, Any],
        assistant: Union[str, Dict[str, Any], None],
    ) -> None:
        """Record a completed exchange so the next request can replay it.

        ``assistant`` may be the raw reply text (what the proxy has on hand) or
        a message dict. Best-effort: a storage failure must never break the
        student's response, only shorten the next request's context.
        """
        try:
            self._remember(profile_id, message_hash(user_message))
            if assistant is None:
                return
            if isinstance(assistant, str):
                assistant = {"role": "assistant", "content": assistant}
            self._remember(profile_id, message_hash(assistant))
        except Exception as exc:
            logger.debug("History ledger write failed for %s: %s", profile_id, exc)


# Module-level instance, mirroring incident_logger / rate_limiter.
history_ledger = HistoryLedger()


# Module-level delegates so callers can use ``history_ledger.filter_history(...)``
# the way they already use ``guards.rate_limit_block_reason(...)``. Resolving the
# singleton at CALL time (not import time) is what lets a test swap it out —
# ``from ... import history_ledger`` in a consumer would otherwise capture the
# instance permanently and silently ignore the replacement.
def filter_history(
    profile_id: str, messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """See :meth:`HistoryLedger.filter_history`."""
    return history_ledger.filter_history(profile_id, messages)


def record_turn(
    profile_id: str,
    user_message: Dict[str, Any],
    assistant: Union[str, Dict[str, Any], None],
) -> None:
    """See :meth:`HistoryLedger.record_turn`."""
    history_ledger.record_turn(profile_id, user_message, assistant)
