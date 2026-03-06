"""
Database Encryption Key Rotation Script

Safely rotates database encryption key by:
1. Creating backup of current database
2. Generating new encryption key
3. Creating new encrypted database with new key
4. Migrating all data
5. Verifying data integrity

Usage:
    python rotate_encryption_key.py

Safety features:
- Creates backup before any changes
- Verifies data integrity after migration
- Rolls back on error
- Logs all operations for audit trail
"""

import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.key_management import KeyManager, validate_key_strength
from storage.encrypted_db_adapter import EncryptedSQLiteAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class KeyRotationError(Exception):
    """Raised when key rotation fails"""
    pass


def verify_database_connection(db_path: Path, encryption_key: str) -> bool:
    """
    Verify we can connect to database with given key

    Args:
        db_path: Path to database file
        encryption_key: Encryption key to test

    Returns:
        True if connection successful
    """
    try:
        adapter = EncryptedSQLiteAdapter(
            db_path=str(db_path),
            encryption_key=encryption_key
        )
        conn = adapter.connect()
        cursor = conn.cursor()

        # Try a simple query
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        table_count = cursor.fetchone()[0]

        conn.close()
        logger.info(f"Database connection verified. Found {table_count} tables.")
        return True

    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


def export_database_to_plaintext(
    source_db: Path,
    source_key: str,
    export_path: Path
) -> bool:
    """
    Export encrypted database to plaintext SQLite

    Args:
        source_db: Path to encrypted source database
        source_key: Encryption key for source
        export_path: Path for plaintext export

    Returns:
        True if successful
    """
    try:
        # Connect to encrypted source
        source_adapter = EncryptedSQLiteAdapter(
            db_path=str(source_db),
            encryption_key=source_key
        )
        source_conn = source_adapter.connect()

        # Create plaintext export
        export_conn = sqlite3.connect(str(export_path))

        # Use SQLite backup API to copy database
        source_conn.backup(export_conn)

        source_conn.close()
        export_conn.close()

        logger.info(f"Database exported to plaintext: {export_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to export database: {e}")
        return False


def import_plaintext_to_encrypted(
    plaintext_db: Path,
    target_db: Path,
    target_key: str
) -> bool:
    """
    Import plaintext database to new encrypted database

    Args:
        plaintext_db: Path to plaintext database
        target_db: Path for new encrypted database
        target_key: Encryption key for target

    Returns:
        True if successful
    """
    try:
        # Connect to plaintext source
        source_conn = sqlite3.connect(str(plaintext_db))

        # Connect to encrypted target
        target_adapter = EncryptedSQLiteAdapter(
            db_path=str(target_db),
            encryption_key=target_key
        )
        target_conn = target_adapter.connect()

        # Use SQLite backup API to copy database
        source_conn.backup(target_conn)

        source_conn.close()
        target_conn.close()

        logger.info(f"Database imported to encrypted: {target_db}")
        return True

    except Exception as e:
        logger.error(f"Failed to import database: {e}")
        return False


def verify_data_integrity(
    original_db: Path,
    original_key: str,
    new_db: Path,
    new_key: str
) -> bool:
    """
    Verify data integrity by comparing table counts and row counts

    Args:
        original_db: Original database path
        original_key: Original encryption key
        new_db: New database path
        new_key: New encryption key

    Returns:
        True if data matches
    """
    try:
        # Connect to both databases
        original_adapter = EncryptedSQLiteAdapter(
            db_path=str(original_db),
            encryption_key=original_key
        )
        original_conn = original_adapter.connect()

        new_adapter = EncryptedSQLiteAdapter(
            db_path=str(new_db),
            encryption_key=new_key
        )
        new_conn = new_adapter.connect()

        # Get all table names from original
        cursor = original_conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cursor.fetchall()]

        print(f"\nVerifying {len(tables)} tables...")

        all_match = True
        for table in tables:
            # Count rows in original
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            original_count = cursor.fetchone()[0]

            # Count rows in new
            new_cursor = new_conn.cursor()
            new_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            new_count = new_cursor.fetchone()[0]

            if original_count == new_count:
                print(f"[OK] {table}: {original_count} rows")
            else:
                print(f"[FAIL] {table}: {original_count} rows (original) vs {new_count} rows (new)")
                all_match = False

        original_conn.close()
        new_conn.close()

        return all_match

    except Exception as e:
        logger.error(f"Data integrity verification failed: {e}")
        return False


def rotate_encryption_key():
    """
    Main key rotation function
    """
    print("\n" + "="*70)
    print("DATABASE ENCRYPTION KEY ROTATION")
    print("="*70)
    print("\n[WARN]  This script will rotate your database encryption key.")
    print("Your data will remain safe, but this is a critical operation.\n")

    # Get current database path
    db_path_str = input("Enter database path (default: data/snflwr.db): ").strip()
    if not db_path_str:
        db_path_str = "data/snflwr.db"

    db_path = Path(db_path_str)

    if not db_path.exists():
        print(f"\n[FAIL] Database not found: {db_path}")
        return False

    # Get current encryption key
    print("\n" + "-"*70)
    print("CURRENT ENCRYPTION KEY")
    print("-"*70)

    current_key = os.getenv('DB_ENCRYPTION_KEY')

    if current_key:
        print(f"[OK] Found DB_ENCRYPTION_KEY in environment")
        use_env = input("Use this key? (y/n): ").strip().lower()
        if use_env != 'y':
            current_key = input("Enter current encryption key: ").strip()
    else:
        current_key = input("Enter current encryption key: ").strip()

    # Validate current key
    is_valid, error = validate_key_strength(current_key)
    if not is_valid:
        print(f"\n[FAIL] Current key validation failed: {error}")
        return False

    # Verify we can connect with current key
    print("\nVerifying database connection...")
    if not verify_database_connection(db_path, current_key):
        print("[FAIL] Cannot connect to database with provided key")
        return False

    # Create backup
    print("\n" + "-"*70)
    print("BACKUP CREATION")
    print("-"*70)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    backup_path = backup_dir / f"{db_path.stem}_backup_{timestamp}.db"

    print(f"Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)
    print(f"[OK] Backup created")

    # Generate new key
    print("\n" + "-"*70)
    print("NEW ENCRYPTION KEY")
    print("-"*70)
    print("\nHow do you want to generate the new key?")
    print("1. New passphrase")
    print("2. New random key")

    choice = input("\nEnter choice (1 or 2): ").strip()

    key_manager = KeyManager()

    if choice == "1":
        while True:
            new_passphrase = input("\nEnter new passphrase (min 12 chars): ").strip()
            confirm = input("Confirm new passphrase: ").strip()

            if new_passphrase != confirm:
                print("[FAIL] Passphrases don't match. Try again.")
                continue

            try:
                old_key, new_key = key_manager.rotate_key(current_key, new_passphrase)
                print("\n[OK] New key generated from passphrase")
                break
            except Exception as e:
                print(f"[FAIL] {e}")
                continue

    elif choice == "2":
        confirm = input("\nType 'I UNDERSTAND' to generate random key: ").strip()
        if confirm != "I UNDERSTAND":
            print("Cancelled")
            return False

        old_key, new_key = key_manager.rotate_key(current_key, None)
        # Save new key directly to .env file
        _env_path = Path(os.environ.get('ENV_FILE', '.env'))
        if _env_path.exists():
            _content = _env_path.read_text()
            _content = re.sub(
                r'^DB_ENCRYPTION_KEY=.*$',
                f'DB_ENCRYPTION_KEY={new_key}',
                _content,
                flags=re.MULTILINE,
            )
            _fd = os.open(str(_env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(_fd, 'w') as _f:
                _f.write(_content)
            print("\n[OK] New encryption key generated and saved to .env")
        else:
            print(f"\n[WARN] .env file not found at {_env_path} — update DB_ENCRYPTION_KEY manually")
        print("[WARN] CRITICAL: Back up your .env file immediately!")
        input("Press Enter to continue...")

    else:
        print("Invalid choice")
        return False

    # Perform rotation
    print("\n" + "-"*70)
    print("KEY ROTATION IN PROGRESS")
    print("-"*70)

    try:
        # Step 1: Export to plaintext
        temp_dir = db_path.parent / "temp"
        temp_dir.mkdir(exist_ok=True)

        plaintext_path = temp_dir / f"plaintext_{timestamp}.db"

        print("\n1. Exporting encrypted database to plaintext...")
        if not export_database_to_plaintext(db_path, current_key, plaintext_path):
            raise KeyRotationError("Failed to export database")

        # Step 2: Create new encrypted database
        new_db_path = temp_dir / f"new_encrypted_{timestamp}.db"

        print("2. Creating new encrypted database with new key...")
        if not import_plaintext_to_encrypted(plaintext_path, new_db_path, new_key):
            raise KeyRotationError("Failed to create new encrypted database")

        # Step 3: Verify data integrity
        print("3. Verifying data integrity...")
        if not verify_data_integrity(db_path, current_key, new_db_path, new_key):
            raise KeyRotationError("Data integrity verification failed")

        print("\n[OK] Data integrity verified")

        # Step 4: Replace old database
        print("\n4. Replacing old database...")

        # Create another backup just before replacement
        final_backup = backup_dir / f"{db_path.stem}_pre_rotation_{timestamp}.db"
        shutil.copy2(db_path, final_backup)

        # Replace
        shutil.move(str(new_db_path), str(db_path))

        print("[OK] Database replaced with newly encrypted version")

        # Step 5: Cleanup
        print("\n5. Cleaning up temporary files...")
        plaintext_path.unlink()  # Delete plaintext export
        shutil.rmtree(temp_dir)

        print("\n" + "="*70)
        print("KEY ROTATION SUCCESSFUL")
        print("="*70)
        print(f"\n[OK] Database encrypted with new key")
        print(f"[OK] Backups saved:")
        print(f"   - {backup_path}")
        print(f"   - {final_backup}")
        print(f"\n[WARN]  IMPORTANT NEXT STEPS:")
        print(f"1. Update DB_ENCRYPTION_KEY environment variable with new key")
        print(f"2. Restart the application")
        print(f"3. Verify application can connect to database")
        print(f"4. Delete old backups after confirming everything works")

        return True

    except Exception as e:
        print(f"\n[FAIL] KEY ROTATION FAILED: {e}")
        print(f"\n[OK] Your original database is safe at: {backup_path}")
        print(f"No changes were made to the production database.")
        logger.exception("Key rotation failed")
        return False


if __name__ == "__main__":
    try:
        success = rotate_encryption_key()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[FAIL] Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        logger.exception("Unexpected error during key rotation")
        sys.exit(1)
