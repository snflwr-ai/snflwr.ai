"""Tests for scripts/owui_connect.py — dialect-aware Open WebUI config seeder.

Coverage:
  1. sqlite path — real in-memory/temp sqlite; asserts ollama block written with
     correct proxy URL + key, rc=0; re-run → rc=2 (already-correct short-circuit).
  2. Postgres dialect — mocked psycopg2; asserts %s placeholders used (not ?),
     correct JSON upserted, rc=0; re-run with same data → rc=2.
  3. Key from INTERNAL_API_KEY env var when stdin is empty.
  4. Proxy URL from SNFLWR_PROXY_URL env var.

Run with:
    DB_TYPE=sqlite DB_ENCRYPTION_ENABLED=false pytest tests/test_owui_connect.py -v
"""
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: load owui_connect from its file path (not a package import).
# ---------------------------------------------------------------------------

def _load_owui_connect():
    """Load scripts/owui_connect.py as a module without relying on sys.path."""
    repo_root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "owui_connect", repo_root / "scripts" / "owui_connect.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def owui_connect():
    return _load_owui_connect()


# ---------------------------------------------------------------------------
# Helper: build a temp sqlite DB with a `config` table.
# ---------------------------------------------------------------------------

def _make_sqlite_db(tmp_path: Path, initial_data: dict | None = None) -> Path:
    db_path = tmp_path / "webui.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE config (id INTEGER PRIMARY KEY, data TEXT, version INTEGER)")
    if initial_data is not None:
        con.execute(
            "INSERT INTO config (data, version) VALUES (?, 0)",
            (json.dumps(initial_data),),
        )
    con.commit()
    con.close()
    return db_path


# ===========================================================================
# Part 1 — sqlite path
# ===========================================================================

class TestSqlitePath:
    def test_fresh_db_inserts_ollama_block_and_returns_0(
        self, owui_connect, tmp_path, monkeypatch
    ):
        """Fresh DB (no config row) → INSERT → rc=0; ollama block is correct."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "test-key-123")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        rc = owui_connect.main()
        assert rc == 0

        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        ollama = config["ollama"]
        assert ollama["enable"] is True
        assert ollama["base_urls"] == ["http://snflwr-api:39150"]
        assert ollama["api_configs"]["0"]["key"] == "test-key-123"
        assert ollama["api_configs"]["0"]["enable"] is True

    def test_existing_row_updates_and_returns_0(
        self, owui_connect, tmp_path, monkeypatch
    ):
        """Existing config row with different key → UPDATE → rc=0."""
        existing = {"version": 0, "ui": {}, "ollama": {"enable": False, "base_urls": []}}
        db_path = _make_sqlite_db(tmp_path, initial_data=existing)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "new-key-abc")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        rc = owui_connect.main()
        assert rc == 0

        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        assert config["ollama"]["api_configs"]["0"]["key"] == "new-key-abc"

    def test_already_correct_returns_2(
        self, owui_connect, tmp_path, monkeypatch
    ):
        """Config already has correct key+URL → no-op → rc=2."""
        proxy = "http://snflwr-api:39150"
        key = "stable-key"
        existing = {
            "version": 0,
            "ui": {},
            "ollama": {
                "enable": True,
                "base_urls": [proxy],
                "api_configs": {"0": {"enable": True, "key": key}},
            },
        }
        db_path = _make_sqlite_db(tmp_path, initial_data=existing)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", key)
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        rc = owui_connect.main()
        assert rc == 2

    def test_re_run_returns_2(self, owui_connect, tmp_path, monkeypatch):
        """Running twice with the same key: first rc=0, second rc=2."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "idempotent-key")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        rc1 = owui_connect.main()
        rc2 = owui_connect.main()
        assert rc1 == 0
        assert rc2 == 2

    def test_custom_proxy_url_from_env(self, owui_connect, tmp_path, monkeypatch):
        """SNFLWR_PROXY_URL env var overrides the default proxy URL."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "key-xyz")
        monkeypatch.setenv("SNFLWR_PROXY_URL", "http://snflwr-api-service:8000")

        rc = owui_connect.main()
        assert rc == 0

        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        assert config["ollama"]["base_urls"] == ["http://snflwr-api-service:8000"]


# ===========================================================================
# Part 2 — Postgres dialect (mocked psycopg2)
# ===========================================================================

def _make_pg_mock(initial_row=None):
    """Return a mock psycopg2 module with a fake connection."""
    mock_cur = MagicMock()
    mock_con = MagicMock()
    mock_con.cursor.return_value = mock_cur

    # Simulate SELECT id, data FROM config → initial_row or None
    mock_cur.fetchone.return_value = initial_row

    mock_psycopg2 = MagicMock()
    mock_psycopg2.connect.return_value = mock_con
    return mock_psycopg2, mock_con, mock_cur


class TestPostgresPath:
    def _run_with_pg_mock(self, owui_connect, monkeypatch, initial_row, key, proxy_url=None):
        """Run main() with DATABASE_URL set + mocked psycopg2; return (rc, cur)."""
        mock_pg, mock_con, mock_cur = _make_pg_mock(initial_row=initial_row)

        monkeypatch.setenv("DATABASE_URL", "postgresql://owui:pw@snflwr-pg-rw:5432/openwebui")
        monkeypatch.setenv("INTERNAL_API_KEY", key)
        if proxy_url:
            monkeypatch.setenv("SNFLWR_PROXY_URL", proxy_url)
        else:
            monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        # Patch psycopg2 inside owui_connect's _postgres_path scope
        with patch.dict(sys.modules, {"psycopg2": mock_pg}):
            rc = owui_connect.main()

        return rc, mock_cur

    def test_fresh_pg_db_inserts_with_percent_s_placeholders(
        self, owui_connect, monkeypatch
    ):
        """Fresh Postgres DB (no row) → INSERT with %s placeholders → rc=0."""
        rc, mock_cur = self._run_with_pg_mock(
            owui_connect, monkeypatch, initial_row=None, key="pg-key-fresh"
        )
        assert rc == 0

        # Must use %s (Postgres) placeholders, NOT ? (sqlite)
        insert_calls = [
            c for c in mock_cur.execute.call_args_list
            if "INSERT" in str(c)
        ]
        assert insert_calls, "No INSERT call found"
        insert_sql = insert_calls[0][0][0]
        assert "%s" in insert_sql, f"Expected %s placeholder, got: {insert_sql!r}"
        assert "?" not in insert_sql, f"sqlite ? placeholder leaked into Postgres path: {insert_sql!r}"

        # The JSON payload should contain the correct ollama block
        insert_args = insert_calls[0][0][1]
        data_json = insert_args[0]
        config = json.loads(data_json)
        assert config["ollama"]["api_configs"]["0"]["key"] == "pg-key-fresh"
        assert config["ollama"]["base_urls"] == ["http://snflwr-api:39150"]

    def test_existing_pg_row_updates_with_percent_s(
        self, owui_connect, monkeypatch
    ):
        """Existing Postgres row → UPDATE with %s placeholders → rc=0."""
        initial_config = {"version": 0, "ui": {}}
        initial_row = (42, json.dumps(initial_config))  # (id, data_str)

        rc, mock_cur = self._run_with_pg_mock(
            owui_connect, monkeypatch, initial_row=initial_row, key="pg-key-update"
        )
        assert rc == 0

        update_calls = [
            c for c in mock_cur.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        assert update_calls, "No UPDATE call found"
        update_sql = update_calls[0][0][0]
        assert "%s" in update_sql
        assert "?" not in update_sql

        update_args = update_calls[0][0][1]
        data_json, row_id = update_args
        assert row_id == 42
        config = json.loads(data_json)
        assert config["ollama"]["api_configs"]["0"]["key"] == "pg-key-update"

    def test_pg_already_correct_returns_2(self, owui_connect, monkeypatch):
        """Postgres row already has correct key → rc=2 (no-op)."""
        proxy = "http://snflwr-api:39150"
        key = "pg-stable-key"
        existing = {
            "version": 0,
            "ui": {},
            "ollama": {
                "enable": True,
                "base_urls": [proxy],
                "api_configs": {"0": {"enable": True, "key": key}},
            },
        }
        initial_row = (7, json.dumps(existing))

        rc, mock_cur = self._run_with_pg_mock(
            owui_connect, monkeypatch, initial_row=initial_row, key=key
        )
        assert rc == 2

        # No INSERT or UPDATE should have been called
        write_calls = [
            c for c in mock_cur.execute.call_args_list
            if "INSERT" in str(c) or "UPDATE" in str(c)
        ]
        assert not write_calls, f"Unexpected write calls: {write_calls}"

    def test_pg_path_selected_when_database_url_set(self, owui_connect, monkeypatch):
        """When DATABASE_URL=postgresql://..., psycopg2.connect is called (not sqlite)."""
        mock_pg, mock_con, mock_cur = _make_pg_mock(initial_row=None)
        monkeypatch.setenv("DATABASE_URL", "postgresql://owui:pw@host:5432/openwebui")
        monkeypatch.setenv("INTERNAL_API_KEY", "dialect-test-key")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        with patch.dict(sys.modules, {"psycopg2": mock_pg}):
            rc = owui_connect.main()

        mock_pg.connect.assert_called_once_with(
            "postgresql://owui:pw@host:5432/openwebui"
        )
        assert rc == 0

    def test_sqlite_path_selected_when_database_url_absent(
        self, owui_connect, tmp_path, monkeypatch
    ):
        """When DATABASE_URL is unset, the sqlite path is taken (not psycopg2)."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "sqlite-dialect-key")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        mock_pg = MagicMock()

        with patch.dict(sys.modules, {"psycopg2": mock_pg}):
            rc = owui_connect.main()

        # psycopg2.connect must NOT have been called
        mock_pg.connect.assert_not_called()
        assert rc == 0


# ===========================================================================
# Part 3 — Key source: INTERNAL_API_KEY env var vs stdin fallback
# ===========================================================================

class TestKeySource:
    def test_key_from_env_var(self, owui_connect, tmp_path, monkeypatch):
        """INTERNAL_API_KEY env var is used when set; stdin is not consulted."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "env-provided-key")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        # Provide a DIFFERENT key on stdin — env var should win
        with patch("sys.stdin", StringIO("stdin-key-should-not-be-used")):
            rc = owui_connect.main()

        assert rc == 0
        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        assert config["ollama"]["api_configs"]["0"]["key"] == "env-provided-key"

    def test_key_from_stdin_when_env_unset(self, owui_connect, tmp_path, monkeypatch):
        """When INTERNAL_API_KEY is unset, key is read from stdin (home flow)."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        with patch("sys.stdin", StringIO("stdin-provided-key\n")):
            rc = owui_connect.main()

        assert rc == 0
        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        assert config["ollama"]["api_configs"]["0"]["key"] == "stdin-provided-key"

    def test_empty_key_returns_error(self, owui_connect, monkeypatch):
        """Both env and stdin empty → rc=1."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        with patch("sys.stdin", StringIO("")):
            rc = owui_connect.main()

        assert rc == 1

    def test_env_key_whitespace_stripped(self, owui_connect, tmp_path, monkeypatch):
        """INTERNAL_API_KEY with surrounding whitespace is stripped."""
        db_path = _make_sqlite_db(tmp_path)
        monkeypatch.setattr(owui_connect, "DB_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_KEY", "  padded-key  ")
        monkeypatch.delenv("SNFLWR_PROXY_URL", raising=False)

        rc = owui_connect.main()
        assert rc == 0

        con = sqlite3.connect(str(db_path))
        row = con.execute("SELECT data FROM config").fetchone()
        con.close()
        config = json.loads(row[0])
        assert config["ollama"]["api_configs"]["0"]["key"] == "padded-key"
