"""0004 encrypts child name/birthdate, backfills existing rows, and redacts the
plaintext columns — leaving no cleartext child PII in the table."""
import importlib.util
import sqlite3

_spec = importlib.util.spec_from_file_location(
    "m0004", "database/migrations/0004_encrypt_child_pii.py"
)
m0004 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0004)

from core.profile_manager import field_crypto


def _seed_pre_migration(conn):
    # Pre-migration schema: plaintext name/birthdate, no encrypted columns.
    conn.execute(
        "CREATE TABLE child_profiles ("
        "profile_id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL, birthdate TEXT)"
    )
    conn.execute(
        "INSERT INTO child_profiles VALUES ('p1', 'par1', 'Alice', '2013-05-01')"
    )
    conn.execute("INSERT INTO child_profiles VALUES ('p2', 'par1', 'Bob', NULL)")
    conn.commit()


def test_0004_encrypts_backfills_and_redacts():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_pre_migration(conn)

    m0004.up(conn.cursor(), "sqlite")
    conn.commit()

    rows = {r["profile_id"]: r for r in conn.execute("SELECT * FROM child_profiles")}

    a = rows["p1"]
    # plaintext columns redacted
    assert a["name"] == field_crypto.NAME_PLACEHOLDER
    assert a["birthdate"] is None
    # encrypted columns backfilled and decrypt to the originals
    assert field_crypto.decrypt_name(a["encrypted_name"], None) == "Alice"
    assert field_crypto.decrypt_birthdate(a["encrypted_birthdate"], None) == "2013-05-01"
    assert a["name_hash"] == field_crypto.hash_name("Alice")

    # a child with no birthdate is handled (encrypted_birthdate stays NULL)
    b = rows["p2"]
    assert field_crypto.decrypt_name(b["encrypted_name"], None) == "Bob"
    assert b["encrypted_birthdate"] is None

    # NO cleartext child PII remains anywhere in the raw table dump
    raw = " ".join(
        str(v)
        for row in conn.execute("SELECT * FROM child_profiles")
        for v in tuple(row)
    )
    assert "Alice" not in raw
    assert "Bob" not in raw
    assert "2013-05-01" not in raw


def test_0004_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_pre_migration(conn)

    m0004.up(conn.cursor(), "sqlite")
    conn.commit()
    # Second application must NOT double-encrypt or re-touch already-migrated rows.
    m0004.up(conn.cursor(), "sqlite")
    conn.commit()

    r = conn.execute(
        "SELECT * FROM child_profiles WHERE profile_id = 'p1'"
    ).fetchone()
    assert r["name"] == field_crypto.NAME_PLACEHOLDER
    assert field_crypto.decrypt_name(r["encrypted_name"], None) == "Alice"
