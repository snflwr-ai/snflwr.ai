"""Cross-process state for deploys that run several workers without Redis.

Found 2026-09-24 on the home deploy: snflwr-api runs 8 worker processes with
REDIS_ENABLED=false, so the history ledger and the break reminder fell back to
per-process dicts. A child's turn 2 landed on a different worker 7 times in 8,
the ledger did not recognise turn 1, and the tutor forgot it ("Dropped 2
unrecognized history message(s)" on a continuous two-turn chat). Each
SharedState instance below stands in for one worker; they share only the file.
"""

import os
import stat

import pytest

from utils import shared_state as ss


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "shared_state.db")


def _worker(db, clock=None):
    s = ss.SharedState(db, secret=b"test-secret")
    if clock is not None:
        s._now = clock
    return s


def test_value_written_by_one_worker_is_seen_by_another(db):
    a, b = _worker(db), _worker(db)
    assert a.set("k", "v", 60, namespace="ns") is True
    assert b.get("k", namespace="ns") == "v"
    assert b.exists("k", namespace="ns") is True


def test_namespaces_are_separate(db):
    a = _worker(db)
    a.set("k", "v", 60, namespace="one")
    assert a.get("k", namespace="two") is None


def test_expired_values_are_gone(db):
    t = [1000.0]
    a = _worker(db, clock=lambda: t[0])
    a.set("k", "v", 10, namespace="ns")
    t[0] += 11
    assert a.get("k", namespace="ns") is None
    assert a.exists("k", namespace="ns") is False


def test_file_holds_no_raw_keys(db):
    """Keys carry a profile id and a hash of the child's message; stored keyed
    with the server secret so the file alone cannot confirm a guessed message."""
    a = _worker(db)
    a.set("profile-123:deadbeef", "1", 60, namespace="ns")
    raw = open(db, "rb").read()
    assert b"profile-123" not in raw and b"deadbeef" not in raw


def test_file_is_owner_only(db):
    _worker(db).set("k", "v", 60, namespace="ns")
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600


def test_memory_setting_disables_it(monkeypatch):
    monkeypatch.setenv("SNFLWR_SHARED_STATE_DB", "memory")
    assert ss._db_path() is None
    assert ss.SharedState(None).enabled is False


def test_history_survives_a_worker_switch(db):
    """The regression: turn 1 served by worker A, turn 2 handled by worker B."""
    from api.routes.ollama_proxy import history_ledger as hl

    worker_a = hl.HistoryLedger(ttl_seconds=3600, cache=_worker(db))
    worker_b = hl.HistoryLedger(ttl_seconds=3600, cache=_worker(db))
    turn1 = {"role": "user", "content": "I got 17 on problem 3"}
    reply = {"role": "assistant", "content": "Let's check it together."}
    worker_a.record_turn("p1", turn1, reply["content"])

    turn2 = [turn1, reply, {"role": "user", "content": "what did I get?"}]
    assert worker_b.filter_history("p1", turn2) == turn2


def test_break_reminder_clock_survives_a_worker_switch(db):
    from api.routes.ollama_proxy import break_reminder as br

    t = [1_000_000.0]
    workers = []
    for _ in range(8):
        r = br.BreakReminder(
            interval_seconds=3 * 3600, idle_reset_seconds=1800, cache=_worker(db)
        )
        r._now = lambda: t[0]
        workers.append(r)
    fired = []
    start = t[0]
    for i in range(20):  # a turn every 10 minutes, each on a different worker
        if workers[(i * 3) % 8].due("p1"):
            fired.append(t[0] - start)
        t[0] += 600
    assert fired == [3 * 3600]


def test_default_backends_use_shared_state_when_redis_is_off(db, monkeypatch):
    """With Redis disabled, both components pick the shared file, not a dict."""
    from api.routes.ollama_proxy import break_reminder as br
    from api.routes.ollama_proxy import history_ledger as hl

    shared = _worker(db)
    monkeypatch.setattr(ss, "get_shared_state", lambda: shared)
    assert hl.HistoryLedger(ttl_seconds=60)._redis() is shared
    assert br.BreakReminder()._shared() is shared
