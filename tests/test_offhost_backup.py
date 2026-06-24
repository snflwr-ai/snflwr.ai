"""
Off-host backup tests: prove that after a local backup lands, the backup
file is pushed to an off-host destination via rclone, and that the whole
backup is reported as FAILED (fail-closed) if the off-host copy cannot be
made.

DR_RUNBOOK.md states: "A backup nobody has copied off-box is a backup that
didn't happen." These tests enforce that stance in code — when off-host is
enabled, a local-only backup is not a success.

rclone is a binary, not importable, so subprocess + shutil.which are mocked.
The backup file production path itself is NOT mocked (real on-disk SQLite).
"""

import subprocess
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def offhost_env(tmp_path, monkeypatch):
    """Isolated env with a temp DB + backup dir and off-host ENABLED,
    pointed at a well-formed rclone remote."""
    from config import system_config

    db_path = tmp_path / "offhost_test.db"
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(system_config, "DB_PATH", db_path)
    monkeypatch.setenv("BACKUP_PATH", str(backup_dir))
    monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "true")
    monkeypatch.setenv("RCLONE_REMOTE", "b2:snflwr-backups-prod")
    monkeypatch.setenv("OFFHOST_RETENTION_DAYS", "30")

    yield {"db_path": db_path, "backup_dir": backup_dir}


def _seed_db(db_path: Path) -> None:
    """Create a real initialized SQLite DB so backup_sqlite has a file to copy."""
    from storage.database import DatabaseManager

    db = DatabaseManager(db_path)
    db.initialize_database()
    db.close()


def _fake_run(returncode=0, stderr=""):
    """Build a subprocess.run replacement that records calls."""
    calls = []

    def runner(cmd, *args, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    runner.calls = calls
    return runner


# --------------------------------------------------------------------------
# upload_offhost — the core unit
# --------------------------------------------------------------------------


class TestUploadOffhost:
    def test_invokes_rclone_copy_with_remote_and_file(self, offhost_env, monkeypatch):
        """A well-formed upload shells out to `rclone copy <file> <remote>`."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = offhost_env["backup_dir"] / "snflwr_sqlite_x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        ok, msg = bak.upload_offhost(str(backup_file))

        assert ok is True, msg
        assert runner.calls, "rclone was never invoked"
        cmd = runner.calls[0]
        assert cmd[0] == "rclone"
        assert "copy" in cmd
        assert str(backup_file) in cmd
        assert "b2:snflwr-backups-prod" in cmd

    def test_fails_closed_when_rclone_binary_missing(self, offhost_env, monkeypatch):
        """If rclone is not installed, upload returns failure and never shells out."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: None)
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = offhost_env["backup_dir"] / "snflwr_sqlite_x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        ok, msg = bak.upload_offhost(str(backup_file))

        assert ok is False
        assert "rclone" in msg.lower()
        assert not runner.calls, "must not invoke rclone when binary is missing"

    def test_fails_closed_on_nonzero_rclone_exit(self, offhost_env, monkeypatch):
        """A non-zero rclone exit (e.g. auth/network failure) is a failed upload."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=1, stderr="connection refused")
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = offhost_env["backup_dir"] / "snflwr_sqlite_x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        ok, msg = bak.upload_offhost(str(backup_file))

        assert ok is False
        assert "connection refused" in msg

    def test_rejects_malformed_remote(self, tmp_path, monkeypatch):
        """An enabled-but-malformed RCLONE_REMOTE fails closed and never shells out."""
        from config import system_config
        import scripts.backup_database as bd

        monkeypatch.setattr(system_config, "DB_PATH", tmp_path / "db.db")
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "true")
        monkeypatch.setenv("RCLONE_REMOTE", "not a valid; remote")
        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = tmp_path / "backups" / "snflwr_sqlite_x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        ok, msg = bak.upload_offhost(str(backup_file))

        assert ok is False
        assert not runner.calls

    def test_rejects_empty_remote(self, tmp_path, monkeypatch):
        """Enabled but RCLONE_REMOTE unset fails closed without shelling out."""
        from config import system_config
        import scripts.backup_database as bd

        monkeypatch.setattr(system_config, "DB_PATH", tmp_path / "db.db")
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "true")
        monkeypatch.delenv("RCLONE_REMOTE", raising=False)
        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = tmp_path / "backups" / "x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        ok, msg = bak.upload_offhost(str(backup_file))

        assert ok is False
        assert "RCLONE_REMOTE" in msg
        assert not runner.calls

    def test_skips_nonexistent_files(self, offhost_env, monkeypatch):
        """Files that don't exist are skipped, not uploaded."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, _ = bak.upload_offhost(str(offhost_env["backup_dir"] / "does_not_exist.gz"))

        assert ok is True
        assert not runner.calls, "must not upload a file that doesn't exist"

    def test_passes_config_path_when_set(self, offhost_env, monkeypatch):
        """RCLONE_CONFIG, when set, is passed through as --config."""
        import scripts.backup_database as bd

        monkeypatch.setenv("RCLONE_CONFIG", "/etc/snflwr/rclone.conf")
        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        backup_file = offhost_env["backup_dir"] / "snflwr_sqlite_x.db.gz"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        backup_file.write_bytes(b"fake")

        bak.upload_offhost(str(backup_file))

        cmd = runner.calls[0]
        assert "--config" in cmd
        assert "/etc/snflwr/rclone.conf" in cmd


# --------------------------------------------------------------------------
# Remote retention prune
# --------------------------------------------------------------------------


class TestPruneOffhost:
    def test_prune_invokes_rclone_delete_with_min_age(self, offhost_env, monkeypatch):
        """Remote prune deletes files older than the retention window."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        bak.prune_offhost()

        assert runner.calls, "rclone delete was never invoked"
        cmd = runner.calls[0]
        assert cmd[0] == "rclone"
        assert "delete" in cmd
        assert "b2:snflwr-backups-prod" in cmd
        assert "--min-age" in cmd
        assert "30d" in cmd

    def test_prune_skips_when_binary_missing(self, offhost_env, monkeypatch):
        """Prune is best-effort: missing rclone returns failure, no shell-out."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: None)
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, _ = bak.prune_offhost()

        assert ok is False
        assert not runner.calls


# --------------------------------------------------------------------------
# run_backup integration — fail-closed wiring
# --------------------------------------------------------------------------


class TestRunBackupOffhostIntegration:
    def test_run_backup_fails_when_offhost_upload_fails(self, offhost_env, monkeypatch):
        """A successful LOCAL backup is still reported as failed if the
        off-host copy fails (fail-closed)."""
        import scripts.backup_database as bd

        _seed_db(offhost_env["db_path"])
        # rclone missing -> upload fails closed
        monkeypatch.setattr(bd.shutil, "which", lambda _: None)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is False

    def test_run_backup_skips_offhost_when_disabled(self, tmp_path, monkeypatch):
        """With off-host disabled, run_backup succeeds and never touches rclone."""
        from config import system_config
        import scripts.backup_database as bd

        db_path = tmp_path / "db.db"
        monkeypatch.setattr(system_config, "DB_PATH", db_path)
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
        _seed_db(db_path)

        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True
        assert not any(
            c and c[0] == "rclone" for c in runner.calls
        ), "rclone must not run when off-host is disabled"

    def test_run_backup_succeeds_when_offhost_upload_succeeds(self, offhost_env, monkeypatch):
        """Local backup + successful off-host copy => overall success."""
        import scripts.backup_database as bd

        _seed_db(offhost_env["db_path"])
        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True
        assert any(c and c[0] == "rclone" and "copy" in c for c in runner.calls)


# --------------------------------------------------------------------------
# Heartbeat / dead-man's-switch
# --------------------------------------------------------------------------


def _fake_urlopen():
    """Record heartbeat pings; return a context-manager-like response."""
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"OK"

    def opener(url, *args, **kwargs):
        calls.append(url)
        return _Resp()

    opener.calls = calls
    return opener


class TestBackupHeartbeat:
    def test_pings_heartbeat_on_success(self, tmp_path, monkeypatch):
        """A fully successful backup pings the configured heartbeat URL."""
        from config import system_config
        import scripts.backup_database as bd

        db_path = tmp_path / "db.db"
        monkeypatch.setattr(system_config, "DB_PATH", db_path)
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
        monkeypatch.setenv("BACKUP_HEARTBEAT_URL", "https://hc-ping.com/abc123")
        _seed_db(db_path)

        opener = _fake_urlopen()
        monkeypatch.setattr(bd.urllib.request, "urlopen", opener)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True
        assert opener.calls == ["https://hc-ping.com/abc123"]

    def test_pings_fail_endpoint_on_failure(self, offhost_env, monkeypatch):
        """A failed backup pings the /fail sub-endpoint so monitoring alarms."""
        import scripts.backup_database as bd

        _seed_db(offhost_env["db_path"])
        monkeypatch.setenv("BACKUP_HEARTBEAT_URL", "https://hc-ping.com/abc123")
        # rclone missing -> off-host fails -> backup fails closed
        monkeypatch.setattr(bd.shutil, "which", lambda _: None)
        opener = _fake_urlopen()
        monkeypatch.setattr(bd.urllib.request, "urlopen", opener)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is False
        assert opener.calls == ["https://hc-ping.com/abc123/fail"]

    def test_no_ping_when_url_unset(self, tmp_path, monkeypatch):
        """No heartbeat configured => no ping attempted."""
        from config import system_config
        import scripts.backup_database as bd

        db_path = tmp_path / "db.db"
        monkeypatch.setattr(system_config, "DB_PATH", db_path)
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
        monkeypatch.delenv("BACKUP_HEARTBEAT_URL", raising=False)
        _seed_db(db_path)

        opener = _fake_urlopen()
        monkeypatch.setattr(bd.urllib.request, "urlopen", opener)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True
        assert not opener.calls

    def test_heartbeat_network_error_is_swallowed(self, tmp_path, monkeypatch):
        """A heartbeat that can't be reached must not change the backup result."""
        from config import system_config
        import scripts.backup_database as bd

        db_path = tmp_path / "db.db"
        monkeypatch.setattr(system_config, "DB_PATH", db_path)
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
        monkeypatch.setenv("BACKUP_HEARTBEAT_URL", "https://hc-ping.com/abc123")
        _seed_db(db_path)

        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(bd.urllib.request, "urlopen", boom)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True  # local backup succeeded; ping failure ignored

    def test_rejects_non_http_heartbeat_url(self, tmp_path, monkeypatch):
        """A non-http(s) heartbeat URL is ignored, never opened."""
        from config import system_config
        import scripts.backup_database as bd

        db_path = tmp_path / "db.db"
        monkeypatch.setattr(system_config, "DB_PATH", db_path)
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
        monkeypatch.setenv("BACKUP_HEARTBEAT_URL", "file:///etc/passwd")
        _seed_db(db_path)

        opener = _fake_urlopen()
        monkeypatch.setattr(bd.urllib.request, "urlopen", opener)

        bak = bd.DatabaseBackup()
        assert bak.run_backup() is True
        assert not opener.calls


# --------------------------------------------------------------------------
# Restore from off-host (pull)
# --------------------------------------------------------------------------


class TestPullOffhost:
    def test_pull_invokes_rclone_copy_from_remote(self, offhost_env, monkeypatch):
        """Pulling a named backup copies it from the remote into BACKUP_PATH."""
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, local_path = bak.pull_offhost("snflwr_sqlite_20260617.db.gz")

        assert ok is True
        cmd = runner.calls[0]
        assert cmd[0] == "rclone"
        assert "copy" in cmd
        assert "b2:snflwr-backups-prod/snflwr_sqlite_20260617.db.gz" in cmd
        # destination is the local backup dir
        assert str(offhost_env["backup_dir"]) in cmd
        assert local_path.endswith("snflwr_sqlite_20260617.db.gz")

    def test_pull_fails_closed_when_binary_missing(self, offhost_env, monkeypatch):
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: None)
        runner = _fake_run(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, msg = bak.pull_offhost("snflwr_sqlite_x.db.gz")

        assert ok is False
        assert not runner.calls

    def test_pull_then_restore_recovers_data(self, offhost_env, monkeypatch):
        """End-to-end drill: a backup that exists ONLY off-host can be pulled
        and restored. Simulates total local loss (host failure)."""
        import scripts.backup_database as bd

        # 1. Seed + back up locally to produce a real .db.gz.
        seed_path = offhost_env["db_path"]
        _seed_db(seed_path)
        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/rclone")
        bak = bd.DatabaseBackup()
        ok, backup_file = bak.backup_sqlite()
        assert ok
        backup_name = Path(backup_file).name

        # 2. Simulate host loss: move the backup "off-host" (out of BACKUP_PATH)
        #    and corrupt the live DB.
        offhost_store = offhost_env["backup_dir"].parent / "remote"
        offhost_store.mkdir(parents=True, exist_ok=True)
        import shutil as _sh
        _sh.move(backup_file, offhost_store / backup_name)
        seed_path.write_bytes(b"corrupted")

        # 3. rclone "copy" is mocked to restore the file from our fake remote.
        def fake_pull(cmd, *args, **kwargs):
            # cmd: rclone copy <remote/name> <dest_dir> [...]
            dest_dir = Path(cmd[3])
            _sh.copy2(offhost_store / backup_name, dest_dir / backup_name)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bd.subprocess, "run", fake_pull)

        ok, local_path = bak.pull_offhost(backup_name)
        assert ok is True

        # 4. Restore from the pulled file and verify recovery.
        from scripts.backup_database import restore_sqlite
        import sqlite3
        assert restore_sqlite(Path(local_path)) is True
        conn = sqlite3.connect(str(seed_path))
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "child_profiles" in tables


# --------------------------------------------------------------------------
# backup_open_webui — Open WebUI data volume backup (docker cp)
# --------------------------------------------------------------------------


@pytest.fixture
def owui_env(tmp_path, monkeypatch):
    """Isolated env with a temp backup dir; OWUI backup is on by default."""
    from config import system_config

    monkeypatch.setattr(system_config, "DB_PATH", tmp_path / "snflwr.db")
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "false")
    yield {"backup_dir": tmp_path / "backups"}


def _docker_cp_runner(returncode=0, stderr=""):
    """subprocess.run stand-in that simulates `docker cp <c>:<dir> <dest>` by
    materializing <dest>/data (the basename of /app/backend/data)."""
    calls = []

    def runner(cmd, *args, **kwargs):
        calls.append(cmd)
        if returncode == 0 and len(cmd) >= 4 and cmd[0] == "docker" and cmd[1] == "cp":
            data_dir = Path(cmd[3]) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "webui.db").write_bytes(b"fake sqlite")
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    runner.calls = calls
    return runner


class TestBackupOpenWebUI:
    def test_archives_owui_data_via_docker_cp(self, owui_env, monkeypatch):
        import tarfile
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/docker")
        runner = _docker_cp_runner(returncode=0)
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, archive = bak.backup_open_webui()

        assert ok is True
        archive_path = Path(archive)
        assert archive_path.exists()
        assert archive_path.name.startswith("snflwr_owui_")
        assert archive_path.name.endswith(".tar.gz")
        # docker cp was invoked against the configured container/path
        assert any(c[0] == "docker" and c[1] == "cp" for c in runner.calls)
        # archive actually contains the OWUI data (webui.db)
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("webui.db") for n in names)
        # temp working dir is cleaned up
        assert not list(owui_env["backup_dir"].glob(".owui_tmp_*"))

    def test_fails_when_docker_missing(self, owui_env, monkeypatch):
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: None)
        bak = bd.DatabaseBackup()
        ok, reason = bak.backup_open_webui()
        assert ok is False
        assert "docker" in reason.lower()

    def test_fails_on_docker_cp_nonzero(self, owui_env, monkeypatch):
        import scripts.backup_database as bd

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/docker")
        runner = _docker_cp_runner(returncode=1, stderr="No such container: snflwr-frontend")
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok, reason = bak.backup_open_webui()
        assert ok is False
        assert "No such container" in reason

    def test_owui_artifact_pushed_offhost(self, tmp_path, monkeypatch):
        """A full run pushes the OWUI archive off-host alongside the DB backup."""
        from config import system_config
        import scripts.backup_database as bd

        monkeypatch.setattr(system_config, "DB_PATH", tmp_path / "snflwr.db")
        monkeypatch.setenv("BACKUP_PATH", str(tmp_path / "backups"))
        monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("OFFHOST_BACKUP_ENABLED", "true")
        monkeypatch.setenv("RCLONE_REMOTE", "b2:snflwr-backups-prod")
        monkeypatch.setenv("OWUI_BACKUP_ENABLED", "true")
        _seed_db(tmp_path / "snflwr.db")

        uploaded = []

        def runner(cmd, *args, **kwargs):
            # Simulate docker cp materializing the data dir; record rclone copies.
            if cmd[0] == "docker" and cmd[1] == "cp":
                (Path(cmd[3]) / "data").mkdir(parents=True, exist_ok=True)
                (Path(cmd[3]) / "data" / "webui.db").write_bytes(b"fake")
            elif cmd[0] == "rclone" and cmd[1] == "copy":
                uploaded.append(cmd[2])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(bd.shutil, "which", lambda _: "/usr/bin/" + "x")
        monkeypatch.setattr(bd.subprocess, "run", runner)

        bak = bd.DatabaseBackup()
        ok = bak.run_backup()

        assert ok is True
        assert any("snflwr_owui_" in u for u in uploaded), uploaded
