"""The box-wide slot table must survive a lock at startup.

Several workers open the slot file at the same instant. Switching a fresh file to
WAL takes an exclusive lock and SQLite can answer "database is locked" at once.
The init used to try once and then run PER PROCESS for the life of the worker,
so "slots=1" became up to 8. Caught on CI 2026-09-24: "shared slot table
unavailable (database is locked)" followed by two OVERLAPPING admitted holds.
"""

import sqlite3

import pytest

from core.inference import admission as adm


class _FlakyConnect:
    """sqlite3.connect that raises 'database is locked' for the first N calls."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0
        self.real = sqlite3.connect

    def __call__(self, *a, **k):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise sqlite3.OperationalError("database is locked")
        return self.real(*a, **k)


def test_a_transient_lock_at_startup_does_not_disable_the_table(tmp_path, monkeypatch):
    flaky = _FlakyConnect(fail_times=3)
    monkeypatch.setattr(adm.sqlite3, "connect", flaky)
    monkeypatch.setattr(adm.time, "sleep", lambda s: None)
    slots = adm._SharedSlots(str(tmp_path / "slots.db"))
    assert slots.enabled, "gave up on a lock that cleared on the 4th try"
    assert slots.reserve(1) is not None


def test_a_table_that_never_opened_at_init_is_retried_later(tmp_path, monkeypatch):
    flaky = _FlakyConnect(fail_times=adm._SharedSlots._INIT_ATTEMPTS)
    monkeypatch.setattr(adm.sqlite3, "connect", flaky)
    monkeypatch.setattr(adm.time, "sleep", lambda s: None)
    slots = adm._SharedSlots(str(tmp_path / "slots.db"))
    assert not slots._ready
    slots._next_retry = 0.0  # the retry interval has elapsed
    assert slots.enabled, "never retried: the process would stay per-worker forever"


def test_reserve_does_not_reissue_the_journal_mode_pragma(tmp_path, monkeypatch):
    """A lock on that PRAGMA inside reserve() used to fail OPEN (admit)."""
    slots = adm._SharedSlots(str(tmp_path / "slots.db"))
    seen = []
    real = sqlite3.connect

    class _Spy:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *a):
            seen.append(sql)
            return self._con.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._con, name)

    monkeypatch.setattr(adm.sqlite3, "connect", lambda *a, **k: _Spy(real(*a, **k)))
    assert slots.reserve(1) is not None
    assert not any("journal_mode" in q for q in seen)


@pytest.mark.parametrize("fail_times", [0, 1, 7])
def test_one_slot_is_still_one_slot_after_a_flaky_start(
    tmp_path, monkeypatch, fail_times
):
    monkeypatch.setattr(adm.sqlite3, "connect", _FlakyConnect(fail_times))
    monkeypatch.setattr(adm.time, "sleep", lambda s: None)
    slots = adm._SharedSlots(str(tmp_path / "slots.db"))
    monkeypatch.setattr(adm.sqlite3, "connect", sqlite3.connect)
    first = slots.reserve(1)
    assert first is not None
    assert slots.reserve(1) is None, "a second holder was admitted against one slot"
