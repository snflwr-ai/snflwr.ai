#!/usr/bin/env python3
"""
Add missing columns to users table (SQLite)
Migration for BLOCKER fixes #5 and #6
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import db_manager
from config import system_config

def migrate_sqlite():
    """Add name and email_notifications_enabled columns to SQLite users table"""
    print("="*60)
    print("Users Table Column Migration (SQLite)")
    print("="*60)
    print()

    # Check if columns already exist
    print("Checking existing columns...")
    columns_result = db_manager.execute_read("PRAGMA table_info(users)")

    existing_columns = [col[1] for col in columns_result] if columns_result else []
    print(f"Existing columns: {', '.join(existing_columns)}")
    print()

    has_name = 'name' in existing_columns
    has_notifications = 'email_notifications_enabled' in existing_columns

    if has_name and has_notifications:
        print("[OK] Both columns already exist - no migration needed")
        return

    # Add name column if missing
    if not has_name:
        print("Adding 'name' column...")
        try:
            db_manager.execute_write(
                "ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'User'"
            )
            print("[OK] 'name' column added successfully")
        except Exception as e:
            print(f"[FAIL] Failed to add 'name' column: {e}")
            return
    else:
        print("[OK] 'name' column already exists")

    # Add email_notifications_enabled column if missing
    if not has_notifications:
        print("Adding 'email_notifications_enabled' column...")
        try:
            db_manager.execute_write(
                "ALTER TABLE users ADD COLUMN email_notifications_enabled INTEGER DEFAULT 1"
            )
            print("[OK] 'email_notifications_enabled' column added successfully")
        except Exception as e:
            print(f"[FAIL] Failed to add 'email_notifications_enabled' column: {e}")
            return
    else:
        print("[OK] 'email_notifications_enabled' column already exists")

    print()
    print("="*60)
    print("[OK] Migration completed successfully!")
    print("="*60)
    print()
    print("Fixed blockers:")
    print("  #5: SQLite schema now has 'name' column")
    print("  #6: SQLite schema now has 'email_notifications_enabled' column")
    print()
    print("Password reset and safety alerts will now work on SQLite!")

def main():
    """Run migration"""
    print()
    print("Database type:", system_config.DB_TYPE)

    if system_config.DB_TYPE == 'sqlite':
        migrate_sqlite()
    else:
        print()
        print("This migration is for SQLite only.")
        print("PostgreSQL schema already has these columns.")
        print()

    return 0

if __name__ == "__main__":
    sys.exit(main())
