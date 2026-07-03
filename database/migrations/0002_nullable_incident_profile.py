"""0002 — make safety_incidents.profile_id nullable so a crisis can be recorded
even when the profile isn't a valid child_profiles reference (synthetic /
unknown / deleted). Real profiles keep the FK + ON DELETE CASCADE; profile-less
incidents record unlinked (profile_id NULL). Idempotent; irreversible."""

revision = "0002"
name = "nullable_incident_profile"

_NEW_TABLE = """
CREATE TABLE safety_incidents_new (
    incident_id INTEGER PRIMARY KEY,
    profile_id TEXT,
    session_id TEXT,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    content_snippet TEXT,
    timestamp TEXT NOT NULL,
    parent_notified BOOLEAN DEFAULT FALSE,
    parent_notified_at TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TEXT,
    resolution_notes TEXT,
    metadata TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
    CONSTRAINT valid_severity CHECK (severity IN ('minor', 'major', 'critical'))
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_incidents_profile ON safety_incidents(profile_id)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON safety_incidents(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_severity ON safety_incidents(severity)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_unresolved ON safety_incidents(resolved) WHERE NOT resolved",
]


def _sqlite_profile_id_already_nullable(cursor) -> bool:
    for row in cursor.execute("PRAGMA table_info(safety_incidents)"):
        if row[1] == "profile_id":
            return row[3] == 0  # notnull flag == 0 -> already nullable
    return True  # table absent -> nothing to do


def up(cursor, dialect):
    if dialect == "postgresql":
        cursor.execute(
            "ALTER TABLE safety_incidents ALTER COLUMN profile_id DROP NOT NULL"
        )
        return

    # sqlite: cannot ALTER COLUMN drop NOT NULL -> recreate the table.
    if _sqlite_profile_id_already_nullable(cursor):
        return  # idempotent no-op
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.execute(_NEW_TABLE)
    cursor.execute(
        "INSERT INTO safety_incidents_new SELECT "
        "incident_id, profile_id, session_id, incident_type, severity, "
        "content_snippet, timestamp, parent_notified, parent_notified_at, "
        "resolved, resolved_at, resolution_notes, metadata FROM safety_incidents"
    )
    cursor.execute("DROP TABLE safety_incidents")
    cursor.execute("ALTER TABLE safety_incidents_new RENAME TO safety_incidents")
    for idx in _INDEXES:
        cursor.execute(idx)
    cursor.execute("PRAGMA foreign_keys=ON")
