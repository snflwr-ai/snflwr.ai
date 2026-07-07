"""0004 — encrypt child PII (name, birthdate) at the application layer.

SQLCipher protects only the SQLite path; in Postgres/k8s mode a child's name and
birthdate were cleartext columns at rest. This adds encrypted_name / name_hash /
encrypted_birthdate, backfills them from the existing plaintext, then redacts the
plaintext columns (name -> placeholder, birthdate -> NULL).

Idempotent: only rows whose encrypted_name IS NULL are migrated, and a row already
holding the name placeholder is skipped. See core/profile_manager/field_crypto.py.
"""

from core.profile_manager import field_crypto

revision = "0004"
name = "encrypt_child_pii"

_COLS = ("encrypted_name", "name_hash", "encrypted_birthdate")


def _sqlite_has_column(cursor, table, column) -> bool:
    return any(r[1] == column for r in cursor.execute(f"PRAGMA table_info({table})"))


def up(cursor, dialect):
    # 1. Add the encrypted columns (idempotent across dialects).
    if dialect == "postgresql":
        for c in _COLS:
            cursor.execute(
                f"ALTER TABLE child_profiles ADD COLUMN IF NOT EXISTS {c} TEXT"
            )
        ph = "%s"
    else:
        for c in _COLS:
            if not _sqlite_has_column(cursor, "child_profiles", c):
                cursor.execute(f"ALTER TABLE child_profiles ADD COLUMN {c} TEXT")
        ph = "?"

    # 2. Backfill: encrypt the existing plaintext for rows not yet migrated.
    cursor.execute(
        "SELECT profile_id, name, birthdate FROM child_profiles "
        "WHERE encrypted_name IS NULL"
    )
    rows = cursor.fetchall()
    for row in rows:
        # Robust to tuple rows (sqlite) and dict/RealDict rows (postgres).
        if isinstance(row, dict):
            profile_id, name_val, birthdate_val = (
                row.get("profile_id"),
                row.get("name"),
                row.get("birthdate"),
            )
        else:
            profile_id, name_val, birthdate_val = row[0], row[1], row[2]

        if name_val == field_crypto.NAME_PLACEHOLDER:
            continue  # already redacted; nothing real left to encrypt

        cursor.execute(
            f"UPDATE child_profiles SET encrypted_name = {ph}, name_hash = {ph}, "
            f"encrypted_birthdate = {ph}, name = {ph}, birthdate = NULL "
            f"WHERE profile_id = {ph}",
            (
                field_crypto.encrypt_name(name_val or ""),
                field_crypto.hash_name(name_val or ""),
                field_crypto.encrypt_birthdate(birthdate_val),
                field_crypto.NAME_PLACEHOLDER,
                profile_id,
            ),
        )
