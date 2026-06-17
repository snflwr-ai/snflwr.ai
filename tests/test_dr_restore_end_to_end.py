"""
Disaster-recovery end-to-end test: prove the backup → restore path actually
works against the SQLite tier (Family USB + Home Server).

Pitching snflwr.ai as production-ready to a paying school requires more
than "we have backup code." It requires evidence that a corrupted live DB
can be restored from a backup file, the schema survives the round-trip,
the seed data survives, and the app can read+write to the restored DB.

This test exercises that path end-to-end with a real on-disk SQLite DB
(no mocks of the backup/restore code). It is fast enough to run on every
PR (~1 second).

The PostgreSQL tier needs a live container and is not covered here; that
test runs nightly via a separate workflow.

DR-RUNBOOK.md documents the operator-facing procedure and RPO/RTO targets;
this test validates the engineering surface those targets depend on.
"""

import gzip
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _seed_db(db_path: Path) -> dict:
    """Create a fresh DB with the production schema + a minimal child-data
    graph. Returns the seeded identifiers so phase 5 can assert recovery."""
    from storage.database import DatabaseManager

    db = DatabaseManager(db_path)
    db.initialize_database()

    now = datetime.now(timezone.utc).isoformat()
    parent_id = "dr-parent-001"
    profile_id = "dr-profile-001"
    conversation_id = "dr-conv-001"

    db.execute_write(
        """
        INSERT INTO accounts
        (parent_id, username, password_hash, device_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (parent_id, "dr_parent", "hash", "dr-device-001", now),
    )
    db.execute_write(
        """
        INSERT INTO child_profiles
        (profile_id, parent_id, name, age, grade, created_at,
         parental_consent_given, coppa_verified, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1, 1, 1)
        """,
        (profile_id, parent_id, "RestoredChild", 9, "4", now),
    )
    db.execute_write(
        """
        INSERT INTO sessions (session_id, profile_id, parent_id, started_at)
        VALUES (?, ?, ?, ?)
        """,
        ("dr-sess-001", profile_id, parent_id, now),
    )
    db.execute_write(
        """
        INSERT INTO conversations
        (conversation_id, session_id, profile_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, "dr-sess-001", profile_id, now, now),
    )
    db.execute_write(
        """
        INSERT INTO messages
        (message_id, conversation_id, role, content, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("dr-msg-001", conversation_id, "user", "what is the capital of france", now),
    )
    db.close()

    return {
        "parent_id": parent_id,
        "profile_id": profile_id,
        "conversation_id": conversation_id,
        "message_content": "what is the capital of france",
    }


def _count(db_path: Path, sql: str, params: tuple) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def dr_env(tmp_path, monkeypatch):
    """Isolated DR environment: temp DB path + temp backup dir, both wired
    into system_config so backup/restore operate on the temp files."""
    from config import system_config

    db_path = tmp_path / "dr_test.db"
    backup_dir = tmp_path / "backups"

    # Run with encryption OFF — encryption-key recovery is a separate
    # concern documented in ENCRYPTION_KEY_RECOVERY.md and tested elsewhere.
    monkeypatch.setattr(system_config, "DB_PATH", db_path)
    monkeypatch.setenv("BACKUP_PATH", str(backup_dir))
    monkeypatch.setenv("DB_ENCRYPTION_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")

    yield {"db_path": db_path, "backup_dir": backup_dir}


# --------------------------------------------------------------------------
# Tests — one test per phase, plus a phases-1-through-5 combined drill
# --------------------------------------------------------------------------


class TestDRRestoreEndToEnd:
    """End-to-end disaster-recovery drill for the SQLite tier."""

    def test_phase_1_backup_produces_compressed_file(self, dr_env):
        """A backup of a seeded DB lands a .db.gz file in BACKUP_PATH."""
        _seed_db(dr_env["db_path"])

        from scripts.backup_database import DatabaseBackup
        bak = DatabaseBackup()
        ok, backup_file = bak.backup_sqlite()

        assert ok is True
        backup_path = Path(backup_file)
        assert backup_path.exists(), "backup file was not created"
        assert backup_path.suffix == ".gz", (
            "backup file should be gzip-compressed by default"
        )
        assert backup_path.stat().st_size > 0

    def test_phase_2_corruption_breaks_live_db(self, dr_env):
        """Sanity check: truncating the live DB really does break it,
        so phase 3 is testing recovery from a genuinely-broken state."""
        seed = _seed_db(dr_env["db_path"])

        # Truncate the live DB to a 1-byte stub (simulates fs corruption).
        with open(dr_env["db_path"], "wb") as f:
            f.write(b"X")

        # Now attempting to read should fail.
        with pytest.raises(sqlite3.DatabaseError):
            _count(
                dr_env["db_path"],
                "SELECT COUNT(*) FROM child_profiles WHERE profile_id = ?",
                (seed["profile_id"],),
            )

    def test_phase_3_through_5_restore_recovers_full_data_graph(self, dr_env):
        """The headline DR drill: seed → backup → corrupt → restore →
        assert every seeded row is present and the schema is intact.

        This single test is the production-readiness gate. If it ever
        fails, restoring a real customer's data is not safe."""
        seed = _seed_db(dr_env["db_path"])

        # Phase 1: backup
        from scripts.backup_database import DatabaseBackup, restore_sqlite
        bak = DatabaseBackup()
        ok, backup_file = bak.backup_sqlite()
        assert ok, f"backup failed: {backup_file}"

        # Phase 2: corrupt the live DB
        with open(dr_env["db_path"], "wb") as f:
            f.write(b"corrupted-not-a-sqlite-file")

        # Phase 3: restore
        ok = restore_sqlite(Path(backup_file))
        assert ok is True

        # Phase 4: schema validates — opening the restored DB and listing
        # tables exercises sqlite_master, which is the first thing that
        # would fail on a half-restored file.
        conn = sqlite3.connect(str(dr_env["db_path"]))
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for required in (
                "accounts",
                "child_profiles",
                "conversations",
                "messages",
                "audit_log",
                "parental_consent_log",
            ):
                assert required in tables, f"schema lost {required} after restore"
        finally:
            conn.close()

        # Phase 5: every seeded row is back
        assert _count(
            dr_env["db_path"],
            "SELECT COUNT(*) FROM accounts WHERE parent_id = ?",
            (seed["parent_id"],),
        ) == 1
        assert _count(
            dr_env["db_path"],
            "SELECT COUNT(*) FROM child_profiles WHERE profile_id = ?",
            (seed["profile_id"],),
        ) == 1
        assert _count(
            dr_env["db_path"],
            "SELECT COUNT(*) FROM conversations WHERE conversation_id = ?",
            (seed["conversation_id"],),
        ) == 1
        # Message content survives byte-identical
        conn = sqlite3.connect(str(dr_env["db_path"]))
        try:
            content = conn.execute(
                "SELECT content FROM messages WHERE message_id = ?",
                ("dr-msg-001",),
            ).fetchone()[0]
            assert content == seed["message_content"]
        finally:
            conn.close()

    def test_restore_preserves_writeability(self, dr_env):
        """After restore, the DB must accept new writes. A read-only
        restore is technically possible (e.g. file mode flags) and would
        silently break the app on first new request."""
        _seed_db(dr_env["db_path"])

        from scripts.backup_database import DatabaseBackup, restore_sqlite
        bak = DatabaseBackup()
        _, backup_file = bak.backup_sqlite()

        with open(dr_env["db_path"], "wb") as f:
            f.write(b"corrupt")

        assert restore_sqlite(Path(backup_file)) is True

        # New write must succeed.
        conn = sqlite3.connect(str(dr_env["db_path"]))
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO messages (message_id, conversation_id, role,
                                      content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("dr-msg-post-restore", "dr-conv-001", "user",
                 "post-restore write", now),
            )
            conn.commit()
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE message_id = ?",
                ("dr-msg-post-restore",),
            ).fetchone()[0]
            assert n == 1
        finally:
            conn.close()

    def test_restore_keeps_a_pre_restore_safety_copy(self, dr_env):
        """restore_sqlite() should save the live DB as `.db.pre-restore`
        before overwriting it — last-line defense against restoring the
        wrong backup file in a panic."""
        _seed_db(dr_env["db_path"])

        from scripts.backup_database import DatabaseBackup, restore_sqlite
        bak = DatabaseBackup()
        _, backup_file = bak.backup_sqlite()

        # Make the "live DB" slightly different from the backup so we can
        # tell the pre-restore copy apart from the backup file content.
        conn = sqlite3.connect(str(dr_env["db_path"]))
        try:
            conn.execute(
                """
                INSERT INTO messages (message_id, conversation_id, role,
                                      content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("uncommitted-since-last-backup", "dr-conv-001", "user",
                 "lost work", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        assert restore_sqlite(Path(backup_file)) is True

        pre_restore = dr_env["db_path"].with_suffix(".db.pre-restore")
        assert pre_restore.exists(), (
            "pre-restore safety copy missing — operator has no recovery "
            "path if the chosen backup turns out to be the wrong one"
        )
        # The safety copy must contain the row that the backup didn't.
        n = _count(
            pre_restore,
            "SELECT COUNT(*) FROM messages WHERE message_id = ?",
            ("uncommitted-since-last-backup",),
        )
        assert n == 1


class TestDRBackupIntegrity:
    """Properties of the backup file itself."""

    def test_backup_is_actual_sqlite_after_decompress(self, dr_env, tmp_path):
        """The gzip-wrapped backup must decompress to a real SQLite file
        (not, e.g., an HTML error page from a misconfigured backup tool)."""
        _seed_db(dr_env["db_path"])

        from scripts.backup_database import DatabaseBackup
        bak = DatabaseBackup()
        _, backup_file = bak.backup_sqlite()

        decompressed = tmp_path / "decompressed.db"
        with gzip.open(backup_file, "rb") as f_in, open(decompressed, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        # SQLite files start with the magic bytes "SQLite format 3\0".
        with open(decompressed, "rb") as f:
            magic = f.read(16)
        assert magic.startswith(b"SQLite format 3"), (
            f"backup is not a valid SQLite file (magic={magic!r})"
        )
