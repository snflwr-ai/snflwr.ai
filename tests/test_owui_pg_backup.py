"""Unit tests for the Open WebUI Postgres backup/restore (mocked pg_dump/pg_restore)."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def backup_obj(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path))
    monkeypatch.setenv("OWUI_PG_BACKUP_ENABLED", "true")
    monkeypatch.setenv("OWUI_DB_PASSWORD", "owui-secret")
    monkeypatch.setenv("COMPRESS_BACKUPS", "false")
    from scripts.backup_database import DatabaseBackup
    return DatabaseBackup()


def test_owui_pg_backup_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path))
    monkeypatch.delenv("OWUI_PG_BACKUP_ENABLED", raising=False)
    from scripts.backup_database import DatabaseBackup
    assert DatabaseBackup().owui_pg_backup_enabled is False


def test_backup_postgresql_defaults_unchanged(backup_obj):
    """No-arg call must still target the snflwr DB and name the file snflwr_postgres_*."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("-f") + 1]).write_text("dump")
        return MagicMock(returncode=0, stderr="")

    with patch("scripts.backup_database.subprocess.run", side_effect=fake_run):
        ok, result = backup_obj.backup_postgresql()
    assert ok is True
    assert "snflwr_postgres_" in result
    # Defaults must still target the primary snflwr DB/user, not openwebui.
    from config import system_config
    assert captured["cmd"][captured["cmd"].index("-d") + 1] == system_config.POSTGRES_DB
    assert captured["cmd"][captured["cmd"].index("-U") + 1] == system_config.POSTGRES_USER


def test_backup_postgresql_targets_openwebui_with_owui_creds(backup_obj):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["pgpassword"] = kwargs.get("env", {}).get("PGPASSWORD")
        Path(cmd[cmd.index("-f") + 1]).write_text("dump")
        return MagicMock(returncode=0, stderr="")

    with patch("scripts.backup_database.subprocess.run", side_effect=fake_run):
        ok, result = backup_obj.backup_postgresql(
            db_name="openwebui", user="openwebui",
            password="owui-secret", label="owui_postgres")
    assert ok is True
    assert "snflwr_owui_postgres_" in result
    assert captured["cmd"][captured["cmd"].index("-d") + 1] == "openwebui"
    assert captured["cmd"][captured["cmd"].index("-U") + 1] == "openwebui"
    assert captured["pgpassword"] == "owui-secret"


def test_backup_postgresql_fails_closed_on_error(backup_obj):
    with patch("scripts.backup_database.subprocess.run",
               return_value=MagicMock(returncode=1, stderr="boom")):
        ok, result = backup_obj.backup_postgresql(db_name="openwebui", user="openwebui",
                                                  password="x", label="owui_postgres")
    assert ok is False
    assert "boom" in result


def test_restore_postgresql_targets_openwebui(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["pgpassword"] = kwargs.get("env", {}).get("PGPASSWORD")
        return MagicMock(returncode=0, stderr="")

    dump = tmp_path / "snflwr_owui_postgres_x.sql"
    dump.write_text("dump")
    from scripts.backup_database import restore_postgresql
    with patch("scripts.backup_database.subprocess.run", side_effect=fake_run):
        ok = restore_postgresql(dump, db_name="openwebui", user="openwebui", password="owui-secret")
    assert ok is True
    assert captured["cmd"][0] == "pg_restore"
    assert captured["cmd"][captured["cmd"].index("-d") + 1] == "openwebui"
    assert captured["pgpassword"] == "owui-secret"
