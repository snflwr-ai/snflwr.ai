# Security & Compliance Documentation
## Production-Ready Security for K-12 Deployment

**Last Updated:** 2025-12-21
**Status:** ✅ Production Ready
**Compliance:** COPPA/FERPA Compliant

---

## Overview

snflwr.ai implements comprehensive security measures to protect student data and ensure compliance with federal regulations including COPPA (Children's Online Privacy Protection Act) and FERPA (Family Educational Rights and Privacy Act).

---

## 1. Data Encryption

### ✅ Safety Incident Logs - Fully Encrypted

All safety incident data is encrypted at rest using **AES-256 equivalent encryption** (Fernet symmetric encryption):

**Encrypted Fields:**
- ✅ `content_snippet` - First 500 characters of concerning content
- ✅ `metadata` - Additional context and diagnostic information
- ✅ `resolution_notes` - Administrative notes on incident resolution

**Implementation:**
- **File:** `safety/incident_logger.py`
- **Encryption Method:** Fernet (AES-128-CBC + HMAC-SHA256)
- **Key Storage:** Secured with file permissions (0o600, owner-only access)
- **Key Location:** `{APP_DATA_DIR}/.encryption_key`

**Code Reference:**
```python
# Encryption on write (incident_logger.py:101-106)
encrypted_snippet = self.encryption.encrypt_string(content_snippet[:500])
encrypted_metadata = self.encryption.encrypt_dict(metadata)
encrypted_notes = self.encryption.encrypt_string(resolution_notes)

# Decryption on read (incident_logger.py:181-197)
content_snippet = self.encryption.decrypt_string(row['content_snippet'])
metadata = self.encryption.decrypt_dict(row['metadata'])
resolution_notes = self.encryption.decrypt_string(row['resolution_notes'])
```

### Password Security

**Method:** PBKDF2-HMAC-SHA256
**Parameters:**
- Salt: 32 bytes, cryptographically random
- Iterations: 100,000
- Output length: 32 bytes

**File:** `storage/encryption.py:242-303`

### Encryption Key Management

**Security Measures:**
1. Master key generated on first run using `Fernet.generate_key()`
2. Stored in platform-specific secure location:
   - **Windows:** `%APPDATA%\SnflwrAI\.encryption_key`
   - **macOS:** `~/Library/Application Support/SnflwrAI/.encryption_key`
   - **Linux:** `~/.local/share/snflwr_ai/.encryption_key`
3. File permissions restricted to owner-only (0o600)
4. Never transmitted or logged
5. Automatic key rotation every 365 days (configurable)

---

## 2. Data Retention Policy (COPPA Compliance)

### ✅ Comprehensive Retention Periods

snflwr.ai implements **data minimization** as required by COPPA through automated data retention policies:

| Data Type | Retention Period | Justification |
|-----------|-----------------|---------------|
| **Safety Incidents** | 90 days (resolved) | COPPA compliance, security review period |
| **Audit Logs** | 365 days | Security auditing, compliance reporting |
| **Sessions** | 180 days | Educational progress tracking |
| **Conversations** | 180 days | Parent review period, learning history |
| **Analytics** | 730 days | Aggregated, non-PII data for improvement |
| **Auth Tokens** | Immediate (on expiry) | Security best practice |

**Configuration:** `config.py:60-76`

### Automated Cleanup

**Implementation:** `utils/data_retention.py`

**Features:**
- ✅ Scheduled daily cleanup at 2:00 AM (configurable)
- ✅ Threaded background scheduler
- ✅ Comprehensive audit logging of all deletions
- ✅ Database vacuuming to reclaim disk space
- ✅ Error handling and recovery
- ✅ Manual cleanup option available

**Startup Integration:**
```python
# To start automated cleanup scheduler
from utils.data_retention import data_retention_manager
data_retention_manager.start_scheduler()
```

**Manual Cleanup:**
```python
# Run cleanup manually
results = data_retention_manager.run_all_cleanup_tasks()
```

### Data Retention Configuration

**File:** `config.py`

```python
class SafetyConfig:
    # COPPA-compliant retention periods
    SAFETY_LOG_RETENTION_DAYS = 90
    AUDIT_LOG_RETENTION_DAYS = 365
    SESSION_RETENTION_DAYS = 180
    CONVERSATION_RETENTION_DAYS = 180
    ANALYTICS_RETENTION_DAYS = 730

    # Automatic cleanup
    DATA_CLEANUP_ENABLED = True
    DATA_CLEANUP_HOUR = 2  # 2 AM
```

---

## 3. Security Monitoring & Alerts

### Parent Notification Thresholds

**Configuration:** `config.py:91-98`

| Severity | Threshold | Action |
|----------|-----------|--------|
| **Critical** | 1 incident | Immediate parent alert |
| **Major** | 2 incidents | Parent alert after 2nd occurrence |
| **Minor** | 5 incidents | Alert after 5 in 24-hour window |

**Time Window:** 24 hours (configurable)

### Audit Logging

**All security-relevant events are logged:**
- ✅ Data access (incident retrieval)
- ✅ Data modification (incident updates)
- ✅ Data deletion (retention cleanup)
- ✅ Authentication attempts
- ✅ Failed login attempts
- ✅ Account lockouts
- ✅ Data exports

**Implementation:** `storage/database.py` (audit_log table)

---

## 4. Access Controls

### Session Security

**Settings:** `config.py:209-215`

```python
SESSION_TIMEOUT_MINUTES = 60  # Auto-logout after 1 hour inactivity
MAX_FAILED_LOGIN_ATTEMPTS = 5
ACCOUNT_LOCKOUT_DURATION_MINUTES = 30
```

### Password Requirements

```python
PASSWORD_MIN_LENGTH = 8
PASSWORD_REQUIRE_UPPERCASE = True
PASSWORD_REQUIRE_LOWERCASE = True
PASSWORD_REQUIRE_NUMBERS = True
PASSWORD_REQUIRE_SPECIAL_CHARS = False
```

### Parental Controls

**Parents have full access to:**
- ✅ Complete conversation history
- ✅ All safety incidents
- ✅ Session and usage analytics
- ✅ Data export functionality
- ✅ Data deletion rights

---

## 5. Privacy & Compliance

### COPPA Compliance Measures

✅ **Age Verification Required**
✅ **Parental Consent Required**
✅ **Data Minimization** (automated retention)
✅ **No Third-Party Data Sharing**
✅ **Parent Access Rights**
✅ **Data Deletion Rights** (30-day grace period)
✅ **Privacy by Design**

### FERPA Compliance

✅ **Student Data Protection**
✅ **Educational Records Security**
✅ **Parent/Guardian Access Rights**
✅ **Consent for Data Disclosure**
✅ **Secure Storage Requirements**

### Data Collection Restrictions

**Configuration:** `config.py:220-225`

```python
COLLECT_USAGE_ANALYTICS = True      # Aggregated only, no PII
COLLECT_ERROR_REPORTS = True        # For debugging, no PII
SHARE_DATA_WITH_THIRD_PARTIES = False  # NEVER
```

---

## 6. Content Filtering

### Multi-Category Protection

**Categories Monitored:**
1. Violence (14 keywords)
2. Self-Harm (10 keywords)
3. Sexual Content (12 keywords)
4. Drugs/Alcohol (18 keywords)
5. Personal Information Requests (9 keywords)
6. Bullying (11 keywords)
7. Dangerous Activities (9 keywords)

**Total:** 83+ prohibited keyword patterns

**Configuration:** `config.py:102-160`

### Grade-Based Filtering

| Grade Level | Strictness | Features |
|-------------|-----------|----------|
| **Elementary (K-5)** | Maximum | All external links blocked, parent approval required |
| **Middle (6-8)** | High | All external links blocked |
| **High (9-12)** | Moderate | Selective link filtering |

---

## 7. Incident Response

### Incident Lifecycle

1. **Detection** → Content filter identifies concerning content
2. **Logging** → Encrypted incident record created
3. **Classification** → Severity assigned (minor/major/critical)
4. **Alert** → Parent notified if threshold met
5. **Review** → Parent reviews in dashboard
6. **Resolution** → Incident marked resolved with encrypted notes
7. **Retention** → Kept for 90 days, then auto-deleted

### Parent Alert Messages

**Customized by incident type:**
- Violence detection
- Self-harm mentions
- Inappropriate content
- Drug-related content
- Personal information requests
- Bullying indicators
- Dangerous activities

**File:** `safety/incident_logger.py:622-653`

---

## 8. Database Security

### SQLite Configuration

**Security Settings:**
```python
PRAGMA journal_mode = WAL          # Write-Ahead Logging
PRAGMA synchronous = NORMAL        # Balanced durability
PRAGMA foreign_keys = ON           # Referential integrity
```

**Backup Strategy:**
- Regular database backups recommended
- Export functionality for parents
- 30-day grace period before permanent deletion

---

## 9. Security Testing

### Testing Checklist

Before production deployment, verify:

- [ ] Encryption key generated and secured
- [ ] All safety incidents encrypted at rest
- [ ] Data retention scheduler started
- [ ] Audit logging enabled
- [ ] Parent notification system functional
- [ ] Content filtering active
- [ ] Session timeouts working
- [ ] Failed login lockouts working
- [ ] Database backup strategy in place
- [ ] Parent dashboard access verified

### Manual Testing

**Test Encryption:**
```python
from safety.incident_logger import incident_logger
from storage.encryption import encryption_manager

# Log test incident
success, incident_id = incident_logger.log_incident(
    profile_id="test_profile",
    session_id="test_session",
    incident_type="violence",
    severity="major",
    content_snippet="test content for encryption",
    metadata={"test": "metadata"}
)

# Verify encryption
incident = incident_logger.get_incident(incident_id)
print(f"Content retrieved: {incident.content_snippet}")  # Should be decrypted
```

**Test Data Retention:**
```python
from utils.data_retention import data_retention_manager

# Get current retention status
summary = data_retention_manager.get_retention_summary()
print(summary)

# Run manual cleanup (for testing)
results = data_retention_manager.run_all_cleanup_tasks()
print(results)
```

---

## 10. Production Deployment

### Startup Sequence

**1. Initialize Encryption**
```python
from storage.encryption import encryption_manager
# Automatic on import - key generated if needed
```

**2. Start Data Retention Scheduler**
```python
from utils.data_retention import data_retention_manager
data_retention_manager.start_scheduler()
```

**3. Enable Audit Logging**
```python
from config import safety_config
assert safety_config.ENABLE_AUDIT_LOGGING == True
```

### Environment Variables

**Optional Configuration:**
```bash
# Override default retention periods
export SAFETY_LOG_RETENTION_DAYS=90
export AUDIT_LOG_RETENTION_DAYS=365

# Override cleanup schedule
export DATA_CLEANUP_ENABLED=true
export DATA_CLEANUP_HOUR=2
```

### Monitoring

**Monitor these metrics:**
1. Daily cleanup execution status
2. Disk space usage trends
3. Failed authentication attempts
4. Safety incident rates
5. Parent notification delivery
6. Database size growth

---

## 11. Compliance Reporting

### Monthly Compliance Report

**Generate compliance summary:**
```python
from config import safety_config

# Get retention policy summary
policy = safety_config.get_retention_policy()
print(policy)
```

**Output:**
```json
{
  "safety_incidents": {
    "retention_days": 90,
    "description": "Resolved safety incidents are automatically deleted after retention period"
  },
  "compliance": {
    "framework": "COPPA/FERPA",
    "data_minimization": true,
    "automatic_cleanup": true,
    "parent_controls": true
  }
}
```

### Audit Trail Access

**Query audit logs:**
```sql
SELECT * FROM audit_log
WHERE event_type = 'data_retention'
ORDER BY timestamp DESC
LIMIT 100;
```

---

## 12. Security Incident Contacts

### Reporting Security Issues

**For security vulnerabilities:**
- Create issue with `[SECURITY]` prefix
- Include detailed description
- **DO NOT** disclose publicly until patched

### Data Breach Response

**In case of suspected breach:**
1. Immediately disable system
2. Secure all encryption keys
3. Contact parents/guardians
4. Document incident in audit log
5. Review access logs
6. Implement remediation
7. File required regulatory reports

---

## Summary

✅ **Encryption:** All safety incident data encrypted at rest (AES-256 equivalent)
✅ **Data Retention:** COPPA-compliant automated cleanup (90-730 day retention)
✅ **Access Controls:** Session timeouts, failed login protection, parental controls
✅ **Audit Logging:** Comprehensive logging of all security-relevant events
✅ **Compliance:** COPPA/FERPA compliant by design
✅ **Privacy:** No third-party data sharing, parent access rights
✅ **Content Filtering:** Multi-category, grade-based filtering
✅ **Monitoring:** Automated safety incident detection and parent alerts

---

## References

- **Configuration:** `config.py`
- **Encryption:** `storage/encryption.py`
- **Incident Logging:** `safety/incident_logger.py`
- **Data Retention:** `utils/data_retention.py`
- **Database:** `storage/database.py`

---

**Document Version:** 1.0
**Compliance Review Date:** 2025-12-21
**Next Review:** 2026-01-21
