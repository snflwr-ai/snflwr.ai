#!/usr/bin/env python3
"""
SQLite to PostgreSQL Migration Script
Migrates all data from SQLite to PostgreSQL
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    print("Error: psycopg2 is not installed")
    print("Install with: pip install psycopg2-binary")
    sys.exit(1)

from config import system_config


def _mask_secret(value: str, visible: int = 4) -> str:
    """Show only last N chars of a secret for verification."""
    s = str(value)
    if len(s) <= visible:
        return '***'
    return f"***{s[-visible:]}"


class DatabaseMigrator:
    """Migrates data from SQLite to PostgreSQL"""

    def __init__(self, sqlite_path: Path, pg_config: dict):
        self.sqlite_path = sqlite_path
        self.pg_config = pg_config
        self.sqlite_conn = None
        self.pg_conn = None

        # Tables to migrate in order (respecting foreign keys)
        self.tables = [
            'accounts',
            'auth_tokens',
            'child_profiles',
            'sessions',
            'messages',
            'safety_incidents',
            'parent_alerts',
            'usage_quotas',
            'parental_controls',
            'activity_log',
            'safety_filter_cache',
            'model_usage',
            'system_settings',
            'error_tracking',
            'audit_log'
        ]

    def connect_databases(self):
        """Establish connections to both databases"""
        print("\nConnecting to databases...")

        # Connect to SQLite
        if not self.sqlite_path.exists():
            raise FileNotFoundError(f"SQLite database not found: {self.sqlite_path}")

        self.sqlite_conn = sqlite3.connect(str(self.sqlite_path))
        self.sqlite_conn.row_factory = sqlite3.Row
        print(f"[OK] Connected to SQLite: {self.sqlite_path}")

        # Connect to PostgreSQL
        self.pg_conn = psycopg2.connect(**self.pg_config)
        print(f"[OK] Connected to PostgreSQL: ***@{self.pg_config['host']}:{self.pg_config['port']}/{self.pg_config['database']}")

    def convert_value(self, value, column_name: str):
        """Convert SQLite values to PostgreSQL format"""
        if value is None:
            return None

        # Convert SQLite integers (0/1) to PostgreSQL booleans
        if column_name in ['is_active', 'email_verified', 'parent_notified',
                           'acknowledged', 'requires_action', 'require_approval',
                           'enable_web_search', 'enable_file_upload',
                           'enable_code_execution', 'is_safe', 'success',
                           'resolved', 'filtered', 'email_notifications_enabled']:
            return bool(value)

        # Keep timestamps as strings (PostgreSQL will convert)
        if column_name in ['created_at', 'last_login', 'expires_at', 'started_at',
                           'ended_at', 'timestamp', 'cached_at', 'last_used',
                           'updated_at', 'first_seen', 'last_seen', 'resolved_at',
                           'acknowledged_at', 'parent_notified_at', 'sent_at',
                           'reset_at']:
            return value

        return value

    def migrate_table(self, table_name: str):
        """Migrate a single table"""
        # Validate table name against whitelist to prevent SQL injection
        if table_name not in self.tables:
            raise ValueError(f"Invalid table name: {table_name}. Must be one of {self.tables}")

        print(f"\nMigrating table: {table_name}")

        sqlite_cursor = self.sqlite_conn.cursor()
        pg_cursor = self.pg_conn.cursor()

        try:
            # Check if table exists in SQLite
            sqlite_cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            if not sqlite_cursor.fetchone():
                print(f"  ⊘ Table {table_name} does not exist in SQLite, skipping")
                return

            # Get column names from SQLite
            sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [row[1] for row in sqlite_cursor.fetchall()]

            # Count rows
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = sqlite_cursor.fetchone()[0]

            if row_count == 0:
                print(f"  ⊘ Table {table_name} is empty, skipping")
                return

            print(f"  → Found {row_count} rows to migrate")

            # Fetch all data from SQLite
            sqlite_cursor.execute(f"SELECT * FROM {table_name}")
            rows = sqlite_cursor.fetchall()

            # Prepare INSERT statement for PostgreSQL
            placeholders = ', '.join(['%s'] * len(columns))
            insert_sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"

            # Convert and insert data
            converted_rows = []
            for row in rows:
                converted_row = tuple(
                    self.convert_value(row[col], col) for col in columns
                )
                converted_rows.append(converted_row)

            # Batch insert for performance
            execute_batch(pg_cursor, insert_sql, converted_rows, page_size=100)
            self.pg_conn.commit()

            print(f"  [OK] Migrated {len(converted_rows)} rows successfully")

        except Exception as e:
            self.pg_conn.rollback()
            print(f"  [FAIL] Error migrating table {table_name}: {e}")
            raise

    def verify_migration(self):
        """Verify data was migrated correctly"""
        print("\n" + "=" * 70)
        print("Verifying Migration")
        print("=" * 70)

        sqlite_cursor = self.sqlite_conn.cursor()
        pg_cursor = self.pg_conn.cursor()

        results = []
        all_match = True

        for table in self.tables:
            try:
                # Count in SQLite
                sqlite_cursor.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)
                )
                if not sqlite_cursor.fetchone():
                    continue

                sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                sqlite_count = sqlite_cursor.fetchone()[0]

                # Count in PostgreSQL
                pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
                pg_count = pg_cursor.fetchone()[0]

                match = sqlite_count == pg_count
                if not match:
                    all_match = False

                status = "[OK]" if match else "[FAIL]"
                results.append({
                    'table': table,
                    'sqlite': sqlite_count,
                    'postgresql': pg_count,
                    'match': match,
                    'status': status
                })

            except Exception as e:
                print(f"  Error verifying {table}: {e}")

        # Print results
        print(f"\n{'Table':<25} {'SQLite':<10} {'PostgreSQL':<12} {'Status'}")
        print("-" * 70)
        for r in results:
            print(f"{r['table']:<25} {r['sqlite']:<10} {r['postgresql']:<12} {r['status']}")

        return all_match

    def close_connections(self):
        """Close database connections"""
        if self.sqlite_conn:
            self.sqlite_conn.close()
        if self.pg_conn:
            self.pg_conn.close()
        print("\n[OK] Connections closed")


def main():
    """Main migration function"""

    print("=" * 70)
    print("SQLite to PostgreSQL Migration")
    print("=" * 70)

    # Get configuration
    sqlite_path = system_config.DB_PATH
    pg_config = {
        'host': system_config.POSTGRES_HOST,
        'port': system_config.POSTGRES_PORT,
        'database': system_config.POSTGRES_DB,
        'user': system_config.POSTGRES_USER,
        'password': system_config.POSTGRES_PASSWORD
    }

    print(f"\nSource (SQLite):")
    print(f"  Path: {sqlite_path}")

    print(f"\nDestination (PostgreSQL):")
    print(f"  Host: {pg_config['host']}:{pg_config['port']}")
    print(f"  Database: {pg_config['database']}")
    print(f"  User: {_mask_secret(pg_config['user'])}")

    # Validate
    if not pg_config['password']:
        print("\n[FAIL] Error: POSTGRES_PASSWORD not set")
        print("Set it in .env.production or export POSTGRES_PASSWORD=your_password")
        sys.exit(1)

    # Confirm
    print("\n" + "=" * 70)
    print("[WARN]  WARNING: This will DELETE all existing data in PostgreSQL")
    print("=" * 70)
    response = input("\nContinue with migration? (yes/no): ")
    if response.lower() != 'yes':
        print("Migration cancelled")
        sys.exit(0)

    # Create migrator
    migrator = DatabaseMigrator(sqlite_path, pg_config)

    try:
        # Connect
        migrator.connect_databases()

        # Migrate each table
        print("\n" + "=" * 70)
        print("Migrating Data")
        print("=" * 70)

        for table in migrator.tables:
            migrator.migrate_table(table)

        # Verify
        all_match = migrator.verify_migration()

        # Summary
        print("\n" + "=" * 70)
        if all_match:
            print("[OK] Migration Completed Successfully!")
        else:
            print("[WARN]  Migration Completed with Warnings")
            print("Some table counts don't match. Review the verification table above.")
        print("=" * 70)

        print("\nNext steps:")
        print("  1. Review the verification table above")
        print("  2. Test the application with DB_TYPE=postgresql")
        print("  3. Run load tests to verify performance improvements")
        print("  4. If everything works, keep PostgreSQL; otherwise restore SQLite")
        print("\n" + "=" * 70)

    except Exception as e:
        print("\n" + "=" * 70)
        print("[FAIL] Migration Failed")
        print("=" * 70)
        print(f"\nError: {e}")
        print("\nThe PostgreSQL database may be in an inconsistent state.")
        print("You may need to reinitialize it: python database/init_db_postgresql.py")
        sys.exit(1)

    finally:
        migrator.close_connections()


if __name__ == '__main__':
    main()
