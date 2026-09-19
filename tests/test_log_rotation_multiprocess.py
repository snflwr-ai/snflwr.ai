"""Log rotation must survive several processes writing the same files.

api.server runs 4 worker processes, and the backup sidecar shares the data volume.
Each opened its own stdlib RotatingFileHandler on snflwr.log / snflwr.json.log /
errors.log. That handler is not process-safe: on 2026-09-16 the live box showed
snflwr.json.log.1-.5 all exactly 82,349 bytes with one timestamp (cascading
rollovers overwriting history), snflwr.log.1-.5 gone entirely, and repeated
FileNotFoundError tracebacks from doRollover. Operational history was being
silently destroyed.
"""

import multiprocessing as mp
import os
from pathlib import Path

import pytest

WORKERS = 4
LINES = 400
MAX_BYTES = 4096


def _writer(path: str, worker: int, handler_kind: str, errors_path: str, start) -> None:
    import logging
    import logging.handlers

    if handler_kind == "stdlib":
        h = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=500, encoding="utf-8"
        )
    else:
        from utils.logger import ProcessSafeRotatingFileHandler

        h = ProcessSafeRotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=500, encoding="utf-8"
        )

    def _record_error(record):  # count failures instead of printing them
        with open(errors_path, "a") as f:
            f.write("x\n")

    h.handleError = _record_error  # type: ignore[method-assign]
    log = logging.getLogger(f"w{worker}")
    log.propagate = False
    log.addHandler(h)
    # Every writer waits here until all are ready. Under "spawn" each child
    # spends 100ms+ importing, so without this the writers could finish nearly
    # one after another and never collide; the stdlib control then lost no lines
    # and failed ~40% of runs (CI, 2026-09-18). The race has to actually happen.
    start.wait(60)
    for i in range(LINES):
        log.warning("worker=%d line=%04d %s", worker, i, "p" * 40)
    h.close()


def _run(tmp_path: Path, kind: str):
    path = tmp_path / "app.log"
    errors = tmp_path / "errors.txt"
    ctx = mp.get_context("spawn")
    start = ctx.Barrier(WORKERS)
    procs = [
        ctx.Process(target=_writer, args=(str(path), w, kind, str(errors), start))
        for w in range(WORKERS)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    lines = []
    for f in tmp_path.glob("app.log*"):
        if f.suffix == ".lock":
            continue
        lines += [
            l for l in f.read_text(encoding="utf-8").splitlines() if "worker=" in l
        ]
    n_errors = len(errors.read_text().splitlines()) if errors.exists() else 0
    return lines, n_errors


def test_no_line_is_lost_across_concurrent_rotations(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))
    lines, n_errors = _run(tmp_path, "safe")
    assert n_errors == 0
    assert len(lines) == WORKERS * LINES
    assert len(set(lines)) == WORKERS * LINES  # nothing duplicated either
    # and it actually rotated, many times
    assert len(list(tmp_path.glob("app.log.*"))) > 10


@pytest.mark.skipif(os.name != "posix", reason="demonstrates the POSIX race")
def test_stdlib_handler_loses_lines_under_the_same_load(tmp_path):
    """The control: if this ever passes, the test above no longer proves anything.

    A race is probabilistic, so a single clean run is not evidence the stdlib
    handler is safe. It gets three independent attempts; one lossy run is enough.
    """
    outcomes = []
    for attempt in range(3):
        run_dir = tmp_path / f"attempt{attempt}"
        run_dir.mkdir()
        lines, n_errors = _run(run_dir, "stdlib")
        outcomes.append((len(lines), n_errors))
        if len(lines) < WORKERS * LINES or n_errors > 0:
            return
    pytest.fail(f"stdlib handler lost nothing in 3 runs (lines, errors): {outcomes}")
