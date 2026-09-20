"""`slots=N` must mean N turns on the BOX, not N per worker process.

2026-09-20: admission was an `asyncio.Semaphore`, which is per process. The API
runs EIGHT workers (measured with `docker top` on the live container), so a plan
advertising `slots=1` admitted eight concurrent turns onto a card that holds one
model — and up to 40 on the k8s manifest. Same shape as the login rate limit that
advertised 5/min and admitted ~20 across four workers (#272).

These tests run REAL separate processes, because the defect is invisible in one:
a single-process test passes against the broken code. That is the same reason the
log-rotation tests spawn workers instead of threading.
"""

import multiprocessing as mp
import os
from pathlib import Path

import pytest

SLOTS = 1
WORKERS = 4


def _hold(db_path: str, hold_s: float, results, index: int) -> None:
    """Try to take a slot in a fresh process; report admitted/refused."""
    import asyncio

    os.environ["INFERENCE_SLOT_DB"] = db_path
    os.environ["INFERENCE_SLOT_TTL_S"] = "30"

    from core.inference.admission import Admission
    from core.inference.base import EngineOverloaded

    async def run():
        adm = Admission(max_concurrent=SLOTS, queue_wait_s=0.5, max_queue=0)
        try:
            async with adm.slot():
                await asyncio.sleep(hold_s)
            return "admitted"
        except EngineOverloaded:
            return "refused"

    results[index] = asyncio.run(run())


def _run_workers(db_path: str, hold_s: float, n: int) -> list:
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    shared = manager.list([""] * n)
    procs = [
        ctx.Process(target=_hold, args=(db_path, hold_s, shared, i)) for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0, f"worker exited {p.exitcode}"
    return list(shared)


@pytest.mark.skipif(os.name != "posix", reason="spawns real worker processes")
def test_one_slot_admits_one_process_at_a_time(tmp_path: Path):
    """The defect: four processes each took their own slot and all four ran."""
    outcomes = _run_workers(str(tmp_path / "slots.db"), hold_s=2.0, n=WORKERS)
    admitted = outcomes.count("admitted")
    assert admitted == SLOTS, (
        f"{admitted} of {WORKERS} processes were admitted against slots={SLOTS}; "
        f"outcomes={outcomes}. A per-process semaphore gives 4 here."
    )
    assert outcomes.count("refused") == WORKERS - SLOTS


@pytest.mark.skipif(os.name != "posix", reason="spawns real worker processes")
def test_slots_are_released_so_later_turns_are_admitted(tmp_path: Path):
    """A held slot must not leak: the next wave has to get in."""
    db = str(tmp_path / "slots.db")
    first = _run_workers(db, hold_s=0.05, n=1)
    assert first == ["admitted"]
    second = _run_workers(db, hold_s=0.05, n=1)
    assert second == ["admitted"], (
        "the second wave was refused, so the first process never released its "
        "row — one turn would wedge the box until the TTL expired"
    )


@pytest.mark.skipif(os.name != "posix", reason="spawns real worker processes")
def test_memory_setting_keeps_processes_independent():
    """INFERENCE_SLOT_DB=memory is the documented escape hatch (tests, tooling)."""
    outcomes = _run_workers("memory", hold_s=1.0, n=2)
    assert outcomes.count("admitted") == 2, (
        "with sharing disabled each process has its own semaphore, so both "
        f"should be admitted; got {outcomes}"
    )


def test_stale_reservations_are_reaped(tmp_path: Path):
    """A crashed worker cannot release its row; TTL must let the box recover."""
    os.environ["INFERENCE_SLOT_DB"] = str(tmp_path / "slots.db")
    os.environ["INFERENCE_SLOT_TTL_S"] = "0.01"
    import importlib

    import core.inference.admission as adm_mod

    importlib.reload(adm_mod)
    shared = adm_mod._SharedSlots(str(tmp_path / "slots.db"))
    assert shared.enabled
    first = shared.reserve(1)
    assert first is not None and first > 0
    # Nothing released it, but it is older than the TTL, so the box recovers.
    import time

    time.sleep(0.05)
    second = shared.reserve(1)
    assert second is not None and second > 0, (
        "a stale row was not reaped: one crashed worker would wedge admission "
        "until the process restarted"
    )
    os.environ.pop("INFERENCE_SLOT_DB", None)
    os.environ.pop("INFERENCE_SLOT_TTL_S", None)
    importlib.reload(adm_mod)


def test_an_unwritable_store_fails_open():
    """A broken table must degrade to per-process, never refuse to tutor."""
    import core.inference.admission as adm_mod

    shared = adm_mod._SharedSlots("/proc/definitely/not/writable/slots.db")
    assert not shared.enabled
    assert shared.reserve(1) is None or shared.reserve(1) == -1
    shared.release(None)  # must not raise
