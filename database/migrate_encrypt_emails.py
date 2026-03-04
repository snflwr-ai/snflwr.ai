"""
Email Encryption Migration Script
Migrates plaintext emails to encrypted storage for COPPA compliance
"""

import sys
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config
from storage.encryption import EncryptionManager
from utils.logger import get_logger

logger = get_logger(__name__)


def hash_email(email: str) -> str:
    """
    Create SHA256 hash of email for lookup

    Args:
        email: Email address to hash

    Returns:
        Hex string of SHA256 hash
    """
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def check_schema_version():
    """Check if migration is needed"""
    conn = sqlite3.connect(str(system_config.DB_PATH))
    cursor = conn.cursor()

    try:
        # Check if users table has old schema (plaintext email)
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        has_plaintext_email = 'email' in columns
        has_encrypted_email = 'encrypted_email' in columns
        has_email_hash = 'email_hash' in columns

        return {
            'needs_migration': has_plaintext_email and not has_encrypted_email,
            'already_encrypted': has_encrypted_email and has_email_hash,
            'columns': columns
        }
    finally:
        conn.close()


def backup_database():
    """Create backup before migration"""
    db_path = Path(system_config.DB_PATH)
    backup_path = db_path.parent / f"{db_path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{db_path.suffix}"

    import shutil
    shutil.copy2(db_path, backup_path)

    logger.info(f"Database backed up to: {backup_path}")
    return backup_path


def migrate_emails():
    """Migrate plaintext emails to encrypted format"""

    print("=" * 70)
    print("EMAIL ENCRYPTION MIGRATION")
    print("=" * 70)

    # Step 1: Check if migration needed
    print("\n1. Checking schema version...")
    status = check_schema_version()

    if status['already_encrypted']:
        print("   ✓ Database already using encrypted emails!")
        print("   No migration needed.")
        return True

    if not status['needs_migration']:
        print("   ⚠️  Unable to determine schema state")
        print(f"   Columns found: {', '.join(status['columns'])}")
        return False

    print("   → Migration needed: plaintext emails detected")

    # Step 2: Backup database
    print("\n2. Creating database backup...")
    backup_path = backup_database()
    print(f"   ✓ Backup created: {backup_path}")

    # Step 3: Read existing emails
    print("\n3. Reading existing user emails...")
    conn = sqlite3.connect(str(system_config.DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT user_id, email, role FROM users")
        users = cursor.fetchall()

        print(f"   → Found {len(users)} users")

        if len(users) == 0:
            print("   ℹ️  No users to migrate")

        # Step 4: Add new columns
        print("\n4. Adding encrypted email columns...")

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN email_hash TEXT")
            print("   ✓ Added email_hash column")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("   → email_hash column already exists")
            else:
                raise

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN encrypted_email TEXT")
            print("   ✓ Added encrypted_email column")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("   → encrypted_email column already exists")
            else:
                raise

        conn.commit()

        # Step 5: Encrypt existing emails
        print("\n5. Encrypting existing emails...")
        encryption = EncryptionManager()

        migrated_count = 0
        for user in users:
            user_id = user['user_id']
            plaintext_email = user['email']
            role = user['role']

            # Generate hash for lookup
            email_hash_value = hash_email(plaintext_email)

            # Encrypt email
            encrypted_email_value = encryption.encrypt_string(plaintext_email)

            # Update user record
            cursor.execute("""
                UPDATE users
                SET email_hash = ?, encrypted_email = ?
                WHERE user_id = ?
            """, (email_hash_value, encrypted_email_value, user_id))

            migrated_count += 1
            print(f"   → Migrated {role}: [email redacted]")

        conn.commit()
        print(f"\n   ✓ Encrypted {migrated_count} email addresses")

        # Step 6: Verify migration
        print("\n6. Verifying encrypted emails...")
        cursor.execute("SELECT user_id, email, encrypted_email FROM users")
        verification = cursor.fetchall()

        all_encrypted = True
        for row in verification:
            if not row['encrypted_email']:
                print(f"   ❌ User {row['user_id']} has no encrypted_email!")
                all_encrypted = False
            else:
                # Verify we can decrypt
                try:
                    decrypted = encryption.decrypt_string(row['encrypted_email'])
                    if decrypted != row['email']:
                        print(f"   ❌ Decryption mismatch for {row['user_id']}")
                        all_encrypted = False
                except Exception as e:
                    print(f"   ❌ Failed to decrypt {row['user_id']}: {e}")
                    all_encrypted = False

        if all_encrypted:
            print("   ✓ All emails encrypted and verified!")
        else:
            print("   ❌ Verification failed - keeping plaintext column")
            return False

        # Step 7: Create unique index on email_hash
        print("\n7. Creating index on email_hash...")
        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_hash_new ON users(email_hash)")
            conn.commit()
            print("   ✓ Index created")
        except sqlite3.IntegrityError as e:
            print(f"   ❌ Index creation failed: {e}")
            print("   This might indicate duplicate emails in the database")
            return False

        # Step 8: Drop old plaintext email column (SQLite limitation workaround)
        print("\n8. Removing plaintext email column...")
        print("   ℹ️  SQLite doesn't support DROP COLUMN directly")
        print("   Recommendation: Recreate table or leave as deprecated")
        print("   For now, we'll set plaintext emails to empty string")

        cursor.execute("UPDATE users SET email = ''")
        conn.commit()
        print("   ✓ Plaintext emails cleared (column remains for compatibility)")

        print("\n" + "=" * 70)
        print("✓ MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print(f"\nMigrated: {migrated_count} users")
        print(f"Backup: {backup_path}")
        print("\nNext steps:")
        print("1. Test authentication with encrypted emails")
        print("2. Test email notifications")
        print("3. If all works, you can recreate table to fully remove email column")

        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        print(f"\n❌ Migration failed: {e}")
        print(f"\nDatabase backup available at: {backup_path}")
        print("You can restore from backup if needed")
        return False

    finally:
        conn.close()


def verify_encryption():
    """Verify encrypted email storage is working"""
    print("\n" + "=" * 70)
    print("ENCRYPTION VERIFICATION TEST")
    print("=" * 70)

    conn = sqlite3.connect(str(system_config.DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    encryption = EncryptionManager()

    try:
        print("\nTesting email encryption/decryption...")

        # Get a sample user
        cursor.execute("SELECT user_id, email_hash, encrypted_email FROM users LIMIT 1")
        user = cursor.fetchone()

        if not user:
            print("   No users in database to test")
            return

        if not user['encrypted_email']:
            print("   ❌ User has no encrypted_email")
            return

        # Decrypt email
        decrypted_email = encryption.decrypt_string(user['encrypted_email'])

        print(f"   User ID: {user['user_id']}")
        print(f"   Email Hash: {user['email_hash'][:16]}...")
        print(f"   Encrypted: {user['encrypted_email'][:50]}...")
        print(f"   Decrypted: [verified - content redacted]")

        # Verify hash matches
        calculated_hash = hash_email(decrypted_email)
        if calculated_hash == user['email_hash']:
            print("\n   ✓ Hash verification passed!")
        else:
            print("\n   ❌ Hash verification failed!")
            return

        print("\n✓ Encryption system working correctly!")

    finally:
        conn.close()


def main():
    """Main migration function"""
    import argparse

    parser = argparse.ArgumentParser(description='Migrate emails to encrypted storage')
    parser.add_argument('--verify-only', action='store_true', help='Only verify, don\'t migrate')
    args = parser.parse_args()

    if args.verify_only:
        verify_encryption()
    else:
        success = migrate_emails()
        if success:
            verify_encryption()
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
