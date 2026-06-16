"""
COPPA C5 — Cascade delete on parental consent revocation.

Per 16 CFR § 312.6(a)(4), revocation of parental consent requires the operator
to delete the child's personal information. Deactivation flags are insufficient.

These tests exercise the real SQLite schema with FK CASCADE enforcement and
verify that revoke_parental_consent:

1. Atomically removes the child profile and every row keyed (directly or
   transitively) on profile_id.
2. Preserves sessions rows with profile_id NULL'd (session-level audit needs
   the duration record, no child PII on it).
3. Writes a 'parental_consent_revoked' row to audit_log that survives the
   cascade (audit_log has no FK to child_profiles).
4. Rolls back the entire operation if the audit-log insert fails (no orphan
   deletion without a record).
5. Returns False and writes nothing when the profile does not exist.
"""

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.age_verification import AgeVerificationManager
from storage.database import DatabaseManager


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def temp_db():
    """Real SQLite DB with the full production schema and FK enforcement."""
    tmp = tempfile.mkdtemp()
    db = DatabaseManager(Path(tmp) / "coppa_cascade.db")
    db.initialize_database()
    yield db
    db.close()
    shutil.rmtree(tmp)


@pytest.fixture
def seeded_profile(temp_db):
    """
    Seed a complete child-data graph for one profile so we can assert every
    descendant table is cleaned up.

    Returns the profile_id and the parent_id.
    """
    now = datetime.now(timezone.utc).isoformat()
    parent_id = "par-c5-001"
    profile_id = "prof-c5-001"
    session_id = "sess-c5-001"
    conversation_id = "conv-c5-001"

    # accounts (parent)
    temp_db.execute_write(
        """
        INSERT INTO accounts
        (parent_id, username, password_hash, device_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (parent_id, "parent_one", "hash", "device-c5-001", now),
    )

    # child_profiles
    temp_db.execute_write(
        """
        INSERT INTO child_profiles
        (profile_id, parent_id, name, age, grade, created_at,
         parental_consent_given, coppa_verified, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1, 1, 1)
        """,
        (profile_id, parent_id, "ChildName", 8, "3", now),
    )

    # profile_subjects
    temp_db.execute_write(
        "INSERT INTO profile_subjects (profile_id, subject, added_at) VALUES (?, ?, ?)",
        (profile_id, "math", now),
    )

    # sessions  (FK: profile_id ON DELETE SET NULL — row should survive)
    temp_db.execute_write(
        """
        INSERT INTO sessions
        (session_id, profile_id, parent_id, started_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, profile_id, parent_id, now),
    )

    # conversations (FK: profile_id ON DELETE CASCADE)
    temp_db.execute_write(
        """
        INSERT INTO conversations
        (conversation_id, session_id, profile_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, session_id, profile_id, now, now),
    )

    # messages (FK via conversations — transitive cascade)
    temp_db.execute_write(
        """
        INSERT INTO messages
        (message_id, conversation_id, role, content, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("msg-c5-001", conversation_id, "user", "what is 2+2", now),
    )

    # safety_incidents
    temp_db.execute_write(
        """
        INSERT INTO safety_incidents
        (incident_id, profile_id, incident_type, severity, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (1, profile_id, "blocked_query", "minor", now),
    )

    # safety_false_positives
    temp_db.execute_write(
        """
        INSERT INTO safety_false_positives
        (profile_id, message_text, block_reason, triggered_keywords, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (profile_id, "innocent text", "keyword", "[]", now),
    )

    # learning_analytics
    temp_db.execute_write(
        """
        INSERT INTO learning_analytics
        (profile_id, date, subject_area, questions_asked)
        VALUES (?, ?, ?, ?)
        """,
        (profile_id, "2026-06-16", "math", 5),
    )

    # parental_consent_log (the original consent record)
    temp_db.execute_write(
        """
        INSERT INTO parental_consent_log
        (consent_id, profile_id, parent_id, consent_type, consent_method,
         consent_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        ("consent-c5-001", profile_id, parent_id, "initial",
         "email_verification", now),
    )

    return {"profile_id": profile_id, "parent_id": parent_id,
            "session_id": session_id, "conversation_id": conversation_id}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _count(db, sql, params):
    rows = db.execute_query(sql, params)
    if not rows:
        return 0
    row = rows[0]
    return row[0] if not isinstance(row, dict) else list(row.values())[0]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestRevokeCascadeDelete:
    def test_revoke_removes_child_profile_row(self, temp_db, seeded_profile):
        manager = AgeVerificationManager(temp_db)

        ok = manager.revoke_parental_consent(
            profile_id=seeded_profile["profile_id"],
            parent_id=seeded_profile["parent_id"],
            reason="parent requested",
        )

        assert ok is True
        assert _count(
            temp_db,
            "SELECT COUNT(*) FROM child_profiles WHERE profile_id = ?",
            (seeded_profile["profile_id"],),
        ) == 0

    @pytest.mark.parametrize(
        "table",
        [
            "profile_subjects",
            "conversations",
            "safety_incidents",
            "safety_false_positives",
            "learning_analytics",
            "parental_consent_log",
        ],
    )
    def test_revoke_cascades_profile_keyed_tables(
        self, temp_db, seeded_profile, table
    ):
        manager = AgeVerificationManager(temp_db)

        manager.revoke_parental_consent(
            profile_id=seeded_profile["profile_id"],
            parent_id=seeded_profile["parent_id"],
        )

        remaining = _count(
            temp_db,
            f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?",
            (seeded_profile["profile_id"],),
        )
        assert remaining == 0, (
            f"{table} retained rows for revoked profile — COPPA violation"
        )

    def test_revoke_cascades_messages_via_conversations(
        self, temp_db, seeded_profile
    ):
        manager = AgeVerificationManager(temp_db)

        manager.revoke_parental_consent(
            profile_id=seeded_profile["profile_id"],
            parent_id=seeded_profile["parent_id"],
        )

        remaining = _count(
            temp_db,
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (seeded_profile["conversation_id"],),
        )
        assert remaining == 0

    def test_revoke_nulls_session_profile_id_but_keeps_row(
        self, temp_db, seeded_profile
    ):
        """
        sessions.profile_id is ON DELETE SET NULL by design: the duration
        record stays for operational audit, but the child link is severed.
        """
        manager = AgeVerificationManager(temp_db)

        manager.revoke_parental_consent(
            profile_id=seeded_profile["profile_id"],
            parent_id=seeded_profile["parent_id"],
        )

        rows = temp_db.execute_query(
            "SELECT profile_id FROM sessions WHERE session_id = ?",
            (seeded_profile["session_id"],),
        )
        assert len(rows) == 1
        row = rows[0]
        profile_id_val = row["profile_id"] if isinstance(row, dict) else row[0]
        assert profile_id_val is None

    def test_revoke_writes_audit_log_entry(self, temp_db, seeded_profile):
        manager = AgeVerificationManager(temp_db)

        manager.revoke_parental_consent(
            profile_id=seeded_profile["profile_id"],
            parent_id=seeded_profile["parent_id"],
            reason="parent requested",
        )

        rows = temp_db.execute_query(
            """
            SELECT event_type, user_id, action, details
            FROM audit_log
            WHERE event_type = 'parental_consent_revoked'
            """,
            (),
        )
        assert len(rows) == 1
        row = rows[0]
        assert (row["user_id"] if isinstance(row, dict) else row[1]) == \
            seeded_profile["parent_id"]
        details = row["details"] if isinstance(row, dict) else row[3]
        assert seeded_profile["profile_id"] in details
        assert "parent requested" in details

    def test_revoke_missing_profile_returns_false_no_audit(self, temp_db):
        manager = AgeVerificationManager(temp_db)

        ok = manager.revoke_parental_consent(
            profile_id="does-not-exist",
            parent_id="par-ghost",
            reason="parent requested",
        )

        assert ok is False
        assert _count(
            temp_db,
            "SELECT COUNT(*) FROM audit_log WHERE event_type = 'parental_consent_revoked'",
            (),
        ) == 0

    def test_revoke_is_atomic_on_failure(self, temp_db, seeded_profile):
        """
        If anything inside the transaction raises, the child profile must
        remain intact — no half-cascaded state.
        """
        manager = AgeVerificationManager(temp_db)

        with patch("core.age_verification.json.dumps",
                   side_effect=RuntimeError("simulated audit failure")):
            ok = manager.revoke_parental_consent(
                profile_id=seeded_profile["profile_id"],
                parent_id=seeded_profile["parent_id"],
            )

        assert ok is False
        assert _count(
            temp_db,
            "SELECT COUNT(*) FROM child_profiles WHERE profile_id = ?",
            (seeded_profile["profile_id"],),
        ) == 1
        assert _count(
            temp_db,
            "SELECT COUNT(*) FROM conversations WHERE profile_id = ?",
            (seeded_profile["profile_id"],),
        ) == 1
