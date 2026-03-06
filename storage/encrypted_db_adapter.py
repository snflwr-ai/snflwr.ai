"""
Encrypted Database Adapter using SQLCipher
Provides AES-256 encryption at rest for SQLite databases
"""

import os
import sqlite3
from pathlib import Path

try:
    from pysqlcipher3 import dbapi2 as sqlcipher

    SQLCIPHER_AVAILABLE = True
except ImportError:
    SQLCIPHER_AVAILABLE = False

from storage.db_adapters import SQLiteAdapter
from utils.logger import get_logger

logger = get_logger(__name__)


class EncryptedSQLiteAdapter(SQLiteAdapter):
    """
    SQLite database adapter with AES-256 encryption using SQLCipher

    Features:
    - Transparent 256-bit AES encryption at rest
    - Compatible with standard SQLite adapter interface
    - Encryption key management via environment variables
    - Automatic key derivation using PBKDF2
    - PRODUCTION MODE: Fails if encryption unavailable (FERPA compliance)

    Security Notes:
    - Encryption key should be 32+ characters
    - Store encryption key in secure location (not in code)
    - Use environment variables or secrets management
    - Rotate keys periodically using provided migration tools
    """

    def __init__(
        self,
        db_path: Path,
        encryption_key: str = None,
        timeout: float = 30.0,
        check_same_thread: bool = True,
        kdf_iter: int = 256000,  # PBKDF2 iterations (SQLCipher 4 default)
        require_encryption: bool = None,  # If True, fail if SQLCipher unavailable
    ):
        """
        Initialize encrypted SQLite adapter

        Args:
            db_path: Path to database file
            encryption_key: Encryption key (32+ characters recommended)
                          If None, reads from DB_ENCRYPTION_KEY env variable
            timeout: Database lock timeout in seconds
            check_same_thread: SQLite thread-safety setting
            kdf_iter: PBKDF2 key derivation iterations (higher = more secure, slower)
            require_encryption: If True, fail startup if SQLCipher unavailable.
                              If None, auto-detect from ENVIRONMENT env var.

        Raises:
            ValueError: If encryption_key is None and DB_ENCRYPTION_KEY not set
            RuntimeError: If require_encryption=True and SQLCipher not available
        """
        super().__init__(db_path, timeout, check_same_thread)

        # Get encryption key from parameter or environment
        self.encryption_key = encryption_key or os.getenv("DB_ENCRYPTION_KEY")

        if not self.encryption_key:
            raise ValueError(
                "Database encryption key not provided. "
                "Set DB_ENCRYPTION_KEY environment variable or pass encryption_key parameter."
            )

        # Validate key strength
        if len(self.encryption_key) < 32:
            logger.warning(
                f"Encryption key is {len(self.encryption_key)} characters. "
                "Recommended minimum is 32 characters for strong security."
            )

        self.kdf_iter = kdf_iter
        self.is_encrypted = SQLCIPHER_AVAILABLE

        # Determine if encryption is required
        if require_encryption is None:
            # Auto-detect: require encryption in production
            environment = os.getenv("ENVIRONMENT", "development").lower()
            require_encryption = environment in ("production", "prod", "staging")

        self.require_encryption = require_encryption

        if not SQLCIPHER_AVAILABLE:
            error_msg = (
                "CRITICAL: SQLCipher not available - database CANNOT be encrypted.\n"
                "This is a FERPA compliance violation for K-12 student data.\n"
                "Install SQLCipher: pip install pysqlcipher3\n"
                "Or on Ubuntu/Debian: apt-get install libsqlcipher-dev && pip install pysqlcipher3"
            )

            if require_encryption:
                logger.critical(error_msg)
                raise RuntimeError(error_msg)
            else:
                logger.warning(
                    "SQLCipher not available. Database will NOT be encrypted. "
                    "This is acceptable ONLY for local development. "
                    "Install pysqlcipher3 for production: pip install pysqlcipher3"
                )

    def connect(self):
        """
        Establish encrypted SQLite connection using SQLCipher

        Returns:
            Database connection object
        """
        if self.connection is None:
            if SQLCIPHER_AVAILABLE:
                # Use SQLCipher for encryption
                self.connection = sqlcipher.connect(
                    str(self.db_path),
                    timeout=self.timeout,
                    check_same_thread=self.check_same_thread,
                    isolation_level="DEFERRED",
                )
                self.connection.row_factory = sqlcipher.Row

                # Set encryption key (must be first pragma)
                # Validate encryption key to prevent SQL injection
                if not self.encryption_key or len(self.encryption_key) < 32:
                    raise ValueError(
                        "Invalid encryption key: must be at least 32 characters"
                    )
                # Check for SQL injection characters beyond quotes
                if any(
                    c in self.encryption_key for c in [";", "--", "/*", "*/", "\x00"]
                ):
                    raise ValueError("Encryption key contains invalid characters")
                # Escape single quotes by doubling them (SQL standard)
                escaped_key = self.encryption_key.replace("'", "''")
                self.connection.execute(f"PRAGMA key = '{escaped_key}'")

                # Configure SQLCipher 4 settings
                # Validate kdf_iter is an integer to prevent SQL injection
                if (
                    not isinstance(self.kdf_iter, int)
                    or self.kdf_iter < 1000
                    or self.kdf_iter > 10000000
                ):
                    raise ValueError(
                        f"Invalid kdf_iter: must be integer between 1000-10000000, got {self.kdf_iter}"
                    )
                self.connection.execute(f"PRAGMA kdf_iter = {self.kdf_iter}")
                self.connection.execute("PRAGMA cipher_page_size = 4096")
                self.connection.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
                self.connection.execute(
                    "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512"
                )

                logger.info(
                    f"Encrypted database connection established: {self.db_path}"
                )
            else:
                # Fall back to standard SQLite (NOT ENCRYPTED)
                logger.warning(f"Using UNENCRYPTED connection to: {self.db_path}")
                self.connection = sqlite3.connect(
                    str(self.db_path),
                    timeout=self.timeout,
                    check_same_thread=self.check_same_thread,
                    isolation_level="DEFERRED",
                )
                self.connection.row_factory = sqlite3.Row

            # Enable foreign keys
            self.connection.execute("PRAGMA foreign_keys = ON")

            # Performance optimizations
            try:
                if os.name == "nt":
                    self.connection.execute("PRAGMA journal_mode = DELETE")
                else:
                    self.connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error as e:
                logger.debug(f"Failed to set journal mode (non-critical): {e}")

            self.connection.execute("PRAGMA synchronous = NORMAL")
            self.connection.execute("PRAGMA cache_size = -20000")  # 20MB cache
            self.connection.execute("PRAGMA temp_store = MEMORY")

            # Verify encryption is working
            if SQLCIPHER_AVAILABLE:
                try:
                    # Try to read from database (will fail if key is wrong)
                    cursor = self.connection.cursor()
                    cursor.execute("SELECT name FROM sqlite_master LIMIT 1")
                    cursor.close()
                    logger.debug("Encryption key verification successful")
                except sqlite3.Error as e:
                    logger.error(
                        f"Failed to access encrypted database. "
                        f"Incorrect encryption key or corrupted database: {e}"
                    )
                    raise ValueError(
                        "Unable to decrypt database. "
                        "Check DB_ENCRYPTION_KEY environment variable."
                    ) from e

        return self.connection

    def is_database_encrypted(self) -> bool:
        """
        Check if database is actually encrypted

        Returns:
            True if using SQLCipher encryption, False otherwise
        """
        return self.is_encrypted

    def get_encryption_info(self) -> dict:
        """
        Get information about database encryption

        Returns:
            Dictionary with encryption details
        """
        return {
            "encrypted": self.is_encrypted,
            "cipher": "AES-256" if self.is_encrypted else "None",
            "kdf_algorithm": "PBKDF2_HMAC_SHA512" if self.is_encrypted else "None",
            "kdf_iterations": self.kdf_iter if self.is_encrypted else 0,
            "page_size": 4096 if self.is_encrypted else 4096,
            "hmac_algorithm": "HMAC_SHA512" if self.is_encrypted else "None",
            "sqlcipher_available": SQLCIPHER_AVAILABLE,
            "database_path": str(self.db_path),
        }


def create_encrypted_database(
    db_path: Path, encryption_key: str = None
) -> EncryptedSQLiteAdapter:
    """
    Create a new encrypted SQLite database

    Args:
        db_path: Path where database will be created
        encryption_key: Encryption key (32+ characters recommended)

    Returns:
        EncryptedSQLiteAdapter instance

    Example:
        >>> from pathlib import Path
        >>> db = create_encrypted_database(
        ...     Path("data/secure.db"),
        ...     encryption_key="my-very-long-secure-key-at-least-32-chars"
        ... )
        >>> db.connect()
        >>> # Database is now encrypted at rest
    """
    adapter = EncryptedSQLiteAdapter(db_path, encryption_key)
    adapter.connect()
    logger.info(f"Created encrypted database: {db_path}")
    return adapter


def test_encryption_key(db_path: Path, encryption_key: str) -> bool:
    """
    Test if an encryption key can decrypt a database

    Args:
        db_path: Path to encrypted database
        encryption_key: Encryption key to test

    Returns:
        True if key is correct, False otherwise

    Example:
        >>> test_encryption_key(Path("data/secure.db"), "my-key")
        True
    """
    try:
        adapter = EncryptedSQLiteAdapter(db_path, encryption_key)
        adapter.connect()
        # Try to read from database
        cursor = adapter.connection.cursor()
        cursor.execute("SELECT name FROM sqlite_master LIMIT 1")
        cursor.close()
        adapter.close()
        return True
    except (sqlite3.Error, ValueError) as e:
        logger.debug(f"Encryption key test failed (non-critical): {e}")
        return False


# Export public interface
__all__ = [
    "EncryptedSQLiteAdapter",
    "create_encrypted_database",
    "test_encryption_key",
    "SQLCIPHER_AVAILABLE",
]
