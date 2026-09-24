"""Small cross-process key/value store (SQLite, with TTL) for when Redis is off.

WHY
---
Per-child state that must be the same in every worker (the history ledger, the
break-reminder clock) used Redis when enabled and an in-process dict otherwise.
The home deploy runs 8 uvicorn workers with REDIS_ENABLED=false, so each worker
had its own dict: a child's next turn usually landed on a different worker and
the ledger dropped the conversation (found 2026-09-24). Admission control hit
the same wall earlier and moved its slots to a shared SQLite file
(``core/inference/admission.py``); this generalises that pattern.

The interface mirrors ``utils.cache`` (``get`` / ``set`` / ``exists`` with a
namespace), so callers can use either backend unchanged.

PRIVACY
-------
Keys contain a profile id and a hash of a child's message. They are stored as
HMAC-SHA256 under the server's JWT secret, so the file on its own cannot be
used to confirm a guessed message. The file is created owner-only (0600).

ERRORS
------
Methods RAISE on database errors. Callers decide the failure direction (the
ledger fails closed; the reminder falls back to local state).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_PRUNE_EVERY = 256  # writes between sweeps of expired rows


def _db_path() -> Optional[str]:
    """Path of the shared file, or None to disable (``SNFLWR_SHARED_STATE_DB=memory``)."""
    override = os.getenv("SNFLWR_SHARED_STATE_DB")
    if override:
        return None if override == "memory" else override
    try:
        from config import system_config  # noqa: PLC0415 - avoid an import cycle

        return str(system_config.APP_DATA_DIR / "shared_state.db")
    except Exception:  # noqa: BLE001 - tooling contexts have no config
        return None


def _secret() -> bytes:
    try:
        from config import system_config  # noqa: PLC0415

        value = system_config.JWT_SECRET_KEY
        if value:
            return str(value).encode()
    except Exception:  # noqa: BLE001
        pass
    return b"snflwr-shared-state"


class SharedState:
    """Key/value with TTL in one SQLite file shared by every worker process."""

    def __init__(self, path: Optional[str], secret: Optional[bytes] = None):
        self._path = path
        self._secret = secret if secret is not None else _secret()
        self._now = time.time
        self._writes = 0
        self._ready = False
        if path:
            try:
                self._connect().close()
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                self._ready = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("shared state unavailable at %s (%s)", path, exc)

    @property
    def enabled(self) -> bool:
        return self._ready

    def _connect(self):
        con = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "  k TEXT PRIMARY KEY,"
            "  v TEXT NOT NULL,"
            "  expires_at REAL NOT NULL)"
        )
        return con

    def _k(self, key: str, namespace: str) -> str:
        return hmac.new(
            self._secret, f"{namespace}\0{key}".encode(), hashlib.sha256
        ).hexdigest()

    def get(self, key: str, namespace: str = "snflwr") -> Optional[str]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT v FROM kv WHERE k = ? AND expires_at > ?",
                (self._k(key, namespace), self._now()),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def exists(self, key: str, namespace: str = "snflwr") -> bool:
        return self.get(key, namespace=namespace) is not None

    def set(
        self, key: str, value: Any, ttl: Optional[int] = None, namespace: str = "snflwr"
    ) -> bool:
        now = self._now()
        expires = now + (ttl if ttl else 3600)
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO kv (k, v, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v, "
                "expires_at = excluded.expires_at",
                (self._k(key, namespace), str(value), expires),
            )
            self._writes += 1
            if self._writes % _PRUNE_EVERY == 0:
                con.execute("DELETE FROM kv WHERE expires_at <= ?", (now,))
            return True
        finally:
            con.close()


_instance: Optional[SharedState] = None


def get_shared_state() -> Optional[SharedState]:
    """The process-wide store, or None when disabled/unavailable."""
    global _instance
    if _instance is None:
        _instance = SharedState(_db_path())
    return _instance if _instance.enabled else None
