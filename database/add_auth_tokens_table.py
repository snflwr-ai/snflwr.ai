#!/usr/bin/env python3
"""
Add auth_tokens table to existing database
Supports both SQLite and PostgreSQL
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import db_manager
from config import system_config

def migrate_sqlite():
    """Add auth_tokens table to SQLite database"""
    print("Adding auth_tokens table to SQLite...")

    # Check if table exists
    result = db_manager.execute_read(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_tokens'"
    )

    if result:
        print("[OK] auth_tokens table already exists")
        return

    # Create table
    db_manager.execute_write("""
        CREATE TABLE auth_tokens (
            token_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_type TEXT NOT NULL CHECK (token_type IN ('email_verification', 'password_reset')),
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            is_valid INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # Create indexes
    db_manager.execute_write("CREATE INDEX idx_tokens_user ON auth_tokens(user_id)")
    db_manager.execute_write("CREATE INDEX idx_tokens_hash ON auth_tokens(token_hash)")
    db_manager.execute_write("CREATE INDEX idx_tokens_type ON auth_tokens(token_type)")
    db_manager.execute_write("CREATE INDEX idx_tokens_expires ON auth_tokens(expires_at)")
    db_manager.execute_write("CREATE INDEX idx_tokens_valid ON auth_tokens(is_valid) WHERE is_valid = 1")

    print("[OK] auth_tokens table created successfully")

def migrate_postgresql():
    """Add auth_tokens table to PostgreSQL database"""
    print("Adding auth_tokens table to PostgreSQL...")

    # Check if table exists
    result = db_manager.execute_read(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'auth_tokens'"
    )

    if result:
        print("[OK] auth_tokens table already exists")
        return

    # Create table
    db_manager.execute_write("""
        CREATE TABLE auth_tokens (
            token_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_type TEXT NOT NULL CHECK (token_type IN ('email_verification', 'password_reset')),
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            is_valid BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # Create indexes
    db_manager.execute_write("CREATE INDEX idx_tokens_user ON auth_tokens(user_id)")
    db_manager.execute_write("CREATE INDEX idx_tokens_hash ON auth_tokens(token_hash)")
    db_manager.execute_write("CREATE INDEX idx_tokens_type ON auth_tokens(token_type)")
    db_manager.execute_write("CREATE INDEX idx_tokens_expires ON auth_tokens(expires_at)")
    db_manager.execute_write("CREATE INDEX idx_tokens_valid ON auth_tokens(is_valid) WHERE is_valid = TRUE")

    print("[OK] auth_tokens table created successfully")

def main():
    print("=" * 60)
    print("Auth Tokens Table Migration")
    print("=" * 60)

    db_type = system_config.DB_TYPE
    print(f"\nDatabase type: {db_type}")

    try:
        if db_type == 'postgresql':
            migrate_postgresql()
        else:
            migrate_sqlite()

        print("\n" + "=" * 60)
        print("[OK] Migration completed successfully!")
        print("=" * 60)

    except Exception as e:
        print("\n" + "=" * 60)
        print("[FAIL] Migration failed!")
        print("=" * 60)
        print(f"\nError: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
