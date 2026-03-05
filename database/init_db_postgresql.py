#!/usr/bin/env python3
"""
PostgreSQL Database Initialization Script
Creates the snflwr.ai database schema in PostgreSQL
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2 import sql
except ImportError:
    print("Error: psycopg2 is not installed")
    print("Install with: pip install psycopg2-binary")
    sys.exit(1)

from config import system_config


def create_database_if_not_exists(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str
):
    """Create database if it doesn't exist"""

    # Connect to PostgreSQL server (postgres database)
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database='postgres'  # Connect to default database
    )
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Check if database exists
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database,)
        )
        exists = cursor.fetchone()

        if not exists:
            print(f"Creating database: {database}")
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(database)
            ))
            print(f"[OK] Database '{database}' created successfully")
        else:
            print(f"[OK] Database '{database}' already exists")

    except Exception as e:
        print(f"Error creating database: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def initialize_schema(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str
):
    """Initialize database schema"""

    # Read schema file
    schema_file = Path(__file__).parent / 'schema_postgresql.sql'

    if not schema_file.exists():
        print(f"Error: Schema file not found: {schema_file}")
        sys.exit(1)

    with open(schema_file, 'r') as f:
        schema_sql = f.read()

    # Connect to the database
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database
    )

    try:
        cursor = conn.cursor()
        print(f"\nInitializing schema in database: {database}")

        # Execute schema SQL
        cursor.execute(schema_sql)
        conn.commit()

        print("[OK] Schema initialized successfully")

        # Verify tables were created
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)

        tables = cursor.fetchall()
        print(f"\n[OK] Created {len(tables)} tables:")
        for table in tables:
            print(f"  - {table[0]}")

    except Exception as e:
        conn.rollback()
        print(f"\nError initializing schema: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    """Main initialization function"""

    print("=" * 70)
    print("snflwr.ai - PostgreSQL Database Initialization")
    print("=" * 70)

    # Get configuration from environment
    host = system_config.POSTGRES_HOST
    port = system_config.POSTGRES_PORT
    user = system_config.POSTGRES_USER
    password = system_config.POSTGRES_PASSWORD
    database = system_config.POSTGRES_DB

    print(f"\nConnection Details:")
    print(f"  Host: {host}:{port}")
    print(f"  User: {user}")
    print(f"  Database: {database}")

    # Validate configuration
    if not password:
        print("\n[FAIL] Error: POSTGRES_PASSWORD not set in environment")
        print("Set it in .env.production or export POSTGRES_PASSWORD=your_password")
        sys.exit(1)

    try:
        # Step 1: Create database if needed
        print("\n" + "=" * 70)
        print("Step 1: Create Database")
        print("=" * 70)
        create_database_if_not_exists(host, port, user, password, database)

        # Step 2: Initialize schema
        print("\n" + "=" * 70)
        print("Step 2: Initialize Schema")
        print("=" * 70)
        initialize_schema(host, port, user, password, database)

        # Success
        print("\n" + "=" * 70)
        print("[OK] PostgreSQL Database Initialization Complete!")
        print("=" * 70)
        print("\nNext steps:")
        print("  1. If migrating from SQLite, run: python database/migrate_to_postgresql.py")
        print("  2. Create first admin account: python scripts/bootstrap_admin.py")
        print("  3. Start the application with DB_TYPE=postgresql")
        print("\n" + "=" * 70)

    except Exception as e:
        print("\n" + "=" * 70)
        print("[FAIL] Database Initialization Failed")
        print("=" * 70)
        print(f"\nError: {e}")
        print("\nTroubleshooting:")
        print("  1. Check PostgreSQL is running: sudo systemctl status postgresql")
        print("  2. Verify connection settings in .env.production")
        print("  3. Ensure user has database creation privileges")
        print("  4. Check PostgreSQL logs for details")
        sys.exit(1)


if __name__ == '__main__':
    main()
