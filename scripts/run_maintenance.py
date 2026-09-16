#!/usr/bin/env python3
"""Run snflwr's periodic maintenance WITHOUT a Celery broker.

Retention and parent-notification jobs are defined as Celery tasks and scheduled
ONLY by Celery beat. The home deploy runs no Celery worker, no beat and no Redis,
so none of them ever ran there: the 180-day conversation purge, the 90-day
incident purge, audit-log retention and the parents' daily safety digests
(found 2026-09-16). The backup-cron sidecar calls this once a day.

The task list is read from celery_app.conf.beat_schedule, so the home deploy runs
exactly what enterprise beat runs and cannot drift from it. Each entry executes
locally via Task.apply() (synchronous, no broker). An entry runs only when its
interval has elapsed since its last SUCCESSFUL run (state in
<data>/maintenance_state.json); intervals shorter than a day run daily.

Exit status is non-zero if any due entry failed, so the sidecar log shows it.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The sidecar already runs scripts/backup_database.py itself.
SKIP = {"backup-database"}


def _state_path() -> Path:
    base = os.getenv("SNFLWR_DATA_DIR") or "./data"
    return Path(base) / "maintenance_state.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _interval_seconds(schedule) -> float:
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    run_every = getattr(schedule, "run_every", None)  # celery.schedule
    if isinstance(run_every, timedelta):
        return run_every.total_seconds()
    return 86400.0


def run(now: datetime | None = None, state_path: Path | None = None) -> int:
    import tasks.background_tasks  # noqa: F401  (registers the tasks)
    from utils.celery_config import celery_app
    from utils.logger import get_logger

    log = get_logger("maintenance")
    now = now or datetime.now(timezone.utc)
    state_path = state_path or _state_path()
    state = _load_state(state_path)
    failures = 0

    for name, entry in celery_app.conf.beat_schedule.items():
        if name in SKIP:
            continue
        # Daily granularity: a sub-day interval simply runs every day. A little
        # slack keeps a daily job from slipping a day when the loop wakes early.
        interval = max(_interval_seconds(entry["schedule"]), 86400.0) - 3600.0
        last = state.get(name)
        if last and (now - datetime.fromisoformat(last)).total_seconds() < interval:
            continue
        task = celery_app.tasks.get(entry["task"])
        if task is None:
            log.error(
                "maintenance: %s -> task %s is not registered", name, entry["task"]
            )
            failures += 1
            continue
        try:
            result = task.apply(
                args=entry.get("args", ()), kwargs=entry.get("kwargs", {})
            )
            if result.failed():
                raise result.result  # the task's own exception
        except Exception as exc:  # one job must not stop the others
            log.error("maintenance: %s FAILED: %s", name, exc)
            failures += 1
            continue
        state[name] = now.isoformat()
        log.info("maintenance: %s ok (result=%r)", name, result.result)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
