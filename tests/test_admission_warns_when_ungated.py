"""Losing the box-wide slot gate must be LOUD.

`Admission` fails open by design: if the shared SQLite table cannot be opened,
capacity falls back to the per-process semaphore rather than refusing to tutor.
That trade is right -- an over-admitted turn is slow, a refused one is a child
told to go away.

What was wrong is that the fallback was SILENT past startup. `_SharedSlots`
warned once when it could not open the file, and then every request quietly took
a local slot and went. `stats()["cross_process"]` carried the truth and nothing
read it.

The consequence is not subtle. The home deploy runs EIGHT uvicorn workers, so
`max_concurrent=1` becomes 1 per worker: eight concurrent turns onto a card that
holds one model. That is the exact defect admission control was built to fix
(see the module docstring's load test), reappearing without a single log line.

So: ERROR, not WARNING -- a degraded-context fallback is a warning; serving 8x
the GPU's capacity is not. Once per process, not per request, because an
oversubscribed box does not need a line per turn.

Raised by a peer session reviewing the same code while building a second
shared-state store on the same fail-open pattern.
"""

import asyncio
import logging

import pytest

from core.inference.admission import Admission


def _run(adm):
    async def go():
        async with adm.slot():
            pass

    asyncio.run(go())


def test_error_is_logged_when_the_box_wide_gate_is_missing(caplog, monkeypatch):
    """No shared table -> ERROR naming the real consequence."""
    monkeypatch.setenv("INFERENCE_SLOT_DB", "/nonexistent/dir/slots.db")
    adm = Admission(max_concurrent=1, queue_wait_s=0.5, max_queue=0)
    with caplog.at_level(logging.ERROR, logger="core.inference.admission"):
        _run(adm)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, (
        "the box-wide gate was skipped SILENTLY. On 8 workers that is 8 "
        "concurrent turns on a 1-slot GPU with nothing in the log."
    )
    assert "PER PROCESS" in errors[0].getMessage()
    assert adm.stats()["cross_process"] is False


def test_the_error_is_logged_once_per_process_not_per_request(caplog, monkeypatch):
    """An oversubscribed box does not need a log line per turn."""
    monkeypatch.setenv("INFERENCE_SLOT_DB", "/nonexistent/dir/slots.db")
    adm = Admission(max_concurrent=1, queue_wait_s=0.5, max_queue=0)
    with caplog.at_level(logging.ERROR, logger="core.inference.admission"):
        for _ in range(5):
            _run(adm)
    errors = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "PER PROCESS" in r.getMessage()
    ]
    assert len(errors) == 1, f"expected exactly one ERROR, got {len(errors)}"


def test_no_error_when_the_gate_is_working(caplog, monkeypatch, tmp_path):
    """The other direction: a working gate must not cry wolf.

    A warning that fires on the healthy path is worse than no warning -- it
    trains everyone to ignore the one that matters.
    """
    monkeypatch.setenv("INFERENCE_SLOT_DB", str(tmp_path / "slots.db"))
    adm = Admission(max_concurrent=1, queue_wait_s=0.5, max_queue=0)
    with caplog.at_level(logging.ERROR, logger="core.inference.admission"):
        _run(adm)
    assert adm.stats()["cross_process"] is True
    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "PER PROCESS" in r.getMessage()
    ], "ERROR fired while the box-wide gate was working"


@pytest.mark.parametrize("db", ["memory", ""])
def test_sharing_disabled_on_purpose_still_reports_it(caplog, monkeypatch, db):
    """`INFERENCE_SLOT_DB=memory` is a deliberate single-process mode.

    It still logs: a tool or test running single-process is fine, but a
    PRODUCTION box that ends up here has silently lost its capacity promise, and
    the log is the only way to tell the two apart afterwards.
    """
    monkeypatch.setenv("INFERENCE_SLOT_DB", db) if db else monkeypatch.setenv(
        "INFERENCE_SLOT_DB", "memory"
    )
    adm = Admission(max_concurrent=1, queue_wait_s=0.5, max_queue=0)
    with caplog.at_level(logging.ERROR, logger="core.inference.admission"):
        _run(adm)
    assert adm.stats()["cross_process"] is False
    assert [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "PER PROCESS" in r.getMessage()
    ]
