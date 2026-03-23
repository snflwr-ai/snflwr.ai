---
---

# Incident Response Runbook
## snflwr.ai Production Operations

**Last Updated:** 2025-12-25
**Version:** 1.0
**Owner:** Operations Team

---

## Table of Contents

1. [Incident Classification](#incident-classification)
2. [Response Team](#response-team)
3. [Communication Channels](#communication-channels)
4. [Incident Response Procedures](#incident-response-procedures)
5. [Common Incidents](#common-incidents)
6. [Security Incidents](#security-incidents)
7. [Post-Incident Review](#post-incident-review)

---

## Incident Classification

### Severity Levels

| Level | Impact | Response Time | Examples |
|-------|--------|---------------|----------|
| **P0 - Critical** | Complete service outage | 15 minutes | Database down, API completely unavailable |
| **P1 - High** | Major functionality degraded | 1 hour | Safety monitoring disabled, high error rate |
| **P2 - Medium** | Minor functionality impaired | 4 hours | Slow performance, non-critical feature down |
| **P3 - Low** | Minimal user impact | 24 hours | UI glitch, documentation error |

### Escalation Matrix

```
P0: Immediate → On-call Engineer → Engineering Lead → CTO
P1: 15 min → On-call Engineer → Engineering Lead
P2: 1 hour → On-call Engineer
P3: Next business day → Engineering Team
```

---

## Response Team

### Roles

| Role | Responsibility | Contact |
|------|----------------|---------|
| **Incident Commander** | Overall incident coordination | ops@snflwr.ai |
| **Technical Lead** | Technical investigation & resolution | tech-lead@snflwr.ai |
| **Communications Lead** | User communication & updates | comms@snflwr.ai |
| **Security Lead** | Security incident response | security@snflwr.ai |

### On-Call Schedule

- **Primary:** Check PagerDuty schedule
- **Secondary:** Check PagerDuty schedule
- **Escalation:** engineering-oncall@snflwr.ai

---

## Communication Channels

### Internal

- **Slack:** #incidents (primary)
- **PagerDuty:** alerts and escalations
- **Status Page:** Internal dashboard

### External

- **Status Page:** https://status.snflwr.ai
- **Email:** Users subscribed to updates
- **Twitter:** @SnflwrAI (major incidents only)

### Communication Templates

**Initial Update (within 15 min):**
```
We're investigating reports of [issue]. We'll provide an update within [timeframe].
Started: [timestamp]
```

**Progress Update (every 30-60 min):**
```
Update: We've identified [cause] and are working on [solution].
Current status: [details]
ETA: [timeframe]
```

**Resolution:**
```
Resolved: [issue] has been fixed. Service is restored.
Resolved: [timestamp]
Duration: [duration]
Root cause: [brief explanation]
```

---

## Incident Response Procedures

### 1. Detection & Alerting

**Alert Sources:**
- Prometheus/AlertManager
- Health check monitoring
- User reports
- Automated tests
- Error tracking (Sentry)

**Immediate Actions:**
1. Acknowledge alert in PagerDuty
2. Join #incidents Slack channel
3. Assess severity (P0-P3)
4. Assign Incident Commander

### 2. Initial Assessment (5 minutes)

**Questions to answer:**
- [ ] What is broken?
- [ ] How many users affected?
- [ ] Is data at risk?
- [ ] Is this a security incident?
- [ ] What's the business impact?

**Quick Checks:**
```bash
# Health check
curl https://api.snflwr.ai/health

# Prometheus metrics
curl https://api.snflwr.ai/api/metrics | grep snflwr_

# Recent errors
tail -100 /var/log/snflwr/errors.log

# System resources
top
df -h
free -m

# Active connections
netstat -an | grep 8000 | wc -l
```

### 3. Communication

**15-minute update:**
- Post initial status to status page
- Notify #incidents channel
- Update PagerDuty incident

### 4. Investigation & Mitigation

**Investigation checklist:**
- [ ] Check recent deployments
- [ ] Review error logs
- [ ] Check system metrics
- [ ] Verify external dependencies
- [ ] Check database status
- [ ] Review recent code changes

**Mitigation options:**
- Restart services
- Rollback deployment
- Scale resources
- Enable maintenance mode
- Failover to backup

### 5. Resolution

**Resolution checklist:**
- [ ] Fix implemented
- [ ] Monitored for 15 minutes
- [ ] Metrics returned to normal
- [ ] No new errors
- [ ] Users notified

### 6. Post-Incident

Within 24 hours:
- [ ] Write incident report
- [ ] Schedule post-mortem
- [ ] Create action items
- [ ] Update runbook

---

## Common Incidents

### 🔴 P0: API Server Down

**Symptoms:**
- Health check fails
- 100% error rate
- Users cannot access service

**Diagnosis:**
```bash
# Check if process is running
ps aux | grep uvicorn

# Check service status
systemctl status snflwr-api

# Check logs
journalctl -u snflwr-api -n 100

# Check port
netstat -tulpn | grep 8000
```

**Resolution:**
```bash
# Restart service
sudo systemctl restart snflwr-api

# Check status
curl http://localhost:8000/health

# If still failing, check configuration
python -c "from config import system_config; system_config.validate_production_config()"

# Check database connectivity
python -c "from storage.database import db_manager; db_manager.execute_read('SELECT 1')"
```

**Escalation:**
If restart doesn't work within 5 minutes, escalate to Engineering Lead.

---

### 🔴 P0: Database Unavailable

**Symptoms:**
- API returns 503
- Logs show database connection errors
- Queries timing out

**Diagnosis:**
```bash
# PostgreSQL
sudo systemctl status postgresql
pg_isready -h localhost -p 5432

# Check connections
psql -U snflwr -d snflwr_db -c "SELECT count(*) FROM pg_stat_activity;"

# Check disk space
df -h /var/lib/postgresql
```

**Resolution:**
```bash
# Restart PostgreSQL
sudo systemctl restart postgresql

# Check for lock files
ls -la /var/lib/postgresql/*/main/postmaster.pid

# Restore from backup if corrupted
python scripts/backup_database.py restore --file /backups/latest.dump

# Verify recovery
psql -U snflwr -d snflwr_db -c "SELECT 1;"
```

---

### 🟠 P1: High Error Rate (>5%)

**Symptoms:**
- Error rate above normal (>5%)
- Some users affected
- Intermittent failures

**Diagnosis:**
```bash
# Check error logs
tail -100 logs/errors.log

# Check error rate
curl http://localhost:8000/api/metrics | grep error

# Identify error patterns
grep ERROR logs/snflwr.log | cut -d' ' -f4- | sort | uniq -c | sort -rn
```

**Resolution:**
```bash
# If OOM errors - restart with more memory
API_WORKERS=2 systemctl restart snflwr-api

# If database timeout - check slow queries
psql -U snflwr -d snflwr_db -c "SELECT pid, now() - query_start as duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC;"

# If Ollama timeout - check Ollama service
curl http://localhost:11434/api/tags
systemctl status ollama
```

---

### 🟠 P1: Safety Monitoring Disabled

**Symptoms:**
- `ENABLE_SAFETY_MONITORING=false` in logs
- Safety incidents not logged
- Parent alerts not sent

**Impact:** CRITICAL - Students not protected

**Immediate Action:**
```bash
# Verify configuration
grep ENABLE_SAFETY_MONITORING .env.production

# If disabled, enable immediately
sed -i 's/ENABLE_SAFETY_MONITORING=false/ENABLE_SAFETY_MONITORING=true/' .env.production

# Restart service
systemctl restart snflwr-api

# Verify enabled
curl http://localhost:8000/health | jq '.safety_monitoring'
```

**Post-Resolution:**
- Notify parents of temporary gap
- Review safety logs for missed incidents
- Create incident report

---

### 🟡 P2: Slow Response Times (>5s)

**Symptoms:**
- API response time >5 seconds
- User complaints about slowness
- High CPU/memory usage

**Diagnosis:**
```bash
# Check system resources
top
free -m
iostat -x 1

# Check database
psql -U snflwr -d snflwr_db -c "SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"

# Missing indexes
python database/add_performance_indexes.py

# Check cache hit rate
curl http://localhost:8000/api/metrics | grep cache
```

**Resolution:**
```bash
# Scale workers if CPU < 80%
export API_WORKERS=8
systemctl restart snflwr-api

# Add database indexes
python database/add_performance_indexes.py

# Enable Redis caching
export REDIS_ENABLED=true
systemctl restart snflwr-api

# Optimize queries
psql -U snflwr -d snflwr_db -c "ANALYZE;"
```

---

### 🟡 P2: High Memory Usage (>85%)

**Symptoms:**
- Memory usage >85%
- OOMKiller events
- Service crashes

**Diagnosis:**
```bash
# Check memory usage
free -m
ps aux --sort=-%mem | head -10

# Check for memory leaks
ps -o pid,user,%mem,vsz,rss,cmd -p $(pgrep -f snflwr-api)
```

**Resolution:**
```bash
# Restart service (temporary fix)
systemctl restart snflwr-api

# Reduce workers
export API_WORKERS=2
systemctl restart snflwr-api

# Add swap (emergency)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Long-term: upgrade server or optimize code
```

---

### 🟢 P3: Email Delivery Failures

**Symptoms:**
- Parent alerts not received
- Email bounce backs
- SMTP errors in logs

**Diagnosis:**
```bash
# Check SMTP configuration
grep SMTP .env.production

# Test SMTP connection
python -c "
from core.email_service import email_service
result = email_service.send_test_email('test@example.com')
print(f'Email sent: {result}')
"
```

**Resolution:**
```bash
# Verify SMTP credentials
# Check SendGrid/SMTP provider status
# Update SMTP configuration if needed
# Resend failed alerts
python scripts/resend_failed_alerts.py
```

---

## Security Incidents

### 🔴 CRITICAL: Data Breach Suspected

**IMMEDIATE ACTIONS (DO NOT DELAY):**

1. **Isolate (5 minutes)**
   ```bash
   # Block all incoming traffic
   sudo iptables -A INPUT -j DROP

   # Stop API service
   systemctl stop snflwr-api

   # Enable maintenance mode
   ```

2. **Notify (10 minutes)**
   - Security Lead
   - Legal Team
   - CTO/CEO
   - Prepare breach notification

3. **Assess (30 minutes)**
   - What data was accessed?
   - How many users affected?
   - Was PII exposed?
   - Is attacker still active?

4. **Preserve Evidence**
   ```bash
   # Create forensic backup
   sudo dd if=/dev/sda of=/backup/forensic.img bs=4M

   # Copy logs
   cp -r /var/log/snflwr /backup/incident-logs-$(date +%Y%m%d)

   # Capture network state
   netstat -an > /backup/netstat-$(date +%Y%m%d).txt
   ```

5. **Eradicate & Recover**
   - Rotate all secrets (JWT, database passwords, API keys)
   - Patch vulnerability
   - Restore from clean backup
   - Force password resets

6. **Communicate**
   - Within 72 hours: Notify affected users (GDPR/COPPA)
   - Within 24 hours: Notify parents (COPPA)
   - Document timeline for regulators

---

### 🟠 HIGH: Suspicious Activity Detected

**Indicators:**
- Multiple failed login attempts
- Unusual API access patterns
- Unauthorized admin access attempts

**Investigation:**
```bash
# Check failed login attempts
psql -U snflwr -d snflwr_db -c "SELECT user_id, COUNT(*) FROM audit_log WHERE action='login_failed' AND timestamp > now() - interval '1 hour' GROUP BY user_id ORDER BY count DESC;"

# Check audit logs
tail -100 logs/snflwr.log | grep SECURITY

# Review access logs
sudo tail -100 /var/log/nginx/access.log | grep "POST /api/auth"
```

**Mitigation:**
```bash
# Block suspicious IPs (if identified)
sudo iptables -A INPUT -s <IP_ADDRESS> -j DROP

# Increase rate limiting
export RATE_LIMIT_AUTH_MAX=5
systemctl restart snflwr-api

# Force logout all sessions
psql -U snflwr -d snflwr_db -c "UPDATE auth_sessions SET is_active=0;"
```

---

## Post-Incident Review

### Incident Report Template

```markdown
# Incident Report: [Title]

**Date:** YYYY-MM-DD
**Severity:** P0/P1/P2/P3
**Duration:** X hours Y minutes
**Incident Commander:** [Name]

## Summary
Brief description of what happened.

## Impact
- Users affected: X
- Duration: Y hours
- Services impacted: [list]
- Revenue impact: $X (if applicable)

## Timeline
- HH:MM - Event occurred
- HH:MM - Alert triggered
- HH:MM - Investigation started
- HH:MM - Root cause identified
- HH:MM - Fix deployed
- HH:MM - Incident resolved

## Root Cause
Detailed explanation of what caused the incident.

## Resolution
How the incident was resolved.

## Action Items
1. [ ] Item 1 - Owner - Due date
2. [ ] Item 2 - Owner - Due date

## Lessons Learned
What we learned and what we'll do differently.

## Prevention
How we'll prevent this in the future.
```

### Post-Mortem Meeting

**Schedule:** Within 48 hours of resolution

**Attendees:**
- Incident Commander
- Technical Lead
- Affected team members
- Engineering Manager

**Agenda:**
1. Timeline review
2. Root cause analysis
3. Action items assignment
4. Process improvements

---

## Contact Information

### Emergency Contacts

| Role | Contact | Phone |
|------|---------|-------|
| On-Call Engineer | PagerDuty | - |
| Engineering Lead | tech-lead@snflwr.ai | +1-XXX-XXX-XXXX |
| Security Lead | security@snflwr.ai | +1-XXX-XXX-XXXX |
| CTO | cto@snflwr.ai | +1-XXX-XXX-XXXX |

### External Vendors

| Vendor | Service | Contact |
|--------|---------|---------|
| AWS | Infrastructure | aws-support |
| SendGrid | Email | support@sendgrid.com |
| PagerDuty | Alerting | support@pagerduty.com |

---

## Appendices

### A. Quick Reference Commands

```bash
# Service status
systemctl status snflwr-api
systemctl status postgresql
systemctl status ollama

# Restart services
systemctl restart snflwr-api

# View logs
journalctl -u snflwr-api -f
tail -f logs/errors.log

# Database access
psql -U snflwr -d snflwr_db

# Metrics
curl http://localhost:8000/api/metrics

# Health
curl http://localhost:8000/health
```

### B. Rollback Procedure

```bash
# 1. Stop current version
systemctl stop snflwr-api

# 2. Rollback code
cd /opt/snflwr-ai
git log --oneline -5  # Find previous version
git checkout <previous-commit>

# 3. Rollback database (if needed)
python scripts/backup_database.py restore --file /backups/pre-deploy.dump

# 4. Restart
systemctl start snflwr-api

# 5. Verify
curl http://localhost:8000/health
```

---

**Document Version:** 1.0
**Last Review:** 2025-12-25
**Next Review:** 2026-03-25 (quarterly)
