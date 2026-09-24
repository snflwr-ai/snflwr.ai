"""The box-wide slot table must HEAL, because every failure here over-admits.

⚠️ `reserve()` fails OPEN by design — if the shared table cannot be consulted,
the turn is admitted rather than refused, so a child never loses their answer to
an infrastructure problem. That is the right trade, and it makes every
unhealthy-table path a silent over-admission. So the table has to come back on
its own.

#324 fixed the startup case (a WAL lock at worker start latched the box to
per-process capacity for the life of the process, measured on CI as two
OVERLAPPING admitted holds). These are the two routes that fix left open, plus
the event-loop cost of its recovery path.
"""

import sqlite3

import pytest

from core.inference import admission


@pytest.fixture
def slots(tmp_path):
    s = admission._SharedSlots(str(tmp_path / "slots.db"))
    assert s.enabled, "init should succeed on a fresh temp path"
    return s


def test_a_vanished_table_heals_instead_of_admitting_forever(slots):
    """⚠️ Route 1: the db file is deleted, truncated or restored over.

    `_connect` stopped issuing CREATE TABLE IF NOT EXISTS, so every reserve
    raised "no such table: inflight", hit the fail-open branch and admitted.
    This stack DOES restore SQLite files
    (`sqlite-restore-over-stale-wal-corrupts`), so this is not hypothetical.
    """
    assert slots.reserve(1) is not None

    con = sqlite3.connect(slots._path)
    con.execute("DROP TABLE inflight")
    con.commit()
    con.close()

    row = slots.reserve(1)
    assert row is not None and row >= 0, (
        "after the table vanished, reserve returned the fail-open sentinel "
        "instead of recreating it — the box is now admitting without a limit"
    )
    # And the limit is actually enforced again, not just a row handed back.
    assert slots.reserve(1) is None, "capacity is not being enforced after healing"


def test_a_failed_reserve_makes_the_fail_open_transient(slots, monkeypatch):
    """⚠️ Route 2: fail-open was PERMANENT.

    reserve() returned the sentinel and left `_ready` True, so the lazy retry
    added for startup contention was unreachable from here and the process
    over-admitted until restart.
    """
    def boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(slots, "_connect", boom)
    assert slots.reserve(1) == -1, "expected the fail-open sentinel"
    assert slots._ready is False, (
        "reserve failed and left the table marked healthy — the recovery path "
        "will never run and this process over-admits until it restarts"
    )

    # Recovery: once the real connection works again, `enabled` re-inits.
    monkeypatch.undo()
    slots._next_retry = 0.0
    assert slots.enabled is True
    assert slots.reserve(1) is not None


def test_the_lazy_retry_cannot_stall_the_event_loop(slots, monkeypatch):
    """`enabled` is called on the EVENT LOOP, unlike reserve/release which go
    through asyncio.to_thread. With the init timeout of 5s it could block every
    request for up to 5s during exactly the contention it exists to recover
    from."""
    seen = {}

    def fake(timeout=5.0):
        seen["timeout"] = timeout
        return False

    slots._ready = False
    slots._next_retry = 0.0
    monkeypatch.setattr(slots, "_try_init", fake)
    _ = slots.enabled
    assert seen["timeout"] <= 0.5, (
        f"the lazy retry connects with timeout={seen['timeout']}s on the event "
        f"loop; it retries again shortly anyway, so it must be short"
    )


def test_reserve_still_refuses_when_the_box_is_full(slots):
    """Guard: none of the healing above may weaken the actual limit."""
    first = slots.reserve(1)
    assert first is not None
    assert slots.reserve(1) is None
    slots.release(first)
    assert slots.reserve(1) is not None
