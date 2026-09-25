"""restore_sqlite / restore_postgresql must not corrupt or leak.

Two defects, both found by reading the restore path rather than by a failure:

* restore_sqlite copied the backup over the live file and left the live file's
  -wal/-shm beside it. SQLite then replays a newer image's WAL onto an older
  main file -- the exact hazard that left Open WebUI's webui.db malformed
  (tests/test_owui_db_snapshot.py). The pre-restore safety copy had the mirror
  defect: it copied the main file only, so rows still in the WAL were missing
  from the one copy meant to save them.
* both restores decompress a .gz dump next to the backup and deleted it only on
  the success path. A failed pg_restore, or a rejected parameter, left a
  plaintext database dump -- children's data -- on disk.

Real SQLite throughout: the corruption only exists in WAL replay.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scripts.backup_database import restore_postgresql, restore_sqlite

from config import system_config


def _count(path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM chat").fetchone()[0]
    finally:
        conn.close()


def _integrity(path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        conn.close()


def _live_db_with_backup(tmp_path):
    """A WAL-mode live DB, a clean backup of it, then newer writes left in the WAL.

    Returns (db_path, backup_path, open_conn). The connection is still open so
    the -wal/-shm exist; callers copy them before closing (closing checkpoints).
    """
    db = tmp_path / "snflwr.db"
    conn = sqlite3.connect(str(db))
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
    backup = tmp_path / "backup.db"
    shutil.copyfile(db, backup)

    # What the live app does after the backup: schema change, checkpoint, more
    # writes that stay in the WAL.
    conn.execute("CREATE TABLE later(id INTEGER PRIMARY KEY, x TEXT)")
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
    return db, backup, conn


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db, backup, conn = _live_db_with_backup(tmp_path)
    monkeypatch.setattr(system_config, "DB_PATH", db)
    return db, backup, conn


def test_control_a_bare_copy_over_stale_sidecars_corrupts(tmp_path):
    """Positive control: without this, the fix test below proves nothing."""
    db, backup, conn = _live_db_with_backup(tmp_path)
    wal = Path(f"{db}-wal").read_bytes()
    shm = Path(f"{db}-shm").read_bytes()
    conn.close()

    shutil.copyfile(backup, db)  # what restore_sqlite used to do
    Path(f"{db}-wal").write_bytes(wal)
    Path(f"{db}-shm").write_bytes(shm)

    assert _integrity(db) != "ok", "control must reproduce the corruption"


def test_restore_over_stale_sidecars_leaves_a_sound_database(db_env):
    db, backup, conn = db_env
    wal = Path(f"{db}-wal").read_bytes()
    shm = Path(f"{db}-shm").read_bytes()
    conn.close()
    Path(f"{db}-wal").write_bytes(wal)
    Path(f"{db}-shm").write_bytes(shm)

    assert restore_sqlite(backup) is True

    assert _integrity(db) == "ok"
    assert _count(db) == 300


def test_pre_restore_copy_keeps_rows_still_in_the_wal(db_env):
    db, backup, conn = db_env
    live_rows = conn.execute("SELECT count(*) FROM chat").fetchone()[0]
    assert live_rows != 300  # the WAL holds rows the backup does not

    # The app is stopped mid-flight: sidecars stay, connection gone. Copy the
    # sidecars before close() checkpoints them away, then put them back.
    wal = Path(f"{db}-wal").read_bytes()
    shm = Path(f"{db}-shm").read_bytes()
    main = db.read_bytes()
    conn.close()
    db.write_bytes(main)
    Path(f"{db}-wal").write_bytes(wal)
    Path(f"{db}-shm").write_bytes(shm)

    assert restore_sqlite(backup) is True

    pre = db.with_suffix(".db.pre-restore")
    assert pre.exists()
    assert _integrity(pre) == "ok"
    assert _count(pre) == live_rows, "the safety copy lost rows that were in the WAL"


def test_gz_restore_removes_the_plaintext_temp(db_env, tmp_path):
    db, backup, conn = db_env
    conn.close()
    gz = tmp_path / "snflwr_backup.db.gz"
    with open(backup, "rb") as f_in, gzip.open(gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    assert restore_sqlite(gz) is True
    assert _count(db) == 300
    assert not gz.with_suffix("").exists()


def _gz_dump(tmp_path) -> Path:
    gz = tmp_path / "snflwr_postgres_x.sql.gz"
    with gzip.open(gz, "wb") as f:
        f.write(b"-- child data, plaintext once decompressed")
    return gz


def test_failed_pg_restore_removes_the_plaintext_dump(tmp_path):
    gz = _gz_dump(tmp_path)
    with patch(
        "scripts.backup_database.subprocess.run",
        return_value=MagicMock(returncode=1, stderr="boom"),
    ):
        assert (
            restore_postgresql(gz, db_name="snflwr", user="snflwr", password="x")
            is False
        )
    assert not gz.with_suffix(
        ""
    ).exists(), "a failed restore left the dump in plaintext"


def test_rejected_parameter_removes_the_plaintext_dump(tmp_path):
    gz = _gz_dump(tmp_path)
    with patch("scripts.backup_database.subprocess.run") as run:
        assert (
            restore_postgresql(gz, db_name="bad;name", user="snflwr", password="x")
            is False
        )
        run.assert_not_called()
    assert not gz.with_suffix(
        ""
    ).exists(), "a rejected restore left the dump in plaintext"


def test_successful_pg_restore_removes_the_plaintext_dump(tmp_path):
    """Positive control for the two above: the success path always cleaned up."""
    gz = _gz_dump(tmp_path)
    with patch(
        "scripts.backup_database.subprocess.run",
        return_value=MagicMock(returncode=0, stderr=""),
    ) as run:
        assert (
            restore_postgresql(gz, db_name="snflwr", user="snflwr", password="x")
            is True
        )
        run.assert_called_once()
    assert not gz.with_suffix("").exists()
