---
---

# Troubleshooting Guide
## snflwr.ai Common Issues & Solutions

**Last Updated:** 2025-12-25
**Version:** 1.0

---

## Table of Contents

1. [Installation & Setup](#installation--setup)
2. [API Server Issues](#api-server-issues)
3. [Database Problems](#database-problems)
4. [Safety Monitoring Issues](#safety-monitoring-issues)
5. [Performance Problems](#performance-problems)
6. [Authentication & Authorization](#authentication--authorization)
7. [Email & Notifications](#email--notifications)
8. [Docker & Deployment](#docker--deployment)
9. [Frontend/Open WebUI](#frontendopen-webui)
10. [Diagnostic Tools](#diagnostic-tools)

---

## Installation & Setup

### Issue: Python dependencies won't install

**Symptoms:**
```
ERROR: Could not find a version that satisfies the requirement...
```

**Solution:**
```bash
# Verify Python version (requires 3.10+)
python --version  # Should be 3.10 or higher

# Upgrade pip
python -m pip install --upgrade pip

# Install with verbose output
pip install -r requirements.txt -v

# If specific package fails, install separately
pip install psycopg2-binary==2.9.9

# macOS: Install PostgreSQL client libraries
brew install postgresql

# Ubuntu/Debian:
sudo apt-get install libpq-dev python3-dev
```

---

### Issue: Database initialization fails

**Symptoms:**
```
sqlite3.OperationalError: unable to open database file
```

**Solution:**
```bash
# Check directory permissions
ls -la ~/.local/share/snflwr_ai/

# Create directory if missing
mkdir -p ~/.local/share/snflwr_ai/

# Set proper permissions
chmod 755 ~/.local/share/snflwr_ai/

# Initialize database
python database/init_db.py

# Verify tables created
sqlite3 ~/.local/share/snflwr_ai/snflwr.db ".tables"
```

---

### Issue: Configuration validation fails

**Symptoms:**
```
CRITICAL SECURITY ERROR: JWT_SECRET_KEY is using default value
```

**Solution:**
```bash
# Generate secure JWT secret
python -c 'import secrets; print(secrets.token_hex(32))'

# Add to .env.production
echo "JWT_SECRET_KEY=<generated-value>" >> .env.production

# Generate encryption key
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
echo "ENCRYPTION_KEY=<generated-key>" >> .env.production

# Validate configuration
python -c "from config import system_config; system_config.validate_production_config()"
```

---

## API Server Issues

### Issue: API server won't start

**Symptoms:**
```
Address already in use
```

**Diagnosis:**
```bash
# Check if port 8000 is in use
lsof -i :8000
netstat -tulpn | grep 8000

# Find process using port
ps aux | grep uvicorn
```

**Solution:**
```bash
# Kill existing process
kill <PID>

# Or use different port
API_PORT=8001 python api/server.py

# Or stop via systemd
systemctl stop snflwr-api
```

---

### Issue: API returns 500 Internal Server Error

**Symptoms:**
- All requests return 500
- Generic error message
- No specific details

**Diagnosis:**
```bash
# Check error logs
tail -100 logs/errors.log

# Check application logs
tail -100 logs/snflwr.log

# Check for Python errors
journalctl -u snflwr-api -n 100
```

**Common Causes & Solutions:**

**1. Database connection failed:**
```bash
# Test database connection
python -c "from storage.database import db_manager; print(db_manager.execute_read('SELECT 1'))"

# Fix: Check DATABASE_TYPE in .env
# Fix: Verify PostgreSQL is running (if using PostgreSQL)
systemctl status postgresql
```

**2. Missing environment variables:**
```bash
# Check all required variables are set
grep -v '^#' .env.production | grep '='

# Verify loaded in Python
python -c "from config import system_config; print(system_config.JWT_SECRET_KEY)"
```

**3. Import errors:**
```bash
# Check for syntax errors
python -m py_compile api/server.py

# Check for missing dependencies
pip install -r requirements.txt
```

---

### Issue: CORS errors in browser

**Symptoms:**
```
Access to XMLHttpRequest blocked by CORS policy
```

**Solution:**
```bash
# Check CORS_ORIGINS setting
grep CORS_ORIGINS .env.production

# Update to include frontend origin
echo "CORS_ORIGINS=http://localhost:3000,https://app.snflwr.ai" >> .env.production

# Restart API
systemctl restart snflwr-api

# Verify CORS headers
curl -I -X OPTIONS http://localhost:8000/api/profiles/list \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: GET"
```

---

## Database Problems

### Issue: Database locked (SQLite)

**Symptoms:**
```
sqlite3.OperationalError: database is locked
```

**Solution:**
```bash
# Check for stale lock files
ls -la ~/.local/share/snflwr_ai/*.lock

# Remove lock file (only if no processes running)
rm ~/.local/share/snflwr_ai/snflwr.db-journal

# Switch to WAL mode for better concurrency
sqlite3 ~/.local/share/snflwr_ai/snflwr.db "PRAGMA journal_mode=WAL;"

# Long-term: Use PostgreSQL for production
```

---

### Issue: PostgreSQL connection refused

**Symptoms:**
```
psycopg2.OperationalError: could not connect to server: Connection refused
```

**Diagnosis:**
```bash
# Check PostgreSQL status
systemctl status postgresql
pg_isready

# Check if listening on correct port
netstat -an | grep 5432

# Check PostgreSQL logs
sudo tail -50 /var/log/postgresql/postgresql-16-main.log
```

**Solution:**
```bash
# Start PostgreSQL
sudo systemctl start postgresql

# Check configuration
psql -U snflwr -d snflwr_db -c "SELECT version();"

# Verify connection parameters
echo "POSTGRES_HOST: $POSTGRES_HOST"
echo "POSTGRES_PORT: $POSTGRES_PORT"
echo "POSTGRES_USER: $POSTGRES_USER"

# Test connection
psql -h localhost -p 5432 -U snflwr -d snflwr_db
```

---

### Issue: Slow database queries

**Symptoms:**
- API responses >5 seconds
- High database CPU
- Query timeouts

**Diagnosis:**
```bash
# Check slow queries (PostgreSQL)
psql -U snflwr -d snflwr_db -c "
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;"

# Check missing indexes
psql -U snflwr -d snflwr_db -c "
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats
WHERE schemaname='public' AND n_distinct > 1000;"
```

**Solution:**
```bash
# Add performance indexes
python database/add_performance_indexes.py

# Update statistics
psql -U snflwr -d snflwr_db -c "ANALYZE;"

# Vacuum database
psql -U snflwr -d snflwr_db -c "VACUUM ANALYZE;"

# Enable query logging
# Edit postgresql.conf: log_min_duration_statement = 1000
```

---

## Safety Monitoring Issues

### Issue: Safety incidents not logging

**Symptoms:**
- No entries in safety_incidents table
- Parent alerts not sent
- Safety logs empty

**Diagnosis:**
```bash
# Check if safety monitoring is enabled
python -c "from config import system_config; print(f'Enabled: {system_config.ENABLE_SAFETY_MONITORING}')"

# Check safety logs
tail -50 logs/safety_incidents.log

# Test safety pipeline
python -c "
from safety.pipeline import safety_pipeline
result = safety_pipeline.check_input(text='test message', age=10, profile_id='test_profile')
print(f'Safe: {result.is_safe}, Stage: {result.stage}, Reason: {result.reason}')
"
```

**Solution:**
```bash
# Enable safety monitoring
export ENABLE_SAFETY_MONITORING=true
systemctl restart snflwr-api

# Verify database table exists
sqlite3 database.db "SELECT COUNT(*) FROM safety_incidents;"

# Check file permissions
ls -la logs/safety_incidents.log
chmod 644 logs/safety_incidents.log
```

---

### Issue: Safety filter too strict/lenient

**Symptoms:**
- Legitimate content blocked
- Inappropriate content passing through

**Tuning:**
```python
# Edit config.py - Adjust prohibited keywords
# For stricter filtering:
PROHIBITED_KEYWORDS['custom'] = ['additional', 'keywords']

# For more lenient:
# Remove overly broad keywords from PROHIBITED_KEYWORDS

# Test specific content
from safety.pipeline import safety_pipeline
test_content = "Your test message here"
result = safety_pipeline.check_input(text=test_content, age=10, profile_id="test")
print(f"Safe: {result.is_safe}, Reason: {result.reason}")
```

---

## Performance Problems

### Issue: High CPU usage

**Symptoms:**
- CPU at 100%
- Slow response times
- System unresponsive

**Diagnosis:**
```bash
# Check CPU usage
top -o %CPU

# Check which process
ps aux --sort=-%cpu | head -10

# Check API workers
ps aux | grep uvicorn | wc -l
```

**Solution:**
```bash
# Reduce number of workers
export API_WORKERS=2
systemctl restart snflwr-api

# Check for CPU-intensive operations
# Review recent code changes
git log --oneline -10

# Add caching for expensive operations
export REDIS_ENABLED=true
systemctl restart snflwr-api

# Scale horizontally (if using load balancer)
# Add more servers
```

---

### Issue: High memory usage / Memory leaks

**Symptoms:**
- Memory usage climbing over time
- OOMKiller events
- Service crashes

**Diagnosis:**
```bash
# Monitor memory over time
watch -n 5 'free -m'

# Check memory by process
ps aux --sort=-%mem | head -10

# Check for memory leaks
ps -o pid,user,%mem,vsz,rss,cmd -p $(pgrep -f snflwr-api)

# Profile memory usage
python -m memory_profiler api/server.py
```

**Solution:**
```bash
# Restart service (temporary)
systemctl restart snflwr-api

# Reduce workers
export API_WORKERS=2

# Add swap space
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Long-term: Fix memory leaks in code
# Use profiling tools to identify leaks
```

---

## Authentication & Authorization

### Issue: JWT token expired

**Symptoms:**
```
401 Unauthorized: Invalid or expired session
```

**Solution:**
```bash
# User needs to login again
# Tokens expire after JWT_EXPIRATION_HOURS (default: 24h)

# Increase expiration time (if desired)
echo "JWT_EXPIRATION_HOURS=48" >> .env.production
systemctl restart snflwr-api

# Check token expiration
python -c "
import jwt
token = 'your_token_here'
decoded = jwt.decode(token, options={'verify_signature': False})
print(f'Expires: {decoded.get(\"exp\")}')
"
```

---

### Issue: Cannot access child profile (403 Forbidden)

**Symptoms:**
```
403 Forbidden: This is not your child's profile
```

**Diagnosis:**
```bash
# Verify profile ownership
python -c "
from storage.database import db_manager
profile = db_manager.execute_read(
    'SELECT parent_id FROM child_profiles WHERE profile_id = ?',
    ('profile_id_here',)
)
print(f'Parent ID: {profile[0][\"parent_id\"]}')
"

# Check user's role
python -c "
from core.authentication import auth_manager
session = auth_manager.validate_session('token_here')
print(f'User ID: {session.user_id}, Role: {session.role}')
"
```

**Solution:**
```bash
# Ensure logged in with correct account
# Profile belongs to parent_id, not current user

# If admin, admin should have access to all profiles
# Check role is 'admin' in database
```

---

## Email & Notifications

### Issue: Emails not sending

**Symptoms:**
- Parent alerts not received
- Verification emails not arriving
- No email errors in logs

**Diagnosis:**
```bash
# Check SMTP configuration
grep SMTP .env.production

# Test SMTP connection
python -c "
import smtplib
from config import system_config

server = smtplib.SMTP(system_config.SMTP_HOST, system_config.SMTP_PORT)
server.starttls()
server.login(system_config.SMTP_USERNAME, system_config.SMTP_PASSWORD)
server.quit()
print('✅ SMTP connection successful')
"

# Check email service logs
tail -50 logs/snflwr.log | grep email
```

**Common Solutions:**

**1. SMTP credentials invalid:**
```bash
# Verify SendGrid API key
curl -X POST https://api.sendgrid.com/v3/mail/send \
  -H "Authorization: Bearer $SMTP_PASSWORD" \
  -H "Content-Type: application/json" \
  -d '{"personalizations":[{"to":[{"email":"test@example.com"}]}],"from":{"email":"noreply@snflwr.ai"},"subject":"Test","content":[{"type":"text/plain","value":"Test"}]}'

# Expected: 202 Accepted
```

**2. SMTP disabled:**
```bash
# Enable SMTP
echo "SMTP_ENABLED=true" >> .env.production
systemctl restart snflwr-api
```

**3. Firewall blocking port 587:**
```bash
# Check if port 587 is open
telnet smtp.sendgrid.net 587

# If blocked, open firewall
sudo ufw allow out 587/tcp
```

---

### Issue: Emails going to spam

**Solutions:**
- Set up SPF record: `v=spf1 include:sendgrid.net ~all`
- Set up DKIM in SendGrid
- Set up DMARC record
- Use verified sender domain
- Avoid spam trigger words in email content

---

## Docker & Deployment

### Issue: Docker container won't start

**Symptoms:**
```
Error starting userland proxy: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Solution:**
```bash
# Find process using port
lsof -i :8000

# Stop conflicting container
docker stop <container_id>

# Or change port mapping
docker run -p 8001:8000 ...

# Check container logs
docker logs snflwr-api

# Remove and recreate
docker-compose down
docker-compose up -d
```

---

### Issue: Docker build fails

**Symptoms:**
```
ERROR [stage-name X/Y] RUN pip install...
```

**Solutions:**
```bash
# Clear Docker build cache
docker builder prune -a

# Build with no cache
docker build --no-cache -f docker/Dockerfile -t snflwr-api .

# Check Dockerfile syntax
docker build --check -f docker/Dockerfile .

# Build with verbose output
docker build --progress=plain -f docker/Dockerfile -t snflwr-api .
```

---

## Frontend/Open WebUI

### Issue: Frontend can't connect to API

**Symptoms:**
- Network errors in browser console
- API calls fail
- CORS errors

**Solutions:**
```bash
# Check API is running
curl http://localhost:8000/health

# Check CORS configuration
# Frontend must be in CORS_ORIGINS

# Update Open WebUI environment
echo "SNFLWR_API_URL=http://localhost:8000" >> frontend/open-webui/.env

# Check nginx reverse proxy (if used)
nginx -t
systemctl reload nginx
```

---

## Diagnostic Tools

### Health Check Script

```bash
#!/bin/bash
# health-check.sh - Comprehensive system check

echo "=== snflwr.ai Health Check ==="
echo ""

# API Health
echo "1. API Health:"
curl -s http://localhost:8000/health | jq '.' || echo "❌ API not responding"
echo ""

# Database
echo "2. Database:"
python -c "from storage.database import db_manager; print('✅ Database OK')" 2>&1
echo ""

# Ollama
echo "3. Ollama:"
curl -s http://localhost:11434/api/tags | jq '.models | length' && echo "models available" || echo "❌ Ollama not responding"
echo ""

# Disk Space
echo "4. Disk Space:"
df -h / | tail -1
echo ""

# Memory
echo "5. Memory:"
free -h | grep Mem
echo ""

# Logs
echo "6. Recent Errors:"
tail -5 logs/errors.log 2>/dev/null || echo "No recent errors"
```

### Log Analysis Script

```bash
#!/bin/bash
# analyze-logs.sh - Analyze error patterns

echo "=== Error Analysis (Last Hour) ==="

# Count errors by type
echo "Error counts:"
grep ERROR logs/snflwr.log | \
  awk '{print $5}' | \
  sort | uniq -c | sort -rn

# Recent critical errors
echo -e "\nCritical errors:"
grep CRITICAL logs/snflwr.log | tail -5

# Safety incidents
echo -e "\nSafety incidents:"
grep "Safety incident" logs/snflwr.log | tail -5
```

### Performance Monitoring

```bash
# Monitor API performance
watch -n 5 'curl -s http://localhost:8000/api/metrics | grep response_time'

# Monitor database connections
watch -n 5 'psql -U snflwr -d snflwr_db -c "SELECT count(*) FROM pg_stat_activity;"'

# Monitor system resources
htop
```

---

## Getting Help

### Before Asking for Help

1. **Check logs:**
   ```bash
   tail -100 logs/errors.log
   tail -100 logs/snflwr.log
   journalctl -u snflwr-api -n 100
   ```

2. **Run diagnostics:**
   ```bash
   bash health-check.sh
   ```

3. **Check recent changes:**
   ```bash
   git log --oneline -10
   ```

### When Reporting Issues

Include:
- snflwr.ai version: `grep VERSION config.py`
- Python version: `python --version`
- Operating system: `uname -a`
- Error messages (full stack trace)
- Steps to reproduce
- Expected vs actual behavior

### Support Channels

- **GitHub Issues:** https://github.com/tmartin2113/snflwr-ai/issues
- **Documentation:** https://docs.snflwr.ai
- **Email:** support@snflwr.ai

---

**Document Version:** 1.0
**Last Updated:** 2025-12-25
**Next Review:** 2026-01-25 (monthly)
