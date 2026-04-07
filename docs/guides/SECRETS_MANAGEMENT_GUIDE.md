---
---

# Secrets Management & Security Guide
## snflwr.ai Production Deployment

**Last Updated:** December 27, 2025
**Security Priority:** 🔴 CRITICAL
**Audience:** DevOps, System Administrators, Developers

---

## Table of Contents

1. [Current Security Status](#current-security-status)
2. [Secrets Inventory](#secrets-inventory)
3. [What's Already Protected](#whats-already-protected)
4. [Deployment Strategies](#deployment-strategies)
5. [Best Practices](#best-practices)
6. [Pre-Deployment Security Checklist](#pre-deployment-security-checklist)
7. [Emergency Procedures](#emergency-procedures)

---

## Current Security Status

### ✅ **SECURE - Already Protected**

Your codebase is **currently secure** with comprehensive `.gitignore` protection:

**Protected Locations:**
- ✅ `.env` files (all variants: `.env`, `.env.local`, `.env.production`, etc.)
- ✅ `data/` directory (databases, encryption keys)
- ✅ `logs/` directory (may contain sensitive data)
- ✅ `dist/` directory (build artifacts with embedded secrets)
- ✅ Database files (`*.db`, `*.sqlite`, `*.sqlite3`)
- ✅ Python cache and build artifacts

**Verified Safe:**
- ✅ No actual secrets in version control (git ls-files checked)
- ✅ `enterprise/k8s/secrets.yaml` contains only placeholders ("CHANGE-THIS" markers)
- ✅ Documentation shows examples only (no real keys)
- ✅ Encryption keys stored outside project directory (platform-specific locations)

### ⚠️ **ATTENTION REQUIRED**

**dist/SnflwrAI/.env**
- Contains auto-generated secrets for USB deployment
- **Status:** Protected by `.gitignore` (dist/ excluded)
- **Action:** Regenerate these on first deployment (don't reuse)

---

## Secrets Inventory

### 1. **Encryption Keys** 🔴 CRITICAL

**Purpose:** Database encryption (AES-256), PII encryption (emails)

**Secrets:**
```bash
DB_ENCRYPTION_KEY=<32+ character random string>
ENCRYPTION_KEY=<Fernet key, 44 characters base64>
```

**Generation:**
```bash
# Database encryption key
export DB_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Fernet encryption key (for email/PII encryption)
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

**Storage:**
- **Local Dev:** `.env` file (gitignored)
- **Production:** Environment variables or secrets manager
- **Kubernetes:** Kubernetes Secrets (not in enterprise/k8s/secrets.yaml file in git)

**⚠️ CRITICAL:** If these keys are lost, encrypted data CANNOT be recovered!

**Backup Strategy:**
```bash
# Backup encryption keys to secure location (NOT git!)
# Option 1: Password manager (1Password, Bitwarden)
# Option 2: Encrypted USB drive (offline storage)
# Option 3: Cloud secrets manager (AWS Secrets Manager, HashiCorp Vault)
```

---

### 2. **Authentication Secrets** 🔴 CRITICAL

**Purpose:** Session management, JWT tokens, password hashing

**Secrets:**
```bash
JWT_SECRET_KEY=<64 character hex string>
SESSION_SECRET=<32+ character random string>
```

**Generation:**
```bash
# JWT secret key (256-bit)
export JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Session secret
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

**Rotation Policy:**
- Rotate every 90 days (production)
- Rotate immediately if compromised
- Use different keys for dev/staging/production

---

### 3. **Database Credentials** 🔴 CRITICAL

**Purpose:** PostgreSQL/SQLite database access

**Secrets:**
```bash
# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/snflwr
POSTGRES_USER=snflwr_app
POSTGRES_PASSWORD=<secure random password>
POSTGRES_DB=snflwr_production

# SQLite (less critical, but protect database file)
DATABASE_PATH=./data/snflwr.db
```

**Generation:**
```bash
# PostgreSQL password (32 characters, alphanumeric + symbols)
export POSTGRES_PASSWORD=$(python -c "import secrets; import string; chars = string.ascii_letters + string.digits + '!@#$%^&*'; print(''.join(secrets.choice(chars) for _ in range(32)))")
```

**Storage:**
- **Never commit** DATABASE_URL with embedded password
- Use environment variables
- In Kubernetes, use Secrets
- Consider connection pooling with separate credentials

---

### 4. **SMTP Credentials** 🟡 MEDIUM

**Purpose:** Sending parent safety alert emails

**Secrets:**
```bash
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=<SendGrid API key>
SMTP_FROM_EMAIL=noreply@snflwr.ai
```

**Best Practices:**
- Use API keys instead of passwords (SendGrid, Mailgun, etc.)
- Restrict API key permissions (send-only, no read access)
- Use separate keys for dev/staging/production
- Monitor usage for anomalies

**Free Tier Options:**
- SendGrid: 100 emails/day free
- Mailgun: 5,000 emails/month free (first 3 months)
- Amazon SES: 62,000 emails/month free (if on AWS)

---

### 5. **Redis Credentials** 🟡 MEDIUM

**Purpose:** Session storage, caching (optional)

**Secrets:**
```bash
REDIS_URL=redis://default:password@localhost:6379/0
REDIS_PASSWORD=<secure random password>
```

**Generation:**
```bash
export REDIS_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
```

**Note:** Redis is optional for snflwr.ai (can use in-memory sessions)

---

### 6. **Admin Bootstrap Credentials** 🔴 CRITICAL

**Purpose:** First admin account creation

**Secrets:**
```bash
ADMIN_EMAIL=admin@your-domain.com
ADMIN_PASSWORD=<strong password>
```

**⚠️ SECURITY:**
- Change immediately after first login
- Use long, unique password (20+ characters)
- Enable 2FA after initial setup
- Delete admin bootstrap credentials from environment after use

**Generation:**
```bash
# Strong admin password (24 characters)
export ADMIN_PASSWORD=$(python -c "import secrets; import string; chars = string.ascii_letters + string.digits + string.punctuation; print(''.join(secrets.choice(chars) for _ in range(24)))")
```

---

### 7. **Third-Party API Keys** 🟡 MEDIUM (if used)

**Currently NOT used in snflwr.ai (local-first architecture)**

**If you add integrations:**
```bash
# Example only - not currently needed
OPENAI_API_KEY=sk-...          # If using OpenAI (NOT recommended - privacy risk)
ANTHROPIC_API_KEY=sk-ant-...   # If using Anthropic (NOT recommended - privacy risk)
SENTRY_DSN=https://...         # Error monitoring
```

**⚠️ COPPA/FERPA WARNING:**
Do NOT send student data to third-party AI services (OpenAI, Anthropic, etc.) without explicit consent and DPA!

---

## What's Already Protected

### `.gitignore` Coverage Analysis

**✅ PROTECTED - Will NOT be committed to git:**

```gitignore
# Environment variables (ALL secrets should go here)
.env
.env.local
.env.*.local
.env.production
.env.staging
.env.development

# Data directory (databases, encryption keys)
data/
*.db
*.sqlite
*.sqlite3

# Logs (may contain sensitive data)
logs/
*.log

# Build artifacts (may contain embedded secrets)
dist/
build/

# IDE settings (may contain local paths)
.vscode/
.idea/
```

### Encryption Key Storage

**Platform-Specific Locations (OUTSIDE project directory):**

```bash
# Windows
%APPDATA%\SnflwrAI\.encryption_key

# macOS
~/Library/Application Support/SnflwrAI/.encryption_key

# Linux
~/.local/share/snflwr_ai/.encryption_key
```

**Security:**
- File permissions: `0o600` (owner read/write only)
- NOT in project directory (won't be committed)
- Automatically created on first run

---

## Deployment Strategies

### Option 1: Environment Variables (Recommended for Simple Deployments)

**Pros:**
- Simple and widely supported
- Works with Docker, systemd, PM2, etc.
- No additional dependencies

**Cons:**
- Visible in process list (`ps aux`)
- Logged in some environments
- No automatic rotation

**Setup:**

```bash
# Create production .env file (NEVER commit this!)
cat > .env.production << 'EOF'
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# KEEP THIS FILE SECURE - DO NOT COMMIT TO GIT

# Database Encryption (CRITICAL)
DB_ENCRYPTION_ENABLED=true
DB_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Authentication
JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Database
DATABASE_TYPE=postgresql
DATABASE_URL=postgresql://snflwr:CHANGE_PASSWORD@localhost:5432/snflwr_prod

# SMTP (for parent alerts)
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=CHANGE_THIS_SENDGRID_KEY

# Application
ENVIRONMENT=production
LOG_LEVEL=INFO
EOF

# Load into environment
export $(cat .env.production | xargs)

# Or use with systemd
# See: /etc/systemd/system/snflwr.service
# EnvironmentFile=/opt/snflwr/.env.production
```

---

### Option 2: Docker Secrets (Recommended for Docker/Docker Compose)

**Pros:**
- Secrets never visible in container config
- Encrypted at rest and in transit
- Automatic mounting as files

**Cons:**
- Requires Docker Swarm or Kubernetes
- More complex setup

**Setup:**

```bash
# Create secrets
echo "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" | docker secret create db_encryption_key -
echo "$(python -c 'import secrets; print(secrets.token_hex(32))')" | docker secret create jwt_secret -

# docker-compose.yml
version: '3.8'
services:
  snflwr:
    image: snflwr.ai
    secrets:
      - db_encryption_key
      - jwt_secret
    environment:
      DB_ENCRYPTION_KEY_FILE: /run/secrets/db_encryption_key
      JWT_SECRET_FILE: /run/secrets/jwt_secret

secrets:
  db_encryption_key:
    external: true
  jwt_secret:
    external: true
```

---

### Option 3: Kubernetes Secrets (Recommended for Kubernetes)

**Pros:**
- Native Kubernetes integration
- Automatic base64 encoding
- Can use external secrets (Vault, AWS Secrets Manager)

**Cons:**
- Requires Kubernetes
- Base64 is NOT encryption (just encoding)

**Setup:**

```bash
# DO NOT edit enterprise/k8s/secrets.yaml directly - it's a template!
# Instead, create secrets from .env file:

# 1. Create .env.production with actual secrets
cat > .env.production << 'EOF'
DB_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
POSTGRES_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
SMTP_PASSWORD=<your-sendgrid-api-key>
EOF

# 2. Create Kubernetes secret from file
kubectl create secret generic snflwr-secrets \
  --from-env-file=.env.production \
  --namespace=snflwr-ai

# 3. Verify (will show base64 encoded values)
kubectl get secret snflwr-secrets -o yaml -n snflwr-ai

# 4. DELETE .env.production after secret created!
shred -u .env.production  # Linux
srm .env.production       # macOS
```

**Using External Secrets (Recommended for Production):**

```yaml
# external-secrets.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: snflwr-secrets
  namespace: snflwr-ai
spec:
  secretStoreRef:
    name: aws-secrets-manager  # or vault, azure-keyvault, etc.
    kind: SecretStore
  target:
    name: snflwr-secrets
  data:
    - secretKey: DB_ENCRYPTION_KEY
      remoteRef:
        key: snflwr/production/db-encryption-key
    - secretKey: JWT_SECRET_KEY
      remoteRef:
        key: snflwr/production/jwt-secret
```

---

### Option 4: HashiCorp Vault (Recommended for Enterprise)

**Pros:**
- Industry standard for secrets management
- Automatic rotation
- Audit logging
- Dynamic secrets (temporary database credentials)

**Cons:**
- Complex setup
- Additional infrastructure

**Setup:**

```bash
# 1. Install Vault
# https://www.vaultproject.io/downloads

# 2. Start Vault server (production use proper setup!)
vault server -dev

# 3. Store secrets
vault kv put secret/snflwr/production \
  db_encryption_key="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  jwt_secret="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# 4. Retrieve in application
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='<token>'

vault kv get -field=db_encryption_key secret/snflwr/production
```

---

### Option 5: Cloud Provider Secrets Managers

**AWS Secrets Manager:**
```bash
# Store secret
aws secretsmanager create-secret \
  --name snflwr/production/db-encryption-key \
  --secret-string "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Retrieve in application
aws secretsmanager get-secret-value \
  --secret-id snflwr/production/db-encryption-key \
  --query SecretString --output text
```

**Google Cloud Secret Manager:**
```bash
# Store secret
echo -n "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" | \
  gcloud secrets create db-encryption-key --data-file=-

# Retrieve
gcloud secrets versions access latest --secret=db-encryption-key
```

**Azure Key Vault:**
```bash
# Store secret
az keyvault secret set \
  --vault-name snflwr-vault \
  --name db-encryption-key \
  --value "$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Retrieve
az keyvault secret show \
  --vault-name snflwr-vault \
  --name db-encryption-key \
  --query value -o tsv
```

---

## Best Practices

### 1. **Secrets Generation**

✅ **DO:**
- Use cryptographically secure random generation (`secrets` module)
- Generate separate secrets for dev/staging/production
- Use minimum 32 characters for encryption keys
- Use minimum 24 characters for passwords
- Include uppercase, lowercase, digits, and symbols for passwords

❌ **DON'T:**
- Use predictable values ("password123", "admin", etc.)
- Reuse secrets across environments
- Use short secrets (< 16 characters)
- Use your birthday, company name, or dictionary words
- Copy secrets from examples or documentation

**Script for generating all secrets:**

```bash
#!/bin/bash
# generate-secrets.sh

echo "# snflwr.ai Production Secrets"
echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# KEEP THIS SECURE - DO NOT COMMIT TO GIT"
echo ""

echo "# Database Encryption (AES-256)"
echo "DB_ENCRYPTION_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo ""

echo "# Authentication (256-bit)"
echo "JWT_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')"
echo "SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo ""

echo "# Database Password"
echo "POSTGRES_PASSWORD=$(python -c 'import secrets; import string; chars = string.ascii_letters + string.digits + string.punctuation; print("".join(secrets.choice(chars) for _ in range(32)))')"
echo ""

echo "# Redis Password"
echo "REDIS_PASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
echo ""

echo "# Admin Bootstrap"
echo "ADMIN_PASSWORD=$(python -c 'import secrets; import string; chars = string.ascii_letters + string.digits + string.punctuation; print("".join(secrets.choice(chars) for _ in range(24)))')"
```

**Usage:**
```bash
chmod +x generate-secrets.sh
./generate-secrets.sh > .env.production
chmod 600 .env.production  # Protect file permissions
```

---

### 2. **Secrets Storage**

✅ **DO:**
- Store in environment variables (production)
- Use secrets managers (Vault, AWS Secrets Manager, etc.)
- Use `.env` files for local development (gitignored!)
- Encrypt secrets at rest (if storing in files)
- Use separate secrets for each environment
- Restrict file permissions (`chmod 600`)

❌ **DON'T:**
- Commit `.env` files to git
- Store in application code (hardcoded)
- Store in configuration files committed to git
- Email or Slack secrets (use secure channels)
- Store in cloud storage without encryption
- Use the same secrets for dev and production

---

### 3. **Secrets Rotation**

**Rotation Schedule:**

| Secret Type | Rotation Frequency | Priority |
|-------------|-------------------|----------|
| JWT Secret | Every 90 days | High |
| Database Password | Every 90 days | High |
| Encryption Keys | Never (data loss risk) | N/A |
| SMTP API Keys | Every 180 days | Medium |
| Admin Passwords | Every 60 days | High |

**⚠️ CRITICAL - Encryption Keys:**
- **NEVER rotate DB_ENCRYPTION_KEY** without re-encrypting all data
- Backup keys before ANY changes
- Test decryption before deleting old keys

**Rotation Procedure:**

```bash
# 1. Generate new secret
NEW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

# 2. Deploy with BOTH old and new secrets (grace period)
export JWT_SECRET_KEY=$OLD_JWT_SECRET
export JWT_SECRET_KEY_NEW=$NEW_JWT_SECRET

# 3. Update application to accept both (for active sessions)
# Wait 24 hours for sessions to expire

# 4. Switch to new secret only
export JWT_SECRET_KEY=$NEW_JWT_SECRET

# 5. Remove old secret from environment
```

---

### 4. **Access Control**

✅ **DO:**
- Use principle of least privilege
- Separate production and development secrets
- Audit who has access to secrets
- Use temporary credentials when possible
- Log secret access (but not values!)

❌ **DON'T:**
- Share production secrets with developers
- Grant read access to everyone
- Use long-lived API keys (prefer temporary tokens)
- Share secrets via email or chat

**Access Matrix:**

| Role | Dev Secrets | Staging Secrets | Prod Secrets |
|------|-------------|-----------------|--------------|
| Developers | ✅ Read/Write | ✅ Read | ❌ No access |
| DevOps | ✅ Read | ✅ Read/Write | ✅ Read/Write |
| Admins | ✅ Read/Write | ✅ Read/Write | ✅ Read/Write |
| CI/CD | ✅ Read | ✅ Read | ✅ Read (limited) |

---

### 5. **Monitoring & Auditing**

✅ **DO:**
- Monitor for secrets in logs
- Audit secret access
- Alert on unusual access patterns
- Scan git history for accidentally committed secrets
- Use tools like git-secrets, truffleHog, gitleaks

**Setup git-secrets (prevents committing secrets):**

```bash
# Install git-secrets
# macOS
brew install git-secrets

# Ubuntu/Debian
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
sudo make install

# Configure for repository
cd /home/user/snflwr.ai
git secrets --install
git secrets --register-aws  # Detects AWS keys

# Add custom patterns
git secrets --add 'DB_ENCRYPTION_KEY.*=.*'
git secrets --add 'JWT_SECRET.*=.*'
git secrets --add 'password.*=.*[^CHANGE-THIS]'
git secrets --add '[0-9a-f]{64}'  # Hex secrets

# Scan repository
git secrets --scan-history
```

**Scan for secrets in codebase:**

```bash
# Install truffleHog
pip install truffleHog

# Scan git history
trufflehog --regex --entropy=True /home/user/snflwr.ai

# Or use gitleaks
docker run -v $(pwd):/path zricethezav/gitleaks:latest detect --source="/path"
```

---

## Pre-Deployment Security Checklist

### Before First Deployment

- [ ] **1. Generate all production secrets**
  ```bash
  ./generate-secrets.sh > .env.production
  chmod 600 .env.production
  ```

- [ ] **2. Backup encryption keys securely**
  - [ ] Save DB_ENCRYPTION_KEY to password manager (1Password, Bitwarden)
  - [ ] Save backup to encrypted USB drive (offline, physically secure)
  - [ ] Test decryption with backup key

- [ ] **3. Verify .gitignore protection**
  ```bash
  git status  # Ensure no .env files shown
  git ls-files | grep -E "\.env$|\.key$|secret"  # Should return nothing
  ```

- [ ] **4. Scan for accidentally committed secrets**
  ```bash
  git secrets --scan-history
  trufflehog --regex --entropy=True .
  ```

- [ ] **5. Set up secrets management (choose one)**
  - [ ] Environment variables (.env.production file)
  - [ ] Docker Secrets
  - [ ] Kubernetes Secrets
  - [ ] HashiCorp Vault
  - [ ] Cloud provider secrets manager (AWS/GCP/Azure)

- [ ] **6. Restrict file permissions**
  ```bash
  chmod 600 .env.production
  chmod 600 /home/user/.local/share/snflwr_ai/.encryption_key
  ```

- [ ] **7. Configure SMTP for safety alerts**
  - [ ] Create SendGrid account (or alternative)
  - [ ] Generate API key with send-only permissions
  - [ ] Add SMTP_PASSWORD to secrets
  - [ ] Test email delivery

- [ ] **8. Create first admin account**
  ```bash
  python scripts/bootstrap_admin.py \
    --email admin@your-domain.com \
    --name "Admin" \
    --password "$ADMIN_PASSWORD"
  ```

- [ ] **9. Change default passwords**
  - [ ] Admin password (if using default)
  - [ ] Database passwords (PostgreSQL)
  - [ ] Redis password (if used)

- [ ] **10. Document recovery procedures**
  - [ ] Write down backup locations
  - [ ] Document who has access to what
  - [ ] Create incident response runbook

---

### After Deployment

- [ ] **1. Rotate temporary secrets**
  - [ ] Admin bootstrap password (change on first login)
  - [ ] Any deployment-generated secrets

- [ ] **2. Enable monitoring**
  - [ ] Sentry (error tracking)
  - [ ] Log aggregation (ELK, CloudWatch, etc.)
  - [ ] Uptime monitoring

- [ ] **3. Set up alerting**
  - [ ] Failed login attempts
  - [ ] Unusual access patterns
  - [ ] Error rates

- [ ] **4. Test backup restoration**
  - [ ] Database backup
  - [ ] Encryption key recovery
  - [ ] Secrets restoration

- [ ] **5. Schedule rotation**
  - [ ] Add calendar reminders for secret rotation
  - [ ] Document rotation procedures
  - [ ] Test rotation in staging first

---

## Emergency Procedures

### Secret Compromised (Suspected or Confirmed)

**IMMEDIATE ACTIONS:**

1. **Assess Impact**
   ```bash
   # What was compromised?
   # - DB_ENCRYPTION_KEY? (CRITICAL - all encrypted data at risk)
   # - JWT_SECRET_KEY? (All active sessions invalidated when rotated)
   # - Database password? (Unauthorized database access)
   # - SMTP API key? (Spam emails sent from your account)
   ```

2. **Rotate Immediately**
   ```bash
   # Generate new secret
   NEW_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

   # Deploy new secret (depending on method)
   # Environment variables: Update .env.production and restart
   # Kubernetes: kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -
   # Vault: vault kv put secret/snflwr/production jwt_secret=$NEW_SECRET

   # Restart application
   sudo systemctl restart snflwr  # systemd
   docker-compose restart            # Docker Compose
   kubectl rollout restart deployment/snflwr  # Kubernetes
   ```

3. **Revoke Old Secret**
   ```bash
   # For API keys (SMTP, etc.)
   # - Log into provider dashboard
   # - Revoke old key
   # - Verify new key works

   # For database passwords
   ALTER USER snflwr_app WITH PASSWORD 'new_secure_password';
   ```

4. **Audit Access**
   ```bash
   # Check logs for unauthorized access
   grep "authentication failure" /var/log/snflwr/*.log

   # Database access logs
   SELECT * FROM audit_log WHERE timestamp > '2025-12-27' ORDER BY timestamp DESC;

   # Failed login attempts
   SELECT * FROM failed_login_attempts WHERE timestamp > NOW() - INTERVAL '7 days';
   ```

5. **Notify Stakeholders**
   - If user data potentially compromised: Notify affected users (COPPA/FERPA requirement)
   - If institutional deployment: Notify school administrators
   - If breach: Follow incident response runbook (docs/INCIDENT_RESPONSE_RUNBOOK.md)

---

### Encryption Key Lost

**⚠️ CRITICAL - NO RECOVERY POSSIBLE**

If `DB_ENCRYPTION_KEY` is lost, encrypted data **CANNOT be recovered**:
- Encrypted emails
- Safety incident content
- Any other PII encrypted with Fernet

**Prevention:**
- Always maintain 3 backups:
  1. Password manager (1Password, Bitwarden, etc.)
  2. Encrypted USB drive (offline, physically secure)
  3. Cloud secrets manager (AWS Secrets Manager, Vault)

**Recovery Procedure (if backup exists):**
```bash
# 1. Restore key from backup
export DB_ENCRYPTION_KEY="<recovered_key_from_backup>"

# 2. Verify decryption works
python -c "
from storage.encryption import encryption_manager
from storage.database import db_manager

# Test decrypt email
result = db_manager.execute_query('SELECT encrypted_email FROM users LIMIT 1')
decrypted = encryption_manager.decrypt_string(result[0]['encrypted_email'])
print(f'Decryption test: {decrypted[:10]}...')  # Should show email prefix
"

# 3. If successful, update production environment
# 4. If failed, data is lost - must start fresh
```

---

## Summary

### ✅ You're Already Secure

Your codebase has **excellent security hygiene**:
- Comprehensive `.gitignore` (protects all secrets)
- No hardcoded secrets (verified)
- Encryption keys stored outside project
- Template files use placeholders only
- Documentation uses examples, not real secrets

### 🔒 Before Deployment, You Must:

1. **Generate production secrets** (use `generate-secrets.sh`)
2. **Back up encryption keys** (3 locations: password manager, USB, cloud)
3. **Choose secrets management strategy** (env vars, Docker secrets, Vault, etc.)
4. **Set up SMTP for alerts** (SendGrid, Mailgun, etc.)
5. **Create admin account** (with strong password)
6. **Test secret recovery** (verify backups work)

### 📋 Quick Start

```bash
# 1. Generate all secrets
./generate-secrets.sh > .env.production
chmod 600 .env.production

# 2. Back up critical keys
# COPY THESE TO PASSWORD MANAGER:
grep DB_ENCRYPTION_KEY .env.production
grep JWT_SECRET_KEY .env.production

# 3. Load into environment
export $(cat .env.production | xargs)

# 4. Initialize database
python -m storage.database init

# 5. Create admin
python scripts/bootstrap_admin.py --email admin@example.com

# 6. Start application
python main.py
```

---

**Your secrets are safe. Deploy with confidence!** 🚀

**Questions? Review this guide or consult the security team.**

---

**Document Version:** 1.0
**Last Updated:** December 27, 2025
**Next Review:** 90 days after deployment
