"""
Database Initialization Script
Initializes database schema for snflwr.ai
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config, get_database_url
from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


def init_database():
    """Initialize database with schema (supports both SQLite and PostgreSQL)"""
    try:
        logger.info("Initializing snflwr.ai database...")
        logger.info(f"Database type: {system_config.DB_TYPE}")

        # Read schema file
        schema_file = Path(__file__).parent / "schema.sql"
        if not schema_file.exists():
            logger.error(f"Schema file not found: {schema_file}")
            return False

        with open(schema_file, 'r') as f:
            schema_sql = f.read()

        if system_config.DB_TYPE == 'postgresql':
            return _init_postgresql(schema_sql)
        else:
            return _init_sqlite(schema_sql)

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False


def _init_sqlite(schema_sql):
    """Initialize SQLite database."""
    import sqlite3

    db_path = Path(system_config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"SQLite database path: {db_path}")

    # Run column-diffing migration first so that existing tables gain any
    # new columns before executescript tries to create indexes on them.
    # This avoids "no such column" errors when indexes reference columns
    # that exist in the schema but not yet in the live database.
    try:
        from database.migrate import run_schema_migration
        run_schema_migration()
    except Exception as mig_err:
        logger.warning(f"Pre-init migration step encountered an issue: {mig_err}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(schema_sql)
        conn.commit()
        logger.info("SQLite database schema initialized successfully")
        return True
    finally:
        conn.close()


def _init_postgresql(schema_sql):
    """Initialize PostgreSQL database."""
    try:
        import psycopg2
    except ImportError:
        logger.error(
            "psycopg2 is required for PostgreSQL. "
            "Install it with: pip install psycopg2-binary"
        )
        return False

    logger.info(
        f"PostgreSQL: {system_config.POSTGRES_HOST}:{system_config.POSTGRES_PORT}"
        f"/{system_config.POSTGRES_DB}"
    )

    conn = psycopg2.connect(
        host=system_config.POSTGRES_HOST,
        port=system_config.POSTGRES_PORT,
        dbname=system_config.POSTGRES_DB,
        user=system_config.POSTGRES_USER,
        password=system_config.POSTGRES_PASSWORD,
        sslmode=system_config.POSTGRES_SSLMODE,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Split and execute statements individually (PostgreSQL
            # does not support executescript).
            statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
            for stmt in statements:
                # Skip SQLite-specific PRAGMAs
                if stmt.upper().startswith("PRAGMA"):
                    continue
                cur.execute(stmt)
        logger.info("PostgreSQL database schema initialized successfully")
        return True
    finally:
        conn.close()


def verify_tables():
    """Verify all expected tables exist (SQLite and PostgreSQL)"""
    try:
        logger.info("Verifying database tables...")

        expected_tables = [
            'accounts',
            'child_profiles',
            'profile_subjects',
            'sessions',
            'conversations',
            'messages',
            'safety_incidents',
            'parent_alerts',
            'auth_tokens',
            'audit_log',
            'learning_analytics',
            'parental_consent_log',
        ]

        if system_config.DB_TYPE == 'postgresql':
            existing_tables = _list_tables_postgresql()
        else:
            existing_tables = _list_tables_sqlite()

        missing_tables = set(expected_tables) - set(existing_tables)

        if missing_tables:
            logger.error(f"Missing tables: {', '.join(missing_tables)}")
            return False

        logger.info(f"All {len(expected_tables)} tables verified")
        return True

    except Exception as e:
        logger.error(f"Table verification failed: {e}")
        return False


def _list_tables_sqlite():
    import sqlite3
    conn = sqlite3.connect(str(system_config.DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def _list_tables_postgresql():
    import psycopg2
    conn = psycopg2.connect(
        host=system_config.POSTGRES_HOST,
        port=system_config.POSTGRES_PORT,
        dbname=system_config.POSTGRES_DB,
        user=system_config.POSTGRES_USER,
        password=system_config.POSTGRES_PASSWORD,
        sslmode=system_config.POSTGRES_SSLMODE,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def add_default_data():
    """Add default system data using the DB adapter abstraction."""
    try:
        logger.info("Adding default system data...")
        from datetime import datetime, timezone
        from storage.database import db_manager

        # Check if admin user exists
        rows = db_manager.execute_query(
            "SELECT COUNT(*) as count FROM accounts WHERE role = 'admin'", ()
        )
        admin_count = list(rows[0].values())[0] if rows else 0

        if admin_count == 0:
            logger.info("No admin user found. Please create one using the CLI.")
            logger.info("Command: python -m scripts.create_admin")

        # Add default system settings
        default_settings = [
            ('safety_monitoring_enabled', 'true', 'boolean', 'Enable safety monitoring'),
            ('parent_alerts_enabled', 'true', 'boolean', 'Enable parent email alerts'),
            ('max_daily_messages_default', '100', 'integer', 'Default daily message limit'),
            ('session_timeout_minutes', '60', 'integer', 'Session timeout in minutes'),
        ]

        for key, value, stype, desc in default_settings:
            rows = db_manager.execute_query(
                "SELECT COUNT(*) as count FROM system_settings WHERE setting_key = ?",
                (key,)
            )
            if rows and list(rows[0].values())[0] == 0:
                db_manager.execute_write(
                    "INSERT INTO system_settings "
                    "(setting_key, setting_value, setting_type, description, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, value, stype, desc, datetime.now(timezone.utc).isoformat())
                )
                logger.debug(f"Added setting: {key}")

        logger.info("Default data added")
        return True

    except Exception as e:
        logger.error(f"Failed to add default data: {e}")
        return False


def main():
    """Main initialization function"""
    print("=" * 60)
    print("snflwr.ai - Database Initialization")
    print("=" * 60)

    # Initialize database
    if not init_database():
        print("\n❌ Database initialization FAILED")
        return 1

    # Verify tables
    if not verify_tables():
        print("\n❌ Table verification FAILED")
        return 1

    # Add default data
    if not add_default_data():
        print("\n⚠️  Default data creation had warnings")

    print("\n" + "=" * 60)
    print("✓ Database initialization completed successfully")
    print("=" * 60)
    print(f"\nDatabase location: {system_config.DB_PATH}")
    print("\nNext steps:")
    print("1. Create an admin user: python -m scripts.create_admin")
    print("2. Start the API server: python -m api.server")
    print("3. Access the application")

    return 0


if __name__ == "__main__":
    sys.exit(main())
