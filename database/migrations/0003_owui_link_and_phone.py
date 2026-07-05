"""0003 — separate child chat identity from family key.

Adds a partial UNIQUE index on child_profiles.owui_user_id (one OWUI chat login
maps to exactly one child; NULLs unconstrained) and a nullable accounts.phone
column (stored now for later self-serve/SMS; unused this milestone).
Idempotent; both columns/indexes already-exists-safe."""

revision = "0003"
name = "owui_link_and_phone"

_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_child_profiles_owui_user_id "
    "ON child_profiles(owui_user_id) WHERE owui_user_id IS NOT NULL"
)


def _sqlite_has_column(cursor, table, column) -> bool:
    return any(r[1] == column for r in cursor.execute(f"PRAGMA table_info({table})"))


def up(cursor, dialect):
    if dialect == "postgresql":
        cursor.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS phone TEXT")
        cursor.execute(_INDEX_SQL)
        return

    # sqlite: ADD COLUMN is not IF-NOT-EXISTS-aware -> guard manually.
    if not _sqlite_has_column(cursor, "accounts", "phone"):
        cursor.execute("ALTER TABLE accounts ADD COLUMN phone TEXT")
    cursor.execute(_INDEX_SQL)
