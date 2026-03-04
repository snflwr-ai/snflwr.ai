#!/usr/bin/env python3
"""Seed minimal test data into the Snflwr SQLite database.

Usage: python scripts/seed_test_data.py
"""
from uuid import uuid4
from datetime import datetime, timezone
import sys
import os
import hashlib

# Ensure repo root is on sys.path so local packages import correctly when run inside container
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from storage.database import DatabaseManager
from config import system_config


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def seed():
    db = DatabaseManager()

    parent_id = str(uuid4())
    device_id = str(uuid4())
    profile_id = str(uuid4())
    session_id = str(uuid4())
    conversation_id = str(uuid4())
    message_id = str(uuid4())
    incident_id = str(uuid4())
    token_id = str(uuid4())

    now = iso_now()

    # Prepare email for storage: hash for lookup, no plaintext stored.
    # This is test/seed data only — uses simple hashlib rather than full
    # core.email_crypto which may require encryption keys not available
    # in every environment.
    test_email = 'parent@example.com'
    email_hash = hashlib.sha256(test_email.lower().strip().encode()).hexdigest()
    encrypted_email = None  # Not available without Fernet key; acceptable for seed data

    # Insert parent (idempotent)
    db.execute_write(
        """
        INSERT OR IGNORE INTO accounts (
            parent_id, username, password_hash, email, email_hash, encrypted_email,
            device_id, created_at, last_login
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (parent_id, 'test_parent', 'pbkdf2-testhash', None, email_hash, encrypted_email,
         device_id, now, now)
    )

    # Insert child profile (includes all required columns from schema)
    db.execute_write(
        """
        INSERT OR IGNORE INTO child_profiles (
            profile_id, parent_id, name, age, grade,
            avatar, created_at, learning_level
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (profile_id, parent_id, 'Test Child', 10, '4th',
         'default', now, 'adaptive')
    )

    # Insert a session
    db.execute_write(
        """
        INSERT OR IGNORE INTO sessions (
            session_id, profile_id, parent_id, session_type, started_at, platform
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, profile_id, parent_id, 'student', now, 'test')
    )

    # Insert a conversation and a message
    db.execute_write(
        """
        INSERT OR IGNORE INTO conversations (
            conversation_id, session_id, profile_id, created_at, updated_at, message_count, subject_area
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, session_id, profile_id, now, now, 1, 'math')
    )

    db.execute_write(
        """
        INSERT OR IGNORE INTO messages (
            message_id, conversation_id, role, content, timestamp, model_used, response_time_ms, tokens_used
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, conversation_id, 'user', 'Hello, world!', now, 'snflwr-test-model', 10, 5)
    )

    # Insert a safety incident (idempotent with OR IGNORE, includes required incident_id PK)
    db.execute_write(
        """
        INSERT OR IGNORE INTO safety_incidents (
            incident_id, profile_id, session_id, incident_type, severity, content_snippet, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (incident_id, profile_id, session_id, 'test_incident', 'minor', 'test snippet', now)
    )

    # Insert an auth token (matches auth_tokens schema: token_id, user_id, token_type, token_hash)
    # Note: auth_tokens references users table, but for seed data we insert directly
    token_value = str(uuid4())
    token_hash = hashlib.sha256(token_value.encode()).hexdigest()
    db.execute_write(
        """
        INSERT OR IGNORE INTO auth_tokens (
            token_id, user_id, token_type, token_hash, created_at, expires_at, is_valid
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (token_id, parent_id, 'email_verification', token_hash, now, now, 1)
    )

    # Print simple counts
    # Whitelist of valid tables to prevent SQL injection
    VALID_TABLES = {'accounts', 'child_profiles', 'sessions', 'conversations', 'messages', 'safety_incidents', 'auth_tokens'}

    def count(table):
        if table not in VALID_TABLES:
            raise ValueError(f"Invalid table name: {table}. Must be one of {VALID_TABLES}")
        rows = db.execute_query(f"SELECT COUNT(*) as c FROM {table}")
        return rows[0]['c'] if rows else 0

    print("Seed complete. Current counts:")
    for t in ('accounts', 'child_profiles', 'sessions', 'conversations', 'messages', 'safety_incidents', 'auth_tokens'):
        print(f"  {t}: {count(t)}")


if __name__ == '__main__':
    seed()
