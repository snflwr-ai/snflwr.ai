---
---

# Incident Response Runbook
## snflwr.ai Production Operations

**Last Updated:** 2025-12-25
**Version:** 1.0
**Owner:** Operations Team

---

## Table of Contents

1. [Solo Responder Triage Mode](#solo-responder-triage-mode)
2. [Incident Classification](#incident-classification)
3. [Response Team](#response-team)
4. [Communication Channels](#communication-channels)
5. [Incident Response Procedures](#incident-response-procedures)
6. [Common Incidents](#common-incidents)
7. [Security Incidents](#security-incidents)
8. [K-12 Stakeholder Communication](#k-12-stakeholder-communication)
9. [Post-Incident Review](#post-incident-review)

---

## Solo Responder Triage Mode

> Read this first if you are the only person on call. The rest of this document was structured for a team of four; you are not a team of four.

The single most expensive mistake during a solo-responder incident is *silent investigation* — going deep into logs for 90 minutes while parents and school IT staff sit in the dark. Communicate early and partially. People tolerate "investigating" far better than they tolerate silence.

### First 30 minutes

- [ ] Acknowledge the alert — do not dismiss without looking.
- [ ] Answer one question: **is student data actively leaking right now?**
  - **Yes** → jump straight to [Data Breach Suspected](#-critical-data-breach-suspected). Post yellow status before doing anything else.
  - **No** → continue diagnosis below.
- [ ] Post a status update within 10 minutes even if incomplete: *"Investigating [symptom]. Next update in 15 minutes."* Silence is worse than partial truth.

### At 45 minutes, if still diagnosing

- [ ] **Stop drilling.** Open the status page and post a yellow update with a rough timeline.
- [ ] Start parallel forensics in the background (`docker compose logs --since=2h > /tmp/forensics-<date>.log &`).
- [ ] Sleep 20 minutes if you have been awake more than 12 hours. Set an alarm.
- [ ] Return to investigation with clarity. The first hour of fatigue costs more than the 20-minute pause.

### Breach-confirmed fork

- Do **not** wait for full forensics before notifying parents or schools. See [Parent Breach Notification by State](#parent-breach-notification-by-state) for the legal floor — it is much shorter than most people assume.
- Parallelize: comms first, then forensics. The state AG does not care that you were still grepping logs.

### What can stay broken vs. what cannot

| Symptom | Can wait | Why |
|---|---|---|
| Slow API responses (P2) | Yes, post yellow, sleep | Tutoring resumes when you wake up |
| Email delivery (P3) | Yes, until morning | Parents will retry; you owe them an apology, not a 3am push |
| Non-critical feature (P3) | Yes, document, move on | Don't compound by rushing a fix |
| Safety monitoring disabled (P1) | **No** | A child sees unsafe content in the time you let this ride |
| Data exposure suspected (P0) | **No** | Every minute is a worse breach-notification fact pattern |
| INTERNAL_API_KEY in logs/git | **No** | See [Internal API Key Compromise](#-internal-api-key-compromise) — has a no-downtime hot-swap path |

### One-line escalation script

If a school district IT lead reaches you while you are still firefighting, read this verbatim and put it in writing within the hour:

> "We're investigating an incident that may affect your district. I'll have a status update with confirmed scope and a forensic export timeline to you by [hour + 2]. I'm the single point of contact: [your phone, your email]. Please reply with the name of your district's incident lead so I can keep them on every update."

This buys you two hours and binds the district to one contact instead of three. Use it.

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

### 🔴 CRITICAL: INTERNAL_API_KEY Compromise

`INTERNAL_API_KEY` brokers the OWU → snflwr-api hop. Rotating it naively breaks that hop and downs the entire user-facing service. The hot-swap below is the only safe path.

> **Do not run `sed -i ".../.env"` on the API key in production without reading this.** OWU will start returning 401 on every chat request the moment the API restarts with a new key, until OWU is also updated.

**Immediate (0–5 min):**

1. **Do not** rotate the key yet.
2. Determine scope of leak:
   - Was the key in `.env` checked into git? → search history with `git log -p -S 'INTERNAL_API_KEY'`
   - In a public Docker image layer? → `docker history --no-trunc <image>`
   - In a log file streamed to a third party? → check Sentry / Grafana / external log shippers
3. If scope is "key visible to attacker right now," post yellow status and continue. Otherwise treat as P1 and proceed methodically.

**Hot-swap (5–15 min):**

```bash
# 1. Generate the new key
NEW_KEY=$(openssl rand -hex 32)
echo "$NEW_KEY" > /tmp/new_internal_api_key.txt
chmod 600 /tmp/new_internal_api_key.txt

# 2. Update OWU's outbound auth header FIRST. snflwr-api still accepts
#    the OLD key — every existing chat request keeps working.
docker exec snflwr-frontend sh -c "sed -i 's|^OLLAMA_API_KEY=.*|OLLAMA_API_KEY=$NEW_KEY|' /app/backend/.env"
docker compose -f docker/compose/docker-compose.home.yml restart snflwr-frontend

# 3. Verify OWU is now sending the NEW key — chat should fail with 401
#    against snflwr-api because the API still expects the OLD key.
curl -fsS -H "Authorization: Bearer $NEW_KEY" http://snflwr-api:39150/health  # should succeed
curl -fsS http://snflwr-frontend:8080/api/health                                  # should succeed
# Now drive a chat through the UI. It WILL fail with "401 from Ollama" —
# that's the expected intermediate state. Do not panic, do not skip step 4.

# 4. Update snflwr-api to the NEW key. Brief downtime here is acceptable
#    because OWU is already sending the new key, so this restart RESTORES
#    service rather than breaking it.
sed -i "s|^INTERNAL_API_KEY=.*|INTERNAL_API_KEY=$NEW_KEY|" /opt/snflwr/.env.production
docker compose -f docker/compose/docker-compose.home.yml restart snflwr-api

# 5. Validate end-to-end: a chat request from the UI should now return 200.
curl -fsS http://snflwr-frontend:8080/api/health && \
  echo "OWU healthy" || echo "OWU broken — investigate"
```

**Post-rotation (15–60 min):**

- [ ] Confirm no client (other than OWU) was using the old key. Grep for usages: `grep -rn "Bearer $OLD_KEY" .` and external integrations.
- [ ] Shred the old key from password manager, git history (`git filter-repo` if it was committed), CI secrets.
- [ ] Log the rotation in `audit_log`: `INSERT INTO audit_log (timestamp, event_type, action, details) VALUES (NOW(), 'internal_api_key_rotated', 'hot_swap', '{"reason":"...","prior_key_hash":"...","scope":"..."}');`
- [ ] Update the `INTERNAL_API_KEY exposed` row in the [Solo Responder triage table](#what-can-stay-broken-vs-what-cannot) — if your detection path failed to catch this leak, add an alert.

### 🟠 HIGH: Emergency Child Profile Suspension

When a parent account is compromised mid-incident, the safe action is to suspend the child's access *without* deleting data (you still need it for forensics). This is distinct from COPPA-revoke cascade-delete — that one is irreversible and removes evidence.

```sql
-- 1. Mark every profile under the compromised parent as inactive.
--    is_active=0 is the existing soft-disable flag — child cannot log in,
--    OWU stops resolving them, but conversations stay in the DB for audit.
UPDATE child_profiles
   SET is_active = 0
 WHERE parent_id = '<COMPROMISED_PARENT_ID>';

-- 2. Mark every active session for that parent and their children as invalid.
UPDATE auth_tokens
   SET is_valid = 0
 WHERE user_id = '<COMPROMISED_PARENT_ID>'
    OR user_id IN (
        SELECT profile_id FROM child_profiles
         WHERE parent_id = '<COMPROMISED_PARENT_ID>'
    );

-- 3. Record the suspension in audit_log for the forensics timeline.
INSERT INTO audit_log
  (timestamp, event_type, user_id, user_type, action, details, success)
VALUES
  (datetime('now'), 'emergency_suspension', '<COMPROMISED_PARENT_ID>',
   'parent', 'profile_suspended',
   '{"reason":"suspected account compromise","incident_id":"<INCIDENT_ID>"}',
   1);
```

**Re-enable process** (only after the parent is verified):

1. Parent must reset password via the email-verification flow (not in-app).
2. Operator pulls `audit_log` for the suspension window to see what was accessed.
3. Parent signs an attestation confirming they understand the scope of the compromise.
4. Set `is_active = 1` on the child profile rows. Do **not** restore the old auth tokens — the parent re-authenticates from scratch.

### 🟠 HIGH: Parent Breach Notification by State

K-12 student data triggers state-specific notification timelines that are tighter than the GDPR 72-hour floor. The rule of thumb: notify parents *before* regulators, document delivery, do not wait for full forensics.

| State | Deadline | To notify | Key statute / reference |
|---|---|---|---|
| **California** | Without unreasonable delay (typically read as 60 days) | Parents + CA Attorney General | Cal. Civ. Code § 1798.82; AG portal: `oag.ca.gov/privacy/databreach` |
| **Florida** | 30 days | Parents + Florida AG | Fla. Stat. § 501.171 |
| **Texas** | 60 days | Parents + Texas AG | Tex. Bus. & Com. Code § 521.053 |
| **New York** | Most expedient time possible | Parents + NY AG + Dept. of Education (if SED-regulated) | NY Gen. Bus. Law § 899-aa; NY Ed Law § 2-d |
| **Illinois** | Most expedient time possible | Parents (+ AG if > 500 affected) | 815 ILCS 530/10 |

> The table above is operator guidance, not legal advice. Confirm the current statutory text with your privacy counsel before sending anything; state laws change. The relevant references are also flagged in `legal/LAWYER_REVIEW_CHECKLIST.md`.

**Practical sequence:**

1. **First 4 hours**: identify scope. Which `profile_id`s were affected, which parent emails, which states.
2. **Hours 4–24**: draft a parent notification (template below) and send via authenticated email *plus* certified mail to the registered guardian address. The certified-mail receipt is your delivery proof if FERPA standing is challenged.
3. **Hours 24–48**: notify each applicable state AG / DOE in parallel. Most states have an online breach-report portal.
4. **Within 5 business days**: notify school districts whose data was affected — even if the deadline above hasn't expired, districts will hear about it from parents and you want to be the source.

**Parent notification template** (drop into your email tool):

```
Subject: [snflwr.ai] Notice of Security Incident Affecting [Child First Name]

On [DATE], we discovered [SCOPE — e.g., "that an unauthorized party
accessed the account associated with your email"]. We immediately
[ACTION — e.g., "suspended the affected account and rotated all
credentials"].

Your child's [SPECIFIC DATA TYPE — e.g., "first name and chat
transcripts from [DATE RANGE]"] may have been accessed. No financial
data is stored in our system. [If applicable: No biometric data,
no real-time location data, and no full name was exposed.]

We have notified [STATE AG / DEPT OF ED] as required by law.

Steps we are taking:
- [Action 1]
- [Action 2]

Steps you can take:
- Change the password on your snflwr.ai account at [URL].
- Monitor your child's school accounts for unusual activity.
- Contact us at [PHONE / EMAIL] with any questions.

This notice satisfies our obligations under [STATE STATUTE] and
34 CFR § 99.12 (FERPA).

[Operator name + role + contact]
[snflwr.ai mailing address required for certified mail receipt]
```

Keep the language plain. Lawyers can pretty it up later; speed beats polish.

---

## K-12 Stakeholder Communication

### School District IT Escalation

When a Home Server or Enterprise tier deployment sits on a school network, a breach affecting that school is also their breach. They will hear about it from parents within hours; be the source.

**Within 15 minutes of detection:**

- [ ] Pull the affected district list from `child_profiles` joined to the school metadata table (or your CRM).
- [ ] Email each district's IT lead and superintendent simultaneously (do **not** route through helpdesk — go above it).
- [ ] Prepare a forensic export of `audit_log`, `safety_incidents`, and login attempts for the affected window.

**Email template:**

```
To: [district_it_lead@district.k12.STATE.us], [superintendent@district.k12.STATE.us]
CC: [your privacy lawyer]
Subject: [URGENT] snflwr.ai security incident affecting [district name]

We've identified a security incident that may affect [N] students in
your district. Access was discovered on [TIMESTAMP].

Immediate steps we've taken:
- Suspended affected accounts (full list available on request)
- Preserved all audit logs and chat transcripts
- Rotated INTERNAL_API_KEY and parent session tokens

What we need from you:
- Confirmation of affected student count in your SIS
- Any parallel anomalies you've observed (failed login alerts,
  unusual after-hours traffic)
- Your incident lead's name and direct line

Next steps from us:
- Full forensic export to your district IT lead within [24 hours],
  delivered via [SFTP / signed S3 URL — pick one and commit].
- Parent notifications go out from us per [STATE] law within [N] days.
- You may want to brief your school board's legal counsel.

Single point of contact this incident: [your name, phone, email]

[Your name]
[Your title]
snflwr.ai
```

**Forensic export SLA:**

| Hours after detection | Deliverable |
|---|---|
| 2 | Export queued and acknowledged in writing |
| 6 | Signed download link sent to district IT + your legal counsel |
| 24 | Full timeline reconstruction (request → response, with PII redacted as needed) |

The export should include, per district:

- `audit_log` rows for the affected window
- Failed login attempts (`auth_tokens` where `is_valid = 0`)
- Chat transcripts and `safety_incidents` for affected `profile_id`s
- A CSV of affected parent emails + child first names (no full names if redactable) for the district's own notification path

### When to put a lawyer between you and the conversation

- The district threatens litigation.
- The district demands compliance documents you do not have (SOC 2 report, signed BAA with subprocessors).
- The conversation involves more than one state's AG.
- A reporter has called the district before you finish notifying parents.

In each case, route further communications through `legal/DATA_PROCESSING_AGREEMENT.md`'s signed-DPA holder at the district and your privacy counsel. Stop replying directly. This is not unfriendly — it is the standard playbook and the district's lawyers will respect it.

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
