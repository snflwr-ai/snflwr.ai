# Production Credentials Checklist
**snflwr.ai - Security Configuration Before Deployment**

🚨 **CRITICAL:** All items marked with ❗ MUST be changed before production deployment.

---

## 1. Core Application Secrets

### ❗ JWT Authentication (CRITICAL)
**File:** `.env.production`
```bash
# Generate with:
python -c 'import secrets; print(secrets.token_hex(32))'

# Replace this:
JWT_SECRET_KEY=change-this-to-secure-random-value-64-characters-minimum

# With a 64+ character random hex string
JWT_SECRET_KEY=<your-generated-secret-key>
```
**Impact if not changed:** Anyone can forge authentication tokens and impersonate users.

---

### ❗ Encryption Key (CRITICAL)
**File:** `.env.production`
```bash
# Generate with:
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# Replace this:
ENCRYPTION_KEY=change-this-to-fernet-key-44-characters

# With a Fernet key (44 characters):
ENCRYPTION_KEY=<your-generated-fernet-key>
```
**Impact if not changed:** Encrypted data (emails, PII) can be decrypted by attackers.

---

## 2. Database Credentials

### ❗ PostgreSQL Password (if using PostgreSQL)
**File:** `.env.production`
```bash
# Only needed if DATABASE_TYPE=postgresql

# Generate strong password:
python -c 'import secrets; print(secrets.token_urlsafe(32))'

# Replace with:
POSTGRES_PASSWORD=<your-secure-database-password>
POSTGRES_USER=snflwr
POSTGRES_DB=snflwr_db
```

**Also update in:**
- `enterprise/k8s/secrets.yaml` (if using Kubernetes)
- Docker Compose files (if using Docker)

**Impact if not changed:** Database compromise, full data breach.

---

## 3. Email (SMTP) Credentials

### ❗ SMTP Credentials (CRITICAL for alerts)
**File:** `.env.production`
```bash
SMTP_ENABLED=true
SMTP_HOST=smtp.sendgrid.net          # Or your email provider
SMTP_PORT=587
SMTP_USERNAME=apikey                  # Your SMTP username
SMTP_PASSWORD=SG.your_sendgrid_api_key_here  # ❗ REPLACE THIS
SMTP_FROM_EMAIL=noreply@yourdomain.com       # ❗ UPDATE DOMAIN
SMTP_FROM_NAME=snflwr.ai Safety Monitor
SMTP_USE_TLS=true
```

**How to get credentials:**
- **SendGrid:** Sign up at sendgrid.com → Create API Key
- **AWS SES:** Use IAM credentials
- **Gmail:** Use App Password (not recommended for production)
- **Mailgun:** Get SMTP credentials from dashboard

**Impact if not changed:** Safety alerts won't be sent to parents.

---

## 4. Redis Password (if using Redis)

### ❗ Redis Password (MEDIUM priority)
**File:** `.env.production`
```bash
# Generate strong password:
python -c 'import secrets; print(secrets.token_urlsafe(24))'

REDIS_PASSWORD=<your-redis-password>
```

**Default in docker-compose files:**
- `docker/compose/docker-compose.yml` (Redis service): `snflwr_redis_pass` ❗ CHANGE THIS
- `docker/compose/docker-compose.yml` (Celery services): `snflwr_redis` ❗ CHANGE THIS

**Impact if not changed:** Cache poisoning, session hijacking.

---

## 5. Monitoring & Admin Tools

### ❗ Flower Admin Credentials (Celery Monitoring)
**File:** `.env.production`
```bash
FLOWER_ENABLED=true  # If using Celery
FLOWER_USER=admin                           # ❗ CHANGE THIS
FLOWER_PASSWORD=change-this-to-secure-password  # ❗ CHANGE THIS
```

**Impact if not changed:** Unauthorized access to background task monitoring.

---

### Sentry DSN (Error Tracking) - OPTIONAL
**File:** `.env.production`
```bash
SENTRY_ENABLED=true  # If using Sentry
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0  # ❗ REPLACE
SENTRY_ENVIRONMENT=production
```

**How to get:** Sign up at sentry.io → Create project → Get DSN

**Impact if not changed:** Error tracking won't work (not critical).

---

## 6. Domain & URLs

### ❗ Update Domain Names
**File:** `.env.production`
```bash
# Update these to your actual domain:
CORS_ORIGINS=https://yourdomain.com              # ❗ UPDATE
SMTP_FROM_EMAIL=noreply@yourdomain.com          # ❗ UPDATE
FRONTEND_URL=https://yourdomain.com             # ❗ UPDATE
BASE_URL=https://api.yourdomain.com             # ❗ UPDATE
```

**Impact if not changed:** CORS errors, broken email links.

---

## 7. Kubernetes Secrets (if using K8s)

### ❗ Replace ALL placeholders in enterprise/k8s/secrets.yaml
**File:** `enterprise/k8s/secrets.yaml`

**DO NOT use the YAML file directly in production!** Instead:

```bash
# Option 1: Create from .env.production
kubectl create secret generic snflwr-secrets \
  --from-env-file=.env.production \
  --namespace=snflwr-ai

# Option 2: Use a secret management tool
# - HashiCorp Vault
# - AWS Secrets Manager
# - Azure Key Vault
# - Google Secret Manager
```

**Secrets to replace:**
- ❗ `JWT_SECRET_KEY`
- ❗ `ENCRYPTION_KEY`
- ❗ `POSTGRES_PASSWORD`
- ❗ `REDIS_PASSWORD`
- ❗ `SMTP_PASSWORD`
- `SENTRY_DSN` (if using)

---

## 8. Docker Compose Default Passwords

### ❗ Update Redis passwords in Docker Compose files

**File:** `docker/compose/docker-compose.yml`
```yaml
# Redis service - Change default password:
--requirepass ${REDIS_PASSWORD:-snflwr_redis_pass}  # ❗ UPDATE DEFAULT
```

**Better approach:** Always set `REDIS_PASSWORD` environment variable; don't rely on defaults.

---

## 9. SSL/TLS Certificates (CRITICAL for production)

### ❗ Enable SSL and configure certificates
**File:** `.env.production`
```bash
SSL_ENABLED=true  # ❗ ENABLE FOR PRODUCTION
```

**Options:**
1. **Let's Encrypt (Free):** Use Certbot or cert-manager (K8s)
2. **Load Balancer SSL:** CloudFlare, AWS ALB, etc.
3. **Manual certificates:** Purchase from CA

**Impact if not changed:** Data transmitted in plain text, man-in-the-middle attacks.

---

## 10. Security Settings

### ❗ Disable plugins (already disabled by default)
**File:** `.env.production`
```bash
ENABLE_PLUGINS=false  # ✅ Keep this FALSE for K-12 deployments
```

### ❗ Set environment to production
**File:** `.env.production`
```bash
ENVIRONMENT=production  # ✅ CRITICAL - enables security validations
```

---

## Quick Setup Script

Create `.env.production` with all secure values:

```bash
#!/bin/bash
# generate_production_env.sh

cat > .env.production << 'EOF'
# Environment
ENVIRONMENT=production

# Database
DATABASE_TYPE=postgresql  # or sqlite for small deployments
POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

# Security Keys
JWT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

# Redis
REDIS_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')

# Flower
FLOWER_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')

# SMTP (update with your credentials)
SMTP_ENABLED=true
SMTP_HOST=smtp.sendgrid.net
SMTP_USERNAME=apikey
SMTP_PASSWORD=YOUR_SENDGRID_API_KEY_HERE  # ❗ UPDATE MANUALLY
SMTP_FROM_EMAIL=noreply@yourdomain.com    # ❗ UPDATE MANUALLY

# Domains
CORS_ORIGINS=https://yourdomain.com       # ❗ UPDATE MANUALLY
FRONTEND_URL=https://yourdomain.com       # ❗ UPDATE MANUALLY

# SSL
SSL_ENABLED=true

# Plugins (keep disabled)
ENABLE_PLUGINS=false
EOF

echo "✅ Generated .env.production"
echo "⚠️  IMPORTANT: Update the marked values manually!"
```

---

## Verification Checklist

Before deploying to production, verify:

- [ ] ✅ All `CHANGE-THIS` placeholders replaced
- [ ] ✅ All default passwords changed
- [ ] ✅ JWT_SECRET_KEY is 64+ characters and random
- [ ] ✅ ENCRYPTION_KEY is a valid Fernet key
- [ ] ✅ SMTP credentials tested and working
- [ ] ✅ Domain names updated
- [ ] ✅ SSL/TLS enabled
- [ ] ✅ ENVIRONMENT=production
- [ ] ✅ ENABLE_PLUGINS=false
- [ ] ✅ PostgreSQL password is strong (if using)
- [ ] ✅ Redis password is set
- [ ] ✅ Flower password changed
- [ ] ✅ K8s secrets created (if using Kubernetes)
- [ ] ✅ `.env.production` file has correct permissions (chmod 600)
- [ ] ✅ `.env.production` NOT committed to git
- [ ] ✅ Test email sending works
- [ ] ✅ Test database connection works

---

## Security Best Practices

1. **Never commit `.env.production` to git**
   ```bash
   # Already in .gitignore, but double-check:
   echo ".env.production" >> .gitignore
   ```

2. **Use secret management tools in production**
   - AWS Secrets Manager
   - HashiCorp Vault
   - Azure Key Vault
   - Google Secret Manager

3. **Rotate secrets regularly**
   - JWT_SECRET_KEY: Every 90 days
   - Database passwords: Every 90 days
   - API keys: When leaving team members have access

4. **Restrict access**
   ```bash
   chmod 600 .env.production  # Only owner can read/write
   ```

5. **Backup secrets securely**
   - Use encrypted backup storage
   - Store in password manager (1Password, LastPass, etc.)

---

## Emergency: Secrets Compromised

If any production secret is compromised:

1. **Immediately rotate the secret**
   - Generate new value
   - Update `.env.production`
   - Restart services

2. **For JWT_SECRET_KEY compromise:**
   - All users will be logged out
   - Force password reset for all accounts
   - Review audit logs

3. **For database password compromise:**
   - Change password immediately
   - Review database audit logs
   - Check for unauthorized access

4. **For ENCRYPTION_KEY compromise:**
   - Critical: All encrypted data is at risk
   - Rotate key and re-encrypt all data
   - Notify affected users (COPPA requirement)

---

**Last Updated:** 2025-12-25
**Next Review:** Before production deployment
