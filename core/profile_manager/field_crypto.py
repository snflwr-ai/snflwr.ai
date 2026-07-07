"""App-layer encryption for child-profile PII columns (name, birthdate).

SQLCipher protects the SQLite path, but in Postgres/k8s mode (the school/FERPA
deploy) table columns are cleartext at rest. So we encrypt a child's name and
birthdate at the application layer — deploy-independent, mirroring the
``encrypted_email``/``email_hash`` pattern in ``core/email_crypto.py``:

- ``encrypted_name`` / ``encrypted_birthdate`` — Fernet ciphertext (confidential).
- ``name_hash`` — deterministic SHA-256, ONLY for the within-family duplicate-name
  lookup (which can no longer query the plaintext ``name`` column).

The plaintext ``name`` column keeps a non-PII placeholder (it is NOT NULL); the
plaintext ``birthdate`` column is set NULL. Reads fall back to the plaintext
column when the encrypted column is absent (un-migrated rows during rollout).
"""

import hashlib
from typing import Optional

from storage.encryption import encryption_manager

# Written to the plaintext ``name`` column (NOT NULL) once the real name lives in
# ``encrypted_name``. Non-PII; a decrypt-on-read fallback treats it as absent.
NAME_PLACEHOLDER = "[encrypted]"


def hash_name(name: str) -> str:
    """Deterministic hash for the within-family duplicate-name check."""
    return hashlib.sha256((name or "").strip().lower().encode()).hexdigest()


def encrypt_name(name: str) -> str:
    return encryption_manager.encrypt_string(name)


def encrypt_birthdate(birthdate: Optional[str]) -> Optional[str]:
    return encryption_manager.encrypt_string(birthdate) if birthdate else None


def _decrypt(encrypted: Optional[str], fallback: Optional[str]) -> Optional[str]:
    """Decrypt, falling back to the plaintext column for un-migrated rows (or if
    the plaintext column still holds a real value and the encrypted one doesn't)."""
    if not encrypted:
        return fallback
    try:
        return encryption_manager.decrypt_string(encrypted)
    except Exception:
        return fallback


def decrypt_name(
    encrypted: Optional[str], plaintext_fallback: Optional[str]
) -> Optional[str]:
    # The placeholder is not a real name — treat it as no fallback.
    fb = None if plaintext_fallback == NAME_PLACEHOLDER else plaintext_fallback
    return _decrypt(encrypted, fb)


def decrypt_birthdate(
    encrypted: Optional[str], plaintext_fallback: Optional[str]
) -> Optional[str]:
    return _decrypt(encrypted, plaintext_fallback)
