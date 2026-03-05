#!/usr/bin/env python3
"""
Database Encryption Migration Tool

Converts an existing unencrypted SQLite database to an encrypted SQLCipher database.

Usage:
    python scripts/database/encrypt_database.py --source data/snflwr.db --key "your-encryption-key"

Features:
- Migrates all data from unencrypted to encrypted database
- Verifies data integrity after migration
- Creates backup of original database
- Supports rollback if migration fails
"""

import sys
import os
import argparse
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from pysqlcipher3 import dbapi2 as sqlcipher
    SQLCIPHER_AVAILABLE = True
except ImportError:
    SQLCIPHER_AVAILABLE = False
    sqlcipher = None  # Set to None so we can check later

from utils.logger import get_logger

logger = get_logger(__name__)


def backup_database(source_path: Path) -> Path:
    """
    Create a backup of the source database
    
    Args:
        source_path: Path to source database
    
    Returns:
        Path to backup file
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = source_path.parent / f"{source_path.stem}_backup_{timestamp}.db"
    
    print(f"Creating backup: {backup_path}")
    shutil.copy2(source_path, backup_path)
    print(f"[OK] Backup created successfully")
    
    return backup_path


def get_table_count(connection, table_name: str) -> int:
    """Get row count for a table"""
    cursor = connection.cursor()
    # Quote identifier to prevent injection
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    count = cursor.fetchone()[0]
    cursor.close()
    return count


def migrate_to_encrypted(
    source_db: Path,
    encryption_key: str,
    kdf_iter: int = 256000,
    verify: bool = True
) -> bool:
    """
    Migrate unencrypted database to encrypted format
    
    Args:
        source_db: Path to unencrypted source database
        encryption_key: Encryption key for new database
        kdf_iter: PBKDF2 iterations
        verify: Whether to verify data after migration
    
    Returns:
        True if migration successful, False otherwise
    """
    if not source_db.exists():
        print(f"ERROR: Source database not found: {source_db}")
        return False
    
    # Create backup first
    backup_path = backup_database(source_db)
    
    # Temporary encrypted database path
    encrypted_db = source_db.parent / f"{source_db.stem}_encrypted.db"
    
    print(f"\nMigrating {source_db} to encrypted format...")
    print(f"Encryption: AES-256 with PBKDF2 ({kdf_iter:,} iterations)")
    
    try:
        # Connect to source (unencrypted)
        print("\n[1/5] Connecting to source database...")
        source_conn = sqlite3.connect(str(source_db))
        source_conn.row_factory = sqlite3.Row
        
        # Get list of all tables
        cursor = source_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        
        print(f"[OK] Found {len(tables)} tables: {', '.join(tables)}")
        
        # Count total rows
        total_rows = {}
        for table in tables:
            total_rows[table] = get_table_count(source_conn, table)
        
        print(f"[OK] Total rows to migrate: {sum(total_rows.values()):,}")
        
        # Create encrypted database
        print("\n[2/5] Creating encrypted database...")
        dest_conn = sqlcipher.connect(str(encrypted_db))

        # Set encryption key (escape single quotes by doubling them)
        escaped_key = encryption_key.replace("'", "''")
        dest_conn.execute(f"PRAGMA key = '{escaped_key}'")
        dest_conn.execute(f"PRAGMA kdf_iter = {kdf_iter}")
        dest_conn.execute("PRAGMA cipher_page_size = 4096")
        dest_conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
        dest_conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
        
        print("[OK] Encrypted database created")
        
        # Migrate schema
        print("\n[3/5] Migrating database schema...")
        cursor = source_conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        schema_statements = [row[0] for row in cursor.fetchall()]
        cursor.close()
        
        for statement in schema_statements:
            try:
                dest_conn.execute(statement)
            except Exception as e:
                # Skip if already exists or other non-critical error
                logger.debug(f"Schema migration note: {e}")
        
        dest_conn.commit()
        print(f"[OK] Migrated {len(schema_statements)} schema statements")
        
        # Migrate data
        print("\n[4/5] Migrating table data...")
        for table in tables:
            row_count = total_rows[table]
            print(f"  Migrating {table} ({row_count:,} rows)...", end=" ")
            
            # Read all data from source
            source_cursor = source_conn.cursor()
            # Quote table identifier to prevent injection
            source_cursor.execute(f'SELECT * FROM "{table}"')
            rows = source_cursor.fetchall()

            # Get column names
            columns = [description[0] for description in source_cursor.description]
            source_cursor.close()

            # Insert into destination
            if rows:
                placeholders = ','.join(['?' for _ in columns])
                # Quote table and column identifiers to prevent injection
                quoted_columns = ','.join([f'"{col}"' for col in columns])
                insert_sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                
                dest_conn.executemany(insert_sql, [tuple(row) for row in rows])
                dest_conn.commit()
            
            print("[OK]")
        
        print(f"[OK] All data migrated successfully")
        
        # Verify data if requested
        if verify:
            print("\n[5/5] Verifying data integrity...")
            all_verified = True
            
            for table in tables:
                source_count = get_table_count(source_conn, table)
                dest_count = get_table_count(dest_conn, table)
                
                if source_count == dest_count:
                    print(f"  [OK] {table}: {source_count:,} rows (verified)")
                else:
                    print(f"  [FAIL] {table}: Source={source_count:,}, Dest={dest_count:,} (MISMATCH!)")
                    all_verified = False
            
            if not all_verified:
                print("\n[FAIL] DATA VERIFICATION FAILED!")
                print("  Rolling back migration...")
                dest_conn.close()
                source_conn.close()
                encrypted_db.unlink()
                print("  Encrypted database deleted")
                return False
            
            print("[OK] Data integrity verified")
        
        # Close connections
        source_conn.close()
        dest_conn.close()
        
        # Replace original with encrypted version
        print("\n[6/6] Replacing original database...")
        source_db.unlink()  # Delete original
        encrypted_db.rename(source_db)  # Rename encrypted to original name
        print(f"[OK] Database encryption complete: {source_db}")
        
        print(f"\n[OK] Migration successful!")
        print(f"   Backup saved to: {backup_path}")
        print(f"   Original database is now encrypted")
        print(f"\n[WARN]  IMPORTANT: Set DB_ENCRYPTION_KEY environment variable:")
        print(f"   export DB_ENCRYPTION_KEY='{encryption_key}'")
        
        return True
        
    except Exception as e:
        print(f"\n[FAIL] ERROR during migration: {e}")
        logger.exception("Migration failed")
        
        # Clean up temporary file
        if encrypted_db.exists():
            encrypted_db.unlink()
        
        print(f"\n Backup preserved at: {backup_path}")
        print("  You can restore from backup if needed:")
        print(f"  mv {backup_path} {source_db}")
        
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Migrate SQLite database to encrypted SQLCipher format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Encrypt existing database
  python encrypt_database.py --source data/snflwr.db

  # Encrypt with custom key
  python encrypt_database.py --source data/snflwr.db --key "my-secure-key-32-chars-minimum"

  # Encrypt without verification (faster)
  python encrypt_database.py --source data/snflwr.db --no-verify

Security Notes:
  - Use a strong encryption key (32+ characters)
  - Store encryption key securely (environment variable, secrets manager)
  - Backup is automatically created before migration
  - Original database is replaced with encrypted version
        """
    )
    
    parser.add_argument(
        '--source',
        type=Path,
        required=True,
        help='Path to source (unencrypted) database'
    )
    
    parser.add_argument(
        '--key',
        type=str,
        default=None,
        help='Encryption key (reads from DB_ENCRYPTION_KEY env if not provided)'
    )
    
    parser.add_argument(
        '--kdf-iter',
        type=int,
        default=256000,
        help='PBKDF2 iterations (default: 256000, higher = more secure but slower)'
    )
    
    parser.add_argument(
        '--no-verify',
        action='store_true',
        help='Skip data verification (faster but not recommended)'
    )
    
    args = parser.parse_args()

    # Check if SQLCipher is available (after parsing args so --help works)
    if not SQLCIPHER_AVAILABLE:
        print("ERROR: SQLCipher not available. Install with: pip install pysqlcipher3")
        print("\nTo install SQLCipher:")
        print("  macOS:   brew install sqlcipher && pip install pysqlcipher3")
        print("  Ubuntu:  sudo apt-get install libsqlcipher-dev && pip install pysqlcipher3")
        print("  Windows: pip install pysqlcipher3")
        sys.exit(1)

    # Get encryption key
    encryption_key = args.key or os.getenv('DB_ENCRYPTION_KEY')
    
    if not encryption_key:
        print("ERROR: Encryption key not provided")
        print("  Set DB_ENCRYPTION_KEY environment variable, or use --key option")
        sys.exit(1)
    
    # Validate key strength
    if len(encryption_key) < 32:
        print(f"WARNING: Encryption key is {len(encryption_key)} characters")
        print("         Recommended minimum is 32 characters for strong security")
        response = input("Continue anyway? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted")
            sys.exit(1)
    
    print("=" * 70)
    print("DATABASE ENCRYPTION MIGRATION TOOL")
    print("=" * 70)
    print(f"Source: {args.source}")
    print(f"Key Length: {len(encryption_key)} characters")
    print(f"KDF Iterations: {args.kdf_iter:,}")
    print(f"Verification: {'Disabled' if args.no_verify else 'Enabled'}")
    print("=" * 70)
    
    response = input("\nProceed with migration? (yes/no): ")
    if response.lower() != 'yes':
        print("Aborted")
        sys.exit(0)
    
    # Run migration
    success = migrate_to_encrypted(
        args.source,
        encryption_key,
        args.kdf_iter,
        verify=not args.no_verify
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
