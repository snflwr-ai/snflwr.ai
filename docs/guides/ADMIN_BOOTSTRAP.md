---
---

# Admin Account Bootstrap Guide
## Creating the First Admin Account

**Last Updated:** 2025-12-21
**Required for:** Initial deployment

---

## Overview

The admin bootstrap script creates the first admin account for snflwr.ai. This is required during initial deployment before anyone can access the admin panel.

**Features:**
- ✅ Interactive or command-line mode
- ✅ Argon2 password hashing (enterprise-grade security)
- ✅ Email encryption (Fernet + SHA256 for COPPA compliance)
- ✅ Password strength validation
- ✅ Duplicate email detection
- ✅ Login verification
- ✅ Comprehensive error handling

---

## Quick Start

### Interactive Mode (Recommended)

```bash
# Run the bootstrap script
python scripts/bootstrap_admin.py
```

**The script will prompt you for:**
1. Admin display name (default: "System Administrator")
2. Admin email address (with validation)
3. Admin password (with strength requirements)
4. Password confirmation
5. Final confirmation before creation

### Example Interactive Session

```
======================================================================
snflwr.ai - Admin Account Bootstrap
======================================================================

This script will create the first admin account.
The admin can then create other users through the admin panel.

Admin Name:
Enter admin display name [System Administrator]: John Doe

Admin Email:
Enter admin email address: admin@school.edu

Admin Password:
Requirements: 8+ chars, uppercase, lowercase, number, special char
Enter admin password: ********
Confirm admin password: ********

======================================================================
Confirm Admin Account Creation
======================================================================
Name:  John Doe
Email: admin@school.edu
Role:  admin

Create this admin account? (yes/no): yes

======================================================================
Creating admin account...
✓ Admin account created: admin_a1b2c3d4
✓ Admin login verified

======================================================================
Admin Account Created Successfully
======================================================================
✓ Admin account is ready!

User ID: admin_a1b2c3d4
Email:   admin@school.edu
Name:    John Doe

You can now login to the admin panel with these credentials.
Admin panel: http://localhost:8000/admin
```

---

## Command-Line Mode

For automated deployments or scripts:

```bash
# Non-interactive mode (requires all parameters)
python scripts/bootstrap_admin.py \
    --email admin@school.edu \
    --password "SecurePass123!" \
    --name "System Administrator" \
    --non-interactive
```

**⚠️ Security Warning:**
- Passwords on command line may be visible in shell history
- Use interactive mode when possible
- Clear history after use: `history -c`

---

## Password Requirements

The script enforces strong password requirements:

✅ **Minimum 8 characters**
✅ **At least one uppercase letter** (A-Z)
✅ **At least one lowercase letter** (a-z)
✅ **At least one number** (0-9)
✅ **At least one special character** (!@#$%^&*(),.?":{}|<>)

**Good Password Examples:**
- `Admin2024!Pass`
- `SecureK12#2024`
- `Snflwr@Admin1`

**Bad Password Examples:**
- `password` ❌ (no uppercase, number, special char)
- `PASSWORD123` ❌ (no lowercase, special char)
- `Pass123` ❌ (too short, no special char)
- `Admin123` ❌ (no special char)

---

## What Happens During Bootstrap?

### 1. Email Processing
```
admin@school.edu
    ↓
SHA-256 Hash (for lookup)
    ↓
Fernet Encryption (for storage)
    ↓
Database
```

**Why encrypt emails?**
- COPPA compliance (protect parent/admin PII)
- Data breach mitigation
- Privacy best practice

### 2. Password Processing
```
SecurePass123!
    ↓
Argon2id Hashing
    ↓
Hash includes: salt + memory cost + time cost + parallelism
    ↓
Database
```

**Why Argon2?**
- Winner of Password Hashing Competition
- Resistant to GPU/ASIC attacks
- Configurable resource costs
- Industry best practice

### 3. Account Creation
```sql
INSERT INTO users (
    user_id,           -- admin_a1b2c3d4e5f6g7h8
    email_hash,        -- SHA-256 hash for lookup
    encrypted_email,   -- Fernet encrypted storage
    password_hash,     -- Argon2 hash
    salt,              -- Empty (Argon2 includes salt in hash)
    role,              -- 'admin'
    name,              -- Display name
    created_at,        -- ISO timestamp
    is_active,         -- 1 (active)
    email_notifications_enabled  -- 1 (enabled)
) VALUES (...)
```

### 4. Verification
- Attempt login with credentials
- Verify role is 'admin'
- Logout test session
- Confirm all operations successful

---

## Troubleshooting

### "Email already exists"

**Problem:** Another account with this email already exists

**Solution:**
```bash
# Check existing accounts
python << 'EOF'
from storage.database import db_manager
result = db_manager.execute_query("SELECT user_id, role FROM users WHERE role = 'admin'")
for user in result:
    print(f"Admin: {user['user_id']} (role: {user['role']})")
EOF

# Use different email or delete existing account
```

### "Password validation failed"

**Problem:** Password doesn't meet strength requirements

**Solution:** Ensure password has:
- At least 8 characters
- One uppercase, lowercase, number, special char

**Test password strength:**
```python
import re

def check_password(pwd):
    checks = {
        'Length (8+)': len(pwd) >= 8,
        'Uppercase': bool(re.search(r'[A-Z]', pwd)),
        'Lowercase': bool(re.search(r'[a-z]', pwd)),
        'Number': bool(re.search(r'[0-9]', pwd)),
        'Special': bool(re.search(r'[!@#$%^&*(),.?":{}|<>]', pwd))
    }

    for check, passed in checks.items():
        print(f"{'✓' if passed else '✗'} {check}")

check_password("YourPassword123!")
```

### "Database not initialized"

**Problem:** Database tables don't exist

**Solution:**
```bash
# Initialize database first
python database/init_db.py

# Then run bootstrap
python scripts/bootstrap_admin.py
```

### "Login verification failed"

**Problem:** Account created but can't login

**Possible Causes:**
1. Password not saved correctly
2. Email encryption issue
3. Session management issue

**Debug:**
```bash
# Check if account exists
python << 'EOF'
from storage.database import db_manager
result = db_manager.execute_query("SELECT * FROM users WHERE role = 'admin'")
print(f"Found {len(result)} admin(s)")
for admin in result:
    print(f"  - {admin['user_id']}")
EOF

# Try manual login via API
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@school.edu","password":"YourPassword"}'
```

---

## Multiple Admins

### Creating Additional Admins

**After first admin is created, you can:**

1. **Use the bootstrap script again** (will warn about existing admins)
2. **Use the admin panel** (recommended)
3. **Use the API** (requires admin auth)

**Admin panel method:**
```
1. Login as existing admin
2. Navigate to Admin Panel → Users
3. Click "Create User"
4. Set role to "admin"
5. Save
```

**Bootstrap script method:**
```bash
python scripts/bootstrap_admin.py
# Script will warn: "1 admin account(s) already exist"
# You can proceed to create another
```

### Checking Existing Admins

```bash
# Count admins
python << 'EOF'
from storage.database import db_manager
result = db_manager.execute_query("SELECT COUNT(*) as count FROM users WHERE role = 'admin'")
print(f"Total admins: {result[0]['count']}")
EOF

# List admins
python << 'EOF'
from storage.database import db_manager
from core.email_crypto import get_email_crypto

email_crypto = get_email_crypto()
admins = db_manager.execute_query("SELECT * FROM users WHERE role = 'admin'")

for admin in admins:
    email = email_crypto.decrypt_email(admin['encrypted_email'])
    print(f"Admin: {admin['name']} ({email}) - Created: {admin['created_at']}")
EOF
```

---

## Security Best Practices

### ✅ DO

- Run bootstrap script on the server (not remotely)
- Use strong, unique passwords
- Store admin credentials in password manager
- Use interactive mode when possible
- Verify login after creation
- Enable 2FA for admin accounts (if available)
- Rotate admin passwords periodically

### ❌ DON'T

- Share admin credentials
- Use weak passwords
- Hardcode credentials in scripts
- Store passwords in plain text
- Use same password across environments
- Leave default admin accounts active

### Password Management

**Generate secure passwords:**
```bash
# Option 1: OpenSSL
openssl rand -base64 24

# Option 2: Python
python -c 'import secrets; print(secrets.token_urlsafe(24))'

# Option 3: pwgen (if installed)
pwgen 24 1
```

### Backup Encryption Key

**⚠️ CRITICAL:** Backup the encryption key or you'll lose access to encrypted emails

```bash
# View current encryption key
grep ENCRYPTION_KEY .env.production

# Backup (encrypt this file!)
cp .env.production /secure/backup/location/

# Store in password manager or encrypted vault
```

---

## Testing the Bootstrap Script

### Test in Development

```bash
# 1. Backup database
cp ~/.local/share/snflwr_ai/snflwr.db ~/snflwr_backup.db

# 2. Run bootstrap
python scripts/bootstrap_admin.py

# 3. Test login
python << 'EOF'
from core.authentication import auth_manager
success, session, error = auth_manager.login('admin@test.com', 'YourPassword123!')
print(f"Login: {'✓ Success' if success else '✗ Failed - ' + error}")
if session:
    print(f"Role: {session.role}")
    auth_manager.logout(session.session_id)
EOF

# 4. Restore if needed
mv ~/snflwr_backup.db ~/.local/share/snflwr_ai/snflwr.db
```

### Automated Testing

```bash
# Create test admin (non-interactive)
python scripts/bootstrap_admin.py \
    --email "test_admin@example.com" \
    --password "TestAdmin123!" \
    --name "Test Admin" \
    --non-interactive

# Verify creation
python << 'EOF'
from storage.database import db_manager
result = db_manager.execute_query(
    "SELECT user_id FROM users WHERE role = 'admin' AND name = 'Test Admin'"
)
print("✓ Test admin created" if result else "✗ Test admin not found")
EOF

# Clean up
python << 'EOF'
from storage.database import db_manager
from core.email_crypto import get_email_crypto

email_crypto = get_email_crypto()
email_hash, _ = email_crypto.prepare_email_for_storage("test_admin@example.com")

db_manager.execute_write("DELETE FROM users WHERE email_hash = ?", (email_hash,))
print("✓ Test admin deleted")
EOF
```

---

## Integration with Deployment

### Docker Deployment

```dockerfile
# In your Dockerfile
COPY scripts/bootstrap_admin.py /app/scripts/

# In docker-compose.yml or startup script
services:
  api:
    command: >
      sh -c "
        python database/init_db.py &&
        python scripts/bootstrap_admin.py --non-interactive \
          --email ${ADMIN_EMAIL} \
          --password ${ADMIN_PASSWORD} \
          --name 'System Administrator' &&
        python api/server.py
      "
```

### Kubernetes Deployment

```yaml
# ConfigMap for bootstrap script
apiVersion: v1
kind: ConfigMap
metadata:
  name: snflwr-bootstrap
data:
  bootstrap.sh: |
    #!/bin/bash
    python database/init_db.py
    python scripts/bootstrap_admin.py --non-interactive \
      --email "$ADMIN_EMAIL" \
      --password "$ADMIN_PASSWORD" \
      --name "System Administrator"

# Init container
spec:
  initContainers:
  - name: bootstrap
    image: snflwr-ai:latest
    command: ["/bin/bash", "/scripts/bootstrap.sh"]
    envFrom:
    - secretRef:
        name: admin-credentials
    volumeMounts:
    - name: bootstrap-script
      mountPath: /scripts
```

---

## FAQ

**Q: Can I run bootstrap multiple times?**
A: Yes, the script will warn if admins exist. You can create multiple admin accounts.

**Q: What if I forget the admin password?**
A: You can reset it manually in the database or create a new admin account.

**Q: Can I delete admin accounts?**
A: Yes, but ensure at least one admin account always exists.

**Q: Is the email really encrypted?**
A: Yes, using Fernet (AES-128-CBC) with HMAC authentication. Check `core/email_crypto.py`.

**Q: Can I change the email after creation?**
A: Not directly. Create a new admin account with new email, then delete old one.

**Q: What happens to the test session in verification?**
A: It's immediately logged out after verification. No active sessions remain.

**Q: Can students access the bootstrap script?**
A: No, the script only runs on the server with direct database access.

---

## Next Steps

After creating the admin account:

1. **Login to admin panel**
   - URL: http://localhost:8000/admin (or your domain)
   - Use credentials from bootstrap

2. **Create parent accounts**
   - Admin Panel → Users → Create User
   - Set role to "parent"
   - Parents will create their own child profiles

3. **Configure system settings**
   - Review safety filter thresholds
   - Configure email notification templates
   - Set up monitoring and alerts

4. **Test the system**
   - Create test parent account
   - Create test child profile
   - Send test chat messages
   - Verify safety alerts

5. **Document credentials**
   - Store admin credentials securely
   - Share with authorized personnel only
   - Set up password rotation schedule

Admin account is ready!
