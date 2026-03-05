#!/usr/bin/env python3
"""
Database Backup Script for snflwr.ai
Supports both SQLite and PostgreSQL databases with automatic rotation
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import shutil
import subprocess
import gzip
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseBackup:
    """Handle database backups with rotation"""

    def __init__(self):
        # Backup configuration from environment or defaults
        self.backup_enabled = os.getenv('BACKUP_ENABLED', 'true').lower() == 'true'
        self.backup_path = Path(os.getenv('BACKUP_PATH', system_config.APP_DATA_DIR / 'backups'))
        self.backup_retention_days = int(os.getenv('BACKUP_RETENTION_DAYS', '30'))
        self.compress_backups = os.getenv('COMPRESS_BACKUPS', 'true').lower() == 'true'

        # Create backup directory
        self.backup_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Backup configuration:")
        logger.info(f"  Enabled: {self.backup_enabled}")
        logger.info(f"  Path: {self.backup_path}")
        logger.info(f"  Retention: {self.backup_retention_days} days")
        logger.info(f"  Compression: {self.compress_backups}")

    def backup_sqlite(self) -> tuple[bool, str]:
        """Backup SQLite database"""
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_path / f"snflwr_sqlite_{timestamp}.db"

            logger.info(f"Creating SQLite backup: {backup_file}")

            # Use sqlite3 command-line tool for online backup if available
            db_path = system_config.DB_PATH

            if not db_path.exists():
                logger.error(f"Database file not found: {db_path}")
                return False, "Database file not found"

            # Copy the database file
            shutil.copy2(db_path, backup_file)

            # Compress if enabled
            if self.compress_backups:
                compressed_file = Path(str(backup_file) + '.gz')
                with open(backup_file, 'rb') as f_in:
                    with gzip.open(compressed_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                # Remove uncompressed backup
                backup_file.unlink()
                backup_file = compressed_file
                logger.info(f"Compressed backup created: {compressed_file}")

            # Get backup size
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            logger.info(f"[OK] SQLite backup completed: {backup_file} ({size_mb:.2f} MB)")

            return True, str(backup_file)

        except Exception as e:
            logger.exception(f"SQLite backup failed: {e}")
            return False, str(e)

    @staticmethod
    def _validate_postgres_param(param: str, param_name: str) -> str:
        """
        Validate PostgreSQL connection parameters to prevent command injection

        Args:
            param: Parameter value to validate
            param_name: Name of parameter (for error messages)

        Returns:
            The validated parameter

        Raises:
            ValueError: If parameter contains invalid characters
        """
        import re

        # Only allow alphanumeric, dots, hyphens, underscores
        if not re.match(r'^[a-zA-Z0-9._-]+$', param):
            raise ValueError(
                f"Invalid {param_name}: contains disallowed characters. "
                f"Only alphanumeric, dots, hyphens, and underscores are allowed."
            )
        return param

    def backup_postgresql(self) -> tuple[bool, str]:
        """Backup PostgreSQL database using pg_dump"""
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_path / f"snflwr_postgres_{timestamp}.sql"

            logger.info(f"Creating PostgreSQL backup: {backup_file}")

            # Validate all PostgreSQL parameters to prevent command injection
            host = self._validate_postgres_param(system_config.POSTGRES_HOST, 'POSTGRES_HOST')
            user = self._validate_postgres_param(system_config.POSTGRES_USER, 'POSTGRES_USER')
            database = self._validate_postgres_param(system_config.POSTGRES_DB, 'POSTGRES_DB')

            # Build pg_dump command with validated parameters
            cmd = [
                'pg_dump',
                '-h', host,
                '-p', str(system_config.POSTGRES_PORT),  # Port is int, already safe
                '-U', user,
                '-d', database,
                '-F', 'c',  # Custom format (compressed)
                '-f', str(backup_file)
            ]

            # Set password environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = system_config.POSTGRES_PASSWORD

            # Run pg_dump
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.error(f"pg_dump failed: {result.stderr}")
                return False, result.stderr

            # Additional gzip compression if enabled
            if self.compress_backups:
                compressed_file = Path(str(backup_file) + '.gz')
                with open(backup_file, 'rb') as f_in:
                    with gzip.open(compressed_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                backup_file.unlink()
                backup_file = compressed_file
                logger.info(f"Additional compression applied: {compressed_file}")

            # Get backup size
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            logger.info(f"[OK] PostgreSQL backup completed: {backup_file} ({size_mb:.2f} MB)")

            return True, str(backup_file)

        except subprocess.TimeoutExpired:
            logger.error("PostgreSQL backup timed out after 5 minutes")
            return False, "Backup timeout"
        except FileNotFoundError:
            logger.error("pg_dump command not found. Install PostgreSQL client tools.")
            return False, "pg_dump not found"
        except Exception as e:
            logger.exception(f"PostgreSQL backup failed: {e}")
            return False, str(e)

    def cleanup_old_backups(self):
        """Remove backups older than retention period"""
        logger.info(f"Cleaning up backups older than {self.backup_retention_days} days")

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.backup_retention_days)
        removed_count = 0
        removed_size = 0

        for backup_file in self.backup_path.glob('snflwr_*'):
            if backup_file.is_file():
                file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)

                if file_time < cutoff_date:
                    size = backup_file.stat().st_size
                    logger.info(f"Removing old backup: {backup_file.name}")
                    backup_file.unlink()
                    removed_count += 1
                    removed_size += size

        if removed_count > 0:
            size_mb = removed_size / (1024 * 1024)
            logger.info(f"[OK] Removed {removed_count} old backups ({size_mb:.2f} MB freed)")
        else:
            logger.info("No old backups to remove")

    def list_backups(self):
        """List all available backups"""
        logger.info("Available backups:")

        backups = sorted(self.backup_path.glob('snflwr_*'), reverse=True)

        if not backups:
            logger.info("  No backups found")
            return

        for backup_file in backups:
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
            logger.info(f"  {backup_file.name} ({size_mb:.2f} MB) - {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

    def create_backup_metadata(self, backup_file: str, success: bool):
        """Create metadata file for backup"""
        metadata = {
            'backup_file': backup_file,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'database_type': system_config.DATABASE_TYPE,
            'success': success,
            'retention_days': self.backup_retention_days,
            'compressed': self.compress_backups,
        }

        metadata_file = Path(backup_file).with_suffix('.json')
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Metadata saved: {metadata_file}")

    def run_backup(self) -> bool:
        """Execute backup based on database type"""
        if not self.backup_enabled:
            logger.warning("Backups are disabled")
            return False

        logger.info("=" * 60)
        logger.info("Starting Database Backup")
        logger.info("=" * 60)

        # Determine database type
        db_type = system_config.DATABASE_TYPE.lower()

        if db_type == 'sqlite':
            success, result = self.backup_sqlite()
        elif db_type == 'postgresql':
            success, result = self.backup_postgresql()
        else:
            logger.error(f"Unsupported database type: {db_type}")
            return False

        # Create metadata
        if success:
            self.create_backup_metadata(result, success)

        # Cleanup old backups
        self.cleanup_old_backups()

        # List available backups
        self.list_backups()

        logger.info("=" * 60)
        if success:
            logger.info("[OK] Backup completed successfully")
        else:
            logger.error(f"[FAIL] Backup failed: {result}")
        logger.info("=" * 60)

        return success


def restore_sqlite(backup_file: Path) -> bool:
    """Restore SQLite database from backup"""
    logger.info(f"Restoring SQLite database from: {backup_file}")

    try:
        db_path = system_config.DB_PATH

        # Backup current database
        if db_path.exists():
            backup_current = db_path.with_suffix('.db.pre-restore')
            shutil.copy2(db_path, backup_current)
            logger.info(f"Current database backed up to: {backup_current}")

        # Decompress if needed
        if backup_file.suffix == '.gz':
            temp_file = backup_file.with_suffix('')
            with gzip.open(backup_file, 'rb') as f_in:
                with open(temp_file, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            restore_source = temp_file
        else:
            restore_source = backup_file

        # Restore database
        shutil.copy2(restore_source, db_path)

        # Cleanup temp file
        if backup_file.suffix == '.gz':
            temp_file.unlink()

        logger.info("[OK] SQLite database restored successfully")
        return True

    except Exception as e:
        logger.exception(f"Restore failed: {e}")
        return False


def restore_postgresql(backup_file: Path) -> bool:
    """Restore PostgreSQL database from backup"""
    logger.info(f"Restoring PostgreSQL database from: {backup_file}")

    try:
        # Decompress if needed
        if backup_file.suffix == '.gz':
            temp_file = backup_file.with_suffix('')
            with gzip.open(backup_file, 'rb') as f_in:
                with open(temp_file, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            restore_source = temp_file
        else:
            restore_source = backup_file

        # Validate all PostgreSQL parameters to prevent command injection
        # Reuse validation method from DatabaseBackup class
        import re
        def validate_param(param: str, param_name: str) -> str:
            if not re.match(r'^[a-zA-Z0-9._-]+$', param):
                raise ValueError(
                    f"Invalid {param_name}: contains disallowed characters. "
                    f"Only alphanumeric, dots, hyphens, and underscores are allowed."
                )
            return param

        host = validate_param(system_config.POSTGRES_HOST, 'POSTGRES_HOST')
        user = validate_param(system_config.POSTGRES_USER, 'POSTGRES_USER')
        database = validate_param(system_config.POSTGRES_DB, 'POSTGRES_DB')

        # Build pg_restore command with validated parameters
        cmd = [
            'pg_restore',
            '-h', host,
            '-p', str(system_config.POSTGRES_PORT),  # Port is int, already safe
            '-U', user,
            '-d', database,
            '--clean',  # Drop existing objects
            '--if-exists',  # Don't error on non-existent objects
            str(restore_source)
        ]

        # Set password environment variable
        env = os.environ.copy()
        env['PGPASSWORD'] = system_config.POSTGRES_PASSWORD

        # Run pg_restore
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        # Cleanup temp file
        if backup_file.suffix == '.gz':
            temp_file.unlink()

        if result.returncode != 0:
            logger.error(f"pg_restore failed: {result.stderr}")
            return False

        logger.info("[OK] PostgreSQL database restored successfully")
        return True

    except Exception as e:
        logger.exception(f"Restore failed: {e}")
        return False


def main():
    """Main execution"""
    import argparse

    parser = argparse.ArgumentParser(description='snflwr.ai Database Backup/Restore')
    parser.add_argument('action', choices=['backup', 'restore', 'list'],
                        help='Action to perform')
    parser.add_argument('--file', type=str,
                        help='Backup file path (for restore)')

    args = parser.parse_args()

    backup_manager = DatabaseBackup()

    if args.action == 'backup':
        success = backup_manager.run_backup()
        sys.exit(0 if success else 1)

    elif args.action == 'list':
        backup_manager.list_backups()
        sys.exit(0)

    elif args.action == 'restore':
        if not args.file:
            logger.error("--file required for restore")
            sys.exit(1)

        backup_file = Path(args.file)
        if not backup_file.exists():
            logger.error(f"Backup file not found: {backup_file}")
            sys.exit(1)

        db_type = system_config.DATABASE_TYPE.lower()

        if db_type == 'sqlite':
            success = restore_sqlite(backup_file)
        elif db_type == 'postgresql':
            success = restore_postgresql(backup_file)
        else:
            logger.error(f"Unsupported database type: {db_type}")
            sys.exit(1)

        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
