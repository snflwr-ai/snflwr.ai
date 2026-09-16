"""Retention and parent-digest jobs must actually run on the home deploy.

They were Celery tasks scheduled only by Celery beat, and the home deploy runs no
beat, no worker and no Redis -- so the 180-day conversation purge, incident and
audit-log retention, and parents' daily safety digests never ran (2026-09-16).
scripts/run_maintenance.py runs the beat schedule without a broker; the
backup-cron sidecar calls it daily.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 16, 3, 5, tzinfo=timezone.utc)


def _fake_tasks(fail=()):
    """Stand-ins for every task in the beat schedule, recording calls."""
    from utils.celery_config import celery_app

    calls, tasks = [], {}
    for name, entry in celery_app.conf.beat_schedule.items():
        t = MagicMock()
        res = MagicMock()
        res.failed.return_value = name in fail
        res.result = RuntimeError(f"{name} broke") if name in fail else 0

        def _apply(*a, _n=name, _r=res, **k):
            calls.append(_n)
            return _r

        t.apply.side_effect = _apply
        tasks[entry["task"]] = t
    return calls, tasks


def _run(tmp_path, tasks, now=NOW):
    from scripts import run_maintenance

    with patch("utils.celery_config.celery_app.tasks", tasks):
        return run_maintenance.run(now=now, state_path=tmp_path / "state.json")


def test_runs_every_scheduled_job_except_the_sidecars_own_backup(tmp_path):
    from utils.celery_config import celery_app

    calls, tasks = _fake_tasks()
    assert _run(tmp_path, tasks) == 0
    expected = set(celery_app.conf.beat_schedule) - {"backup-database"}
    assert set(calls) == expected
    # the jobs this fix exists for
    assert {"cleanup-old-messages", "send-daily-safety-digests"} <= set(calls)


def test_weekly_jobs_wait_a_week_daily_jobs_run_next_day(tmp_path):
    calls, tasks = _fake_tasks()
    _run(tmp_path, tasks)
    calls.clear()
    _run(tmp_path, tasks, now=NOW + timedelta(days=1))
    assert "cleanup-old-messages" in calls
    assert "vacuum-database" not in calls and "cleanup-analytics" not in calls
    calls.clear()
    _run(tmp_path, tasks, now=NOW + timedelta(days=7))
    assert "vacuum-database" in calls


def test_one_failure_does_not_stop_the_rest_and_is_retried(tmp_path):
    calls, tasks = _fake_tasks(fail={"cleanup-old-sessions"})
    assert _run(tmp_path, tasks) == 1
    assert "send-daily-safety-digests" in calls  # later jobs still ran
    calls.clear()
    _run(tmp_path, tasks, now=NOW + timedelta(hours=2))
    # the failed job is not marked done, so it runs again; succeeded ones do not
    assert calls == ["cleanup-old-sessions"]


def test_retention_sql_deletes_old_messages_and_keeps_recent(tmp_path):
    """The purge compares ISO 'T' timestamps with sqlite datetime(): prove it."""
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute("CREATE TABLE messages (id TEXT, timestamp TEXT)")
    now = datetime.now(timezone.utc)
    rows = {
        "old": (now - timedelta(days=200)).isoformat(),
        "edge_old": (now - timedelta(days=182)).isoformat(),
        "recent": (now - timedelta(days=10)).isoformat(),
        "today": now.isoformat(),
    }
    db.executemany("INSERT INTO messages VALUES (?, ?)", rows.items())
    db.commit()

    fake = MagicMock()
    fake.execute_write.side_effect = lambda q, p=(): db.execute(q, p).rowcount

    from tasks import background_tasks

    with patch.object(background_tasks, "db_manager", fake):
        deleted = background_tasks.cleanup_old_messages.apply().result
    db.commit()
    left = {r[0] for r in db.execute("SELECT id FROM messages")}
    assert deleted == 2
    assert left == {"recent", "today"}


def test_backup_cron_sidecar_runs_maintenance_after_the_backup():
    compose = (REPO / "docker" / "compose" / "docker-compose.home.yml").read_text()
    sidecar = compose[compose.index("  backup-cron:") :]
    assert "scripts/run_maintenance.py" in sidecar
    assert sidecar.index("backup_database.py backup") < sidecar.index(
        "run_maintenance.py"
    )


def test_analytics_retention_uses_the_real_date_column(tmp_path):
    """cleanup_analytics referenced a `timestamp` column learning_analytics does
    not have, so 730-day analytics retention failed on every deploy, Celery
    included. Found by running the maintenance runner against a real DB copy."""
    db = sqlite3.connect(tmp_path / "a.db")
    db.execute(
        "CREATE TABLE learning_analytics (analytics_id TEXT, profile_id TEXT, "
        "date TEXT NOT NULL, subject_area TEXT)"
    )
    today = datetime.now(timezone.utc).date()
    rows = [
        ("old", "p", (today - timedelta(days=800)).isoformat(), "math"),
        ("recent", "p", (today - timedelta(days=30)).isoformat(), "math"),
    ]
    db.executemany("INSERT INTO learning_analytics VALUES (?, ?, ?, ?)", rows)
    db.commit()
    fake = MagicMock()
    fake.execute_write.side_effect = lambda q, p=(): db.execute(q, p).rowcount

    from tasks import background_tasks

    with (
        patch("storage.database.db_manager", fake),
        patch.object(background_tasks, "db_manager", fake),
    ):
        result = background_tasks.cleanup_analytics.apply()
    db.commit()
    assert not result.failed(), result.result
    assert {
        r[0] for r in db.execute("SELECT analytics_id FROM learning_analytics")
    } == {"recent"}


def test_task_failure_handler_reaches_its_alerting():
    """extra={"args": ...} collided with LogRecord.args and raised KeyError, so the
    handler died before dead-letter routing and alerting. And with no broker the
    dead-letter .delay() raised kombu's OperationalError, which was not caught, so
    the alert was skipped anyway."""
    import logging

    from kombu.exceptions import OperationalError

    from utils import celery_config

    sender = MagicMock()
    sender.name = "tasks.background_tasks.cleanup_analytics"
    real = logging.getLogger("snflwr.test.celery_failure")  # real reserved-key check
    real.addHandler(logging.NullHandler())
    dead_letter = MagicMock()
    dead_letter.delay.side_effect = OperationalError("no broker")
    with (
        patch.object(celery_config, "logger", real),
        patch.object(celery_config, "store_failed_task", dead_letter),
        patch.object(celery_config, "_send_failure_alert") as alert,
        patch.dict(
            celery_config._failed_task_counts,
            {sender.name: celery_config._ALERT_THRESHOLD},
        ),
    ):
        celery_config.handle_task_failure(
            sender=sender,
            task_id="t1",
            exception=RuntimeError("boom"),
            args=("a",),
            kwargs={"k": 1},
        )
    dead_letter.delay.assert_called_once()
    alert.assert_called_once()
