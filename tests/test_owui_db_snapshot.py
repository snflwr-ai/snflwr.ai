"""The Open WebUI upgrade rollback must not corrupt webui.db.

Reproduces the WAL hazard that left the home stack's webui.db malformed: a
bare-file backup restored over a newer image's -wal/-shm. Uses real SQLite,
not mocks -- the failure only exists in SQLite's WAL replay.
"""

import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "owui_db_snapshot",
    Path(__file__).resolve().parent.parent / "scripts" / "owui_db_snapshot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
snap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(snap)


def _count(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT count(*) FROM chat").fetchone()[0]
    finally:
        conn.close()


def _live_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE chat(id INTEGER PRIMARY KEY, title TEXT, body TEXT)")
    conn.execute("CREATE INDEX ix_title ON chat(title)")
    conn.executemany(
        "INSERT INTO chat(title, body) VALUES (?, ?)",
        [(f"t{i}", "x" * 500) for i in range(300)],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return conn


def _upgrade_writes_after_backup(conn, db, keep_dir):
    """What a newer image does after the backup: migrate, checkpoint, keep writing.

    Returns copies of the resulting -wal/-shm, i.e. what is left on the volume
    when the smoke test fails and the rollback starts.
    """
    conn.execute("CREATE TABLE calendar(id INTEGER PRIMARY KEY, x TEXT)")
    conn.executemany(
        "INSERT INTO chat(title, body) VALUES (?, ?)",
        [(f"v{i}", "z" * 500) for i in range(400)],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    conn.execute("DELETE FROM chat WHERE id % 3 = 0")
    conn.executemany(
        "INSERT INTO chat(title, body) VALUES (?, ?)",
        [(f"w{i}", "q" * 700) for i in range(150)],
    )
    conn.commit()
    wal, shm = keep_dir / "wal.keep", keep_dir / "shm.keep"
    shutil.copyfile(f"{db}-wal", wal)
    shutil.copyfile(f"{db}-shm", shm)
    return wal, shm


def _leave_stale_sidecars(db, wal, shm):
    shutil.copyfile(wal, f"{db}-wal")
    shutil.copyfile(shm, f"{db}-shm")


def test_old_restore_corrupts_the_database(tmp_path):
    """Control: the previous docker-cp-over-the-volume restore is what breaks it."""
    db = str(tmp_path / "webui.db")
    conn = _live_db(db)
    backup = tmp_path / "bare.bak"
    shutil.copyfile(db, backup)
    wal, shm = _upgrade_writes_after_backup(conn, db, tmp_path)
    conn.close()

    shutil.copyfile(backup, db)
    _leave_stale_sidecars(db, wal, shm)

    ok, detail = snap.integrity(db)
    assert not ok, "control must reproduce the corruption, or this test proves nothing"
    assert "malformed" in detail


def test_restore_over_stale_sidecars_leaves_a_sound_database(tmp_path):
    db = str(tmp_path / "webui.db")
    conn = _live_db(db)
    backup = str(tmp_path / "pre-upgrade.bak")
    assert snap.snapshot(db, backup) == (True, "ok")
    wal, shm = _upgrade_writes_after_backup(conn, db, tmp_path)
    conn.close()
    _leave_stale_sidecars(db, wal, shm)

    ok, detail = snap.restore(backup, db)

    assert (ok, detail) == (True, "ok")
    assert _count(db) == 300
    assert not Path(f"{db}-wal").exists() or Path(f"{db}-wal").stat().st_size == 0


def test_snapshot_includes_writes_still_in_the_wal(tmp_path):
    """A bare copy of the main file silently drops committed-but-uncheckpointed rows."""
    db = str(tmp_path / "webui.db")
    conn = _live_db(db)
    conn.executemany(
        "INSERT INTO chat(title, body) VALUES (?, ?)",
        [(f"late{i}", "y") for i in range(25)],
    )
    conn.commit()

    bare = str(tmp_path / "bare.bak")
    shutil.copyfile(db, bare)
    backup = str(tmp_path / "snap.bak")
    assert snap.snapshot(db, backup) == (True, "ok")
    conn.close()

    assert _count(bare) == 300
    assert _count(backup) == 325


def test_upgrade_preflight_refuses_a_corrupt_database(tmp_path):
    db = str(tmp_path / "webui.db")
    conn = _live_db(db)
    backup = str(tmp_path / "bare.bak")
    shutil.copyfile(db, backup)
    wal, shm = _upgrade_writes_after_backup(conn, db, tmp_path)
    conn.close()
    bad = str(tmp_path / "bad.db")
    shutil.copyfile(backup, bad)
    _leave_stale_sidecars(bad, wal, shm)

    assert snap.main(["x", "check", bad]) == 1
    ok, detail = snap.snapshot(bad, str(tmp_path / "never.bak"))
    assert not ok and "integrity" in detail
    assert not (tmp_path / "never.bak").exists()


def test_restore_refuses_a_corrupt_backup(tmp_path):
    db = str(tmp_path / "webui.db")
    conn = _live_db(db)
    conn.close()
    junk = tmp_path / "junk.bak"
    junk.write_bytes(b"not a database" * 100)

    ok, _ = snap.restore(str(junk), db)

    assert not ok
    assert _count(db) == 300, "a bad backup must never replace the live database"


@pytest.mark.parametrize("argv", [["x"], ["x", "check"], ["x", "nope", "a"]])
def test_usage_errors(argv):
    assert snap.main(argv) == 2
