"""0002 makes safety_incidents.profile_id nullable while preserving the FKs,
the severity CHECK, indexes, and existing rows."""
import importlib.util
import sqlite3

_spec = importlib.util.spec_from_file_location(
    "m0002", "database/migrations/0002_nullable_incident_profile.py"
)
m0002 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0002)


def _seed_baseline(conn):
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE child_profiles (profile_id TEXT PRIMARY KEY, name TEXT)"
    )
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
    conn.execute(
        """CREATE TABLE safety_incidents (
            incident_id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL,
            session_id TEXT, incident_type TEXT NOT NULL, severity TEXT NOT NULL,
            content_snippet TEXT, timestamp TEXT NOT NULL,
            parent_notified BOOLEAN DEFAULT FALSE, parent_notified_at TEXT,
            resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT, resolution_notes TEXT,
            metadata TEXT,
            FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
            CONSTRAINT valid_severity CHECK (severity IN ('minor','major','critical'))
        )"""
    )
    conn.execute("INSERT INTO child_profiles VALUES ('real-1', 'Kid')")
    conn.execute(
        "INSERT INTO safety_incidents (profile_id, incident_type, severity, timestamp) "
        "VALUES ('real-1', 'self_harm', 'critical', '2026-01-01T00:00:00Z')"
    )
    conn.commit()


def test_profile_id_becomes_nullable_and_preserves_everything():
    conn = sqlite3.connect(":memory:")
    _seed_baseline(conn)
    m0002.up(conn.cursor(), "sqlite")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")

    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(safety_incidents)")}
    assert cols["profile_id"][3] == 0  # notnull flag now 0 (nullable)
    # existing row preserved
    assert conn.execute(
        "SELECT COUNT(*) FROM safety_incidents WHERE profile_id='real-1'"
    ).fetchone()[0] == 1
    # both FKs preserved
    fks = list(conn.execute("PRAGMA foreign_key_list(safety_incidents)"))
    reffed = {fk[2] for fk in fks}
    assert {"child_profiles", "sessions"} <= reffed
    # severity CHECK preserved
    try:
        conn.execute(
            "INSERT INTO safety_incidents (profile_id, incident_type, severity, timestamp) "
            "VALUES ('real-1','x','bogus','t')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "severity CHECK should still reject a bad value"
    # a NULL profile_id is now allowed (the whole point)
    conn.execute(
        "INSERT INTO safety_incidents (profile_id, incident_type, severity, timestamp) "
        "VALUES (NULL, 'self_harm', 'critical', 't')"
    )
    # a NON-NULL bad profile_id is still FK-rejected
    try:
        conn.execute(
            "INSERT INTO safety_incidents (profile_id, incident_type, severity, timestamp) "
            "VALUES ('nope', 'x', 'minor', 't')"
        )
        fk_raised = False
    except sqlite3.IntegrityError:
        fk_raised = True
    assert fk_raised
    # indexes recreated
    idx = {r[1] for r in conn.execute("PRAGMA index_list(safety_incidents)")}
    assert {
        "idx_incidents_profile",
        "idx_incidents_severity",
        "idx_incidents_timestamp",
        "idx_incidents_unresolved",
    } <= idx


def test_postgres_branch_emits_drop_not_null():
    calls = []

    class FakeCursor:
        def execute(self, sql, *a):
            calls.append(sql)

    m0002.up(FakeCursor(), "postgresql")
    assert any("DROP NOT NULL" in s and "profile_id" in s for s in calls)
