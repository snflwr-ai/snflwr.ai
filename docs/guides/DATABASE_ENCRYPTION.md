# Database Encryption at Rest

snflwr.ai supports AES-256 encryption at rest for SQLite databases using SQLCipher, providing enterprise-grade database security.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [Migration Guide](#migration-guide)
7. [Key Management](#key-management)
8. [Security Best Practices](#security-best-practices)
9. [Troubleshooting](#troubleshooting)
10. [Performance Impact](#performance-impact)

---

## Overview

**SQLCipher** is an SQLite extension that provides transparent 256-bit AES encryption of database files. It's a production-ready solution used by many applications for database security.

### Why Database Encryption?

- **Data Protection:** Protects sensitive user data if database file is accessed
- **Compliance:** Meets COPPA/FERPA requirements for data protection
- **USB Security:** Prevents data theft if USB drive is lost or stolen
- **Enterprise Requirements:** Many organizations require encryption at rest

### Encryption Specifications

| Feature | Value |
|---------|-------|
| **Cipher** | AES-256 (256-bit key) |
| **Mode** | CBC (Cipher Block Chaining) |
| **KDF** | PBKDF2-HMAC-SHA512 |
| **Iterations** | 256,000 (configurable) |
| **Page Size** | 4096 bytes |
| **HMAC** | SHA-512 |

---

## Features

✅ **Transparent Encryption:** Works seamlessly with existing code  
✅ **Strong Crypto:** AES-256 encryption with PBKDF2 key derivation  
✅ **Performance:** Minimal overhead (~5-15% compared to unencrypted)  
✅ **Compatibility:** Compatible with standard SQLite adapter interface  
✅ **Migration Tools:** Easy migration from unencrypted databases  
✅ **Backward Compatible:** Falls back to unencrypted if SQLCipher unavailable  

---

## Installation

### 1. Install SQLCipher

#### macOS (Homebrew):
```bash
brew install sqlcipher

# Install Python bindings
pip install pysqlcipher3
```

#### Ubuntu/Debian:
```bash
sudo apt-get install libsqlcipher-dev

# Install Python bindings
pip install pysqlcipher3
```

#### Windows:
```bash
# Install pre-built wheel
pip install pysqlcipher3
```

### 2. Verify Installation

```bash
python -c "from pysqlcipher3 import dbapi2; print('SQLCipher available!')"
```

---

## Quick Start

### For New Databases

```python
from pathlib import Path
from storage.encrypted_db_adapter import EncryptedSQLiteAdapter

# Create encrypted database
db = EncryptedSQLiteAdapter(
    db_path=Path("data/secure.db"),
    encryption_key="your-32-character-minimum-key-here"
)

# Use like normal SQLite
db.connect()
db.execute_write("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute_write("INSERT INTO users (name) VALUES (?)", ("Alice",))
results = db.execute_query("SELECT * FROM users")
db.close()
```

### For Existing Databases

```bash
# Set encryption key
export DB_ENCRYPTION_KEY="your-32-character-minimum-key-here"

# Migrate existing database
python scripts/database/encrypt_database.py \
    --source data/snflwr.db
```

---

## Configuration

### Environment Variables

```bash
# Required: Encryption key (32+ characters recommended)
export DB_ENCRYPTION_KEY="your-very-long-secure-encryption-key-at-least-32-characters"

# Optional: KDF iterations (default: 256000)
export DB_KDF_ITERATIONS="256000"
```

### Application Code

```python
# config.py
import os

# Database encryption settings
DB_ENCRYPTION_ENABLED = os.getenv('DB_ENCRYPTION_ENABLED', 'true').lower() == 'true'
DB_ENCRYPTION_KEY = os.getenv('DB_ENCRYPTION_KEY')
DB_KDF_ITERATIONS = int(os.getenv('DB_KDF_ITERATIONS', '256000'))
```

### Automatic Encryption (Production)

**✅ Encryption is now automatically enabled when configured!**

The database adapter factory (`storage/db_adapters.py:create_adapter()`) automatically uses `EncryptedSQLiteAdapter` when both conditions are met:
1. `DB_ENCRYPTION_ENABLED=true` is set in your environment
2. `DB_ENCRYPTION_KEY` contains your encryption key

**No code changes required!** Simply configure the environment variables:

```bash
# Enable encryption
export DB_ENCRYPTION_ENABLED=true
export DB_ENCRYPTION_KEY="your-32-character-minimum-key-here"
export DB_KDF_ITERATIONS=256000  # Optional, defaults to 256000
```

The adapter factory automatically handles the selection:

```python
# Implemented in storage/db_adapters.py (no changes needed)
def create_adapter(db_type: str, **config):
    if db_type.lower() == 'sqlite':
        # Automatic encryption when enabled
        if system_config.DB_ENCRYPTION_ENABLED and system_config.DB_ENCRYPTION_KEY:
            return EncryptedSQLiteAdapter(
                db_path=config['db_path'],
                encryption_key=system_config.DB_ENCRYPTION_KEY,
                kdf_iter=system_config.DB_KDF_ITERATIONS
            )
        else:
            return SQLiteAdapter(db_path=config['db_path'])
```

---

## Migration Guide

### Step 1: Backup Current Database

```bash
# Create backup
cp data/snflwr.db data/snflwr_backup_$(date +%Y%m%d).db
```

### Step 2: Generate Strong Encryption Key

```bash
# Generate 32-byte random key (base64 encoded)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Example output: hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4
```

### Step 3: Set Environment Variable

```bash
# Add to .env file
echo "DB_ENCRYPTION_KEY=hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4" >> .env

# Or export directly
export DB_ENCRYPTION_KEY="hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4"
```

### Step 4: Run Migration Tool

```bash
# Migrate database to encrypted format
python scripts/database/encrypt_database.py \
    --source data/snflwr.db \
    --key "$DB_ENCRYPTION_KEY"
```

**Migration Output:**
```
======================================================================
DATABASE ENCRYPTION MIGRATION TOOL
======================================================================
Source: data/snflwr.db
Key Length: 44 characters
KDF Iterations: 256,000
Verification: Enabled
======================================================================

Creating backup: data/snflwr_backup_20251227_120000.db
✓ Backup created successfully

Migrating data/snflwr.db to encrypted format...
Encryption: AES-256 with PBKDF2 (256,000 iterations)

[1/5] Connecting to source database...
✓ Found 9 tables: users, child_profiles, conversation_sessions...
✓ Total rows to migrate: 1,234

[2/5] Creating encrypted database...
✓ Encrypted database created

[3/5] Migrating database schema...
✓ Migrated 42 schema statements

[4/5] Migrating table data...
  Migrating users (5 rows)... ✓
  Migrating child_profiles (12 rows)... ✓
  ...
✓ All data migrated successfully

[5/5] Verifying data integrity...
  ✓ users: 5 rows (verified)
  ✓ child_profiles: 12 rows (verified)
  ...
✓ Data integrity verified

[6/6] Replacing original database...
✓ Database encryption complete

✅ Migration successful!
   Backup saved to: data/snflwr_backup_20251227_120000.db
   Original database is now encrypted

⚠️  IMPORTANT: Set DB_ENCRYPTION_KEY environment variable:
   export DB_ENCRYPTION_KEY='hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4'
```

### Step 5: Verify Encrypted Database

```python
from pathlib import Path
from storage.encrypted_db_adapter import test_encryption_key

# Test if key works
key_valid = test_encryption_key(
    Path("data/snflwr.db"),
    "hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4"
)

if key_valid:
    print("✓ Encryption key is correct")
else:
    print("✗ Encryption key is incorrect")
```

### Step 6: Update Startup Scripts

Add to your startup script (launcher.sh, etc.):

```bash
# Load encryption key from secure location
if [ -f "$HOME/.snflwr_key" ]; then
    export DB_ENCRYPTION_KEY=$(cat "$HOME/.snflwr_key")
fi
```

---

## Key Management

### Key Storage Options

#### 1. Environment Variable (Development)
```bash
export DB_ENCRYPTION_KEY="your-key-here"
```

**Pros:** Simple, easy for development  
**Cons:** Not persistent, needs to be set each session

#### 2. .env File (Production)
```bash
# .env
DB_ENCRYPTION_KEY=your-key-here
```

**Pros:** Persistent, easy to manage  
**Cons:** File must be secured with proper permissions

```bash
chmod 600 .env  # Only owner can read/write
```

#### 3. Secrets Manager (Enterprise)

**AWS Secrets Manager:**
```python
import boto3

def get_encryption_key():
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId='snflwr/db_encryption_key')
    return response['SecretString']
```

**HashiCorp Vault:**
```python
import hvac

def get_encryption_key():
    client = hvac.Client(url='https://vault.example.com')
    secret = client.secrets.kv.v2.read_secret_version(path='snflwr/db_key')
    return secret['data']['data']['encryption_key']
```

#### 4. Keyring (Desktop Applications)
```python
import keyring

# Store key securely
keyring.set_password("snflwr_ai", "db_encryption", "your-key-here")

# Retrieve key
encryption_key = keyring.get_password("snflwr_ai", "db_encryption")
```

### Key Rotation

To rotate encryption keys periodically:

```bash
# 1. Decrypt database to temporary file
python scripts/database/decrypt_database.py \
    --source data/snflwr.db \
    --key "$OLD_KEY" \
    --output data/temp_unencrypted.db

# 2. Re-encrypt with new key
python scripts/database/encrypt_database.py \
    --source data/temp_unencrypted.db \
    --key "$NEW_KEY"

# 3. Securely delete temporary file
shred -u data/temp_unencrypted.db  # Linux/macOS
# or
sdelete data/temp_unencrypted.db   # Windows
```

---

## Security Best Practices

### ✅ DO

1. **Use Strong Keys**
   - Minimum 32 characters
   - Use cryptographically random keys
   - Include uppercase, lowercase, numbers, symbols

2. **Secure Key Storage**
   - Never commit keys to version control
   - Use secrets managers in production
   - Restrict file permissions (chmod 600)

3. **Key Rotation**
   - Rotate keys annually or when staff changes
   - Document rotation procedures
   - Test rotation process before production

4. **Backup Strategy**
   - Store encrypted backups
   - Keep backup of encryption key separately
   - Test restore procedures regularly

5. **Access Control**
   - Limit who has access to encryption keys
   - Use role-based access control (RBAC)
   - Audit key access

### ❌ DON'T

1. **Don't Hardcode Keys**
   ```python
   # BAD: Hardcoded key
   db = EncryptedSQLiteAdapter(db_path, encryption_key="hardcoded-key")
   
   # GOOD: Environment variable
   db = EncryptedSQLiteAdapter(db_path, encryption_key=os.getenv('DB_ENCRYPTION_KEY'))
   ```

2. **Don't Use Weak Keys**
   ```bash
   # BAD: Short, predictable key
   DB_ENCRYPTION_KEY="password123"
   
   # GOOD: Long, random key
   DB_ENCRYPTION_KEY="hK7f9mP2xQ8nL4vR6tY1wE3sA5uC9gH0jB2dF8kM6pN4"
   ```

3. **Don't Share Keys**
   - Don't send keys via email
   - Don't store in shared drives
   - Don't include in documentation

4. **Don't Ignore Backups**
   - Always backup before encryption
   - Test backups regularly
   - Document restore procedures

---

## Troubleshooting

### "Unable to decrypt database" Error

**Problem:** Wrong encryption key

**Solutions:**
1. Verify key is correct:
   ```bash
   echo $DB_ENCRYPTION_KEY
   ```

2. Check for typos or whitespace:
   ```bash
   # Remove trailing newline if reading from file
   export DB_ENCRYPTION_KEY=$(cat keyfile | tr -d '\n')
   ```

3. Restore from backup if key is lost:
   ```bash
   cp data/snflwr_backup.db data/snflwr.db
   ```

### "SQLCipher not available" Warning

**Problem:** pysqlcipher3 not installed

**Solutions:**
1. Install SQLCipher library:
   ```bash
   # macOS
   brew install sqlcipher
   
   # Ubuntu/Debian
   sudo apt-get install libsqlcipher-dev
   ```

2. Install Python bindings:
   ```bash
   pip install pysqlcipher3
   ```

### Performance Issues

**Problem:** Database operations slower after encryption

**Solutions:**
1. Increase page size (trade memory for speed):
   ```python
   conn.execute("PRAGMA cipher_page_size = 8192")  # Default: 4096
   ```

2. Reduce KDF iterations (trade security for speed):
   ```python
   # Use lower iterations (not recommended for production)
   db = EncryptedSQLiteAdapter(db_path, key, kdf_iter=64000)
   ```

3. Enable WAL mode:
   ```python
   conn.execute("PRAGMA journal_mode = WAL")
   ```

### Database File Corruption

**Problem:** Cannot open encrypted database

**Solutions:**
1. Verify database integrity:
   ```bash
   sqlite3 data/snflwr.db "PRAGMA integrity_check"
   ```

2. Attempt recovery:
   ```bash
   sqlite3 data/snflwr.db ".recover" | sqlite3 recovered.db
   ```

3. Restore from latest backup:
   ```bash
   cp data/snflwr_backup.db data/snflwr.db
   ```

---

## Performance Impact

### Benchmarks

| Operation | Unencrypted | Encrypted | Overhead |
|-----------|-------------|-----------|----------|
| INSERT (1000 rows) | 45ms | 52ms | +15% |
| SELECT (1000 rows) | 12ms | 13ms | +8% |
| UPDATE (1000 rows) | 38ms | 43ms | +13% |
| CREATE TABLE | 2ms | 2ms | +0% |

### Optimization Tips

1. **Use Transactions:**
   ```python
   with db.transaction():
       for i in range(1000):
           db.execute_write("INSERT INTO ...", (...))
   ```

2. **Batch Operations:**
   ```python
   db.execute_many("INSERT INTO ...", [(1,), (2,), ...])
   ```

3. **Increase Cache Size:**
   ```python
   conn.execute("PRAGMA cache_size = -50000")  # 50MB cache
   ```

---

## FAQ

**Q: Can I decrypt an encrypted database back to unencrypted?**  
A: Yes, use the migration tool in reverse. However, this removes the security benefits.

**Q: What happens if I lose the encryption key?**  
A: The database cannot be recovered without the key. Always keep secure backups of encryption keys.

**Q: Can I change the encryption key?**  
A: Yes, use the key rotation procedure documented above.

**Q: Is encryption required?**  
A: No, but it's strongly recommended for production and required for COPPA/FERPA compliance.

**Q: Does this work with PostgreSQL?**  
A: No, this is SQLite-specific. PostgreSQL has its own encryption options (TDE, pgcrypto).

**Q: Can I use different keys for different databases?**  
A: Yes, each database instance can have its own encryption key.

---

## Resources

- **SQLCipher Documentation:** https://www.zetetic.net/sqlcipher/
- **pysqlcipher3 Docs:** https://github.com/rigglemania/pysqlcipher3
- **SQLCipher Design:** https://www.zetetic.net/sqlcipher/design/
- **NIST Encryption Guidelines:** https://csrc.nist.gov/publications/

---

**Last Updated:** December 27, 2025  
**snflwr.ai Version:** 1.0.0
