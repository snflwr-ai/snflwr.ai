---
---

# Monitoring, Error Tracking & Email Alerts
## Production Deployment Guide

**Last Updated:** 2025-12-21
**Status:** ✅ Production Ready

---

## Overview

snflwr.ai includes comprehensive monitoring, error tracking, and email alert systems for production deployment. This document covers setup, configuration, and usage.

---

## 1. Error Tracking System

### Features

- ✅ Automatic exception capture and aggregation
- ✅ Error deduplication using hash-based identification
- ✅ Occurrence counting and trend analysis
- ✅ Severity levels: critical, error, warning
- ✅ Stack trace capture and storage
- ✅ Alert thresholds for frequent errors
- ✅ Resolution tracking

### Implementation

**File:** `utils/error_tracking.py`

### Usage

#### Automatic Exception Tracking

Use the `@track_exceptions` decorator:

```python
from utils.error_tracking import track_exceptions

@track_exceptions(severity='critical')
def critical_function():
    # Your code here
    ...

@track_exceptions(severity='error')
def important_function():
    # Your code here
    ...
```

#### Manual Error Capture

```python
from utils.error_tracking import error_tracker

# Capture exception
try:
    risky_operation()
except Exception as e:
    error_tracker.capture_exception(
        e,
        severity='error',
        user_id='user_123',
        session_id='session_456',
        context={'operation': 'data_processing'}
    )
```

#### Custom Error Logging

```python
error_tracker.capture_error(
    error_type='ValidationError',
    error_message='Invalid input data',
    severity='warning',
    module='data_validator.py',
    function='validate_input',
    line_number=42
)
```

### Viewing Error Summary

```python
from utils.error_tracking import error_tracker

# Get 7-day error summary
summary = error_tracker.get_error_summary(days=7)

print(f"Total unique errors: {summary['total_unique_errors']}")
print(f"Unresolved errors: {summary['unresolved_errors']}")

# Most frequent errors
for error in summary['most_frequent']:
    print(f"{error['error_type']}: {error['occurrence_count']} times")
```

### Resolving Errors

```python
# Mark error as resolved
error_tracker.mark_resolved(
    error_id=123,
    resolution_notes="Fixed by deploying patch v1.2.3"
)
```

### Database Table

Errors are stored in the `error_tracking` table with:
- Automatic deduplication (same error = same hash)
- Occurrence counting
- First seen / last seen timestamps
- Stack traces
- Severity levels
- Resolution tracking

---

## 2. Email Alert System

### Features

- ✅ SMTP-based email delivery
- ✅ HTML email templates
- ✅ Background email queue
- ✅ Retry logic with exponential backoff
- ✅ Multiple alert types:
  - Critical safety incidents
  - Major safety incidents
  - Daily activity digests
  - System error alerts

### Configuration

**File:** `utils/email_alerts.py`

### Environment Variables

Create a `.env` file or set environment variables:

```bash
# SMTP Configuration
SMTP_HOST=smtp.gmail.com              # Your SMTP server
SMTP_PORT=587                          # 587 for TLS, 465 for SSL
SMTP_USERNAME=your-email@gmail.com    # SMTP username
SMTP_PASSWORD=your-app-password        # SMTP password (use app-specific password)
FROM_EMAIL=noreply@snflwr.ai   # Sender email
FROM_NAME=snflwr.ai Safety Team     # Sender name

# Enable/Disable
ENABLE_EMAIL_ALERTS=true               # Set to 'true' to enable
```

### Gmail Setup Example

1. **Enable 2-Factor Authentication** on your Gmail account
2. **Generate App Password:**
   - Go to Google Account → Security → 2-Step Verification → App passwords
   - Generate password for "Mail" application
   - Use this password in `SMTP_PASSWORD`

3. **Configuration:**
```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-16-char-app-password
ENABLE_EMAIL_ALERTS=true
```

### Other Email Providers

**Outlook/Office 365:**
```bash
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
```

**SendGrid:**
```bash
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=your-sendgrid-api-key
```

**AWS SES:**
```bash
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=your-aws-access-key-id
SMTP_PASSWORD=your-aws-secret-access-key
```

### Starting Email System

```python
from utils.email_alerts import email_alert_system

# Start background email worker
email_alert_system.start_worker()

# Test SMTP connection
if email_alert_system.test_connection():
    print("✅ SMTP configured correctly")
else:
    print("❌ SMTP configuration error")
```

### Email Alert Types

#### 1. Critical Safety Incident

Automatically sent when a critical safety incident occurs:

```python
email_alert_system.send_safety_alert(
    parent_email="parent@example.com",
    child_name="Sarah",
    incident_type="self_harm",
    severity="critical",
    incident_id=123,
    timestamp="2025-12-21 14:30:00"
)
```

**Email includes:**
- 🚨 Urgent alert header
- Incident details (type, time, ID)
- Immediate action recommendations
- Crisis hotline information
- Link to full incident report

#### 2. Daily Activity Digest

Send parents a daily summary:

```python
summary_data = {
    'total_sessions': 3,
    'total_questions': 45,
    'incidents': 0
}

email_alert_system.send_daily_digest(
    parent_email="parent@example.com",
    parent_name="John Doe",
    summary_data=summary_data
)
```

#### 3. System Error Alert

Alert administrators about system errors:

```python
email_alert_system.send_error_alert(
    admin_email="admin@example.com",
    error_summary="DatabaseConnectionError: Connection timeout",
    error_count=15
)
```

### Integration with Incident Logger

Email alerts are **automatically sent** when safety incidents are logged:

```python
from safety.incident_logger import incident_logger

# Log incident - email automatically sent if configured
success, incident_id = incident_logger.log_incident(
    profile_id="profile_123",
    session_id="session_456",
    incident_type="violence",
    severity="critical",  # Triggers immediate email
    content_snippet="concerning content...",
    send_alert=True  # Default
)
```

**Email is sent when:**
- Severity is `'critical'` → Immediate email
- Severity is `'major'` → Email after 2nd incident
- Parent has email address in database

---

## 3. Load Testing

### Features

- ✅ Simulate 50+ concurrent users
- ✅ Realistic user behavior patterns
- ✅ Performance metrics (response time, throughput)
- ✅ Stress testing with gradual load increase
- ✅ Detailed results reporting

### Running Load Tests

**File:** `tests/load_testing.py`

#### Quick Test (50 users, 10 messages each)

```bash
python tests/load_testing.py
```

#### Full Stress Test (0-100 users)

```bash
python tests/load_testing.py stress
```

#### Programmatic Usage

```python
from tests.load_testing import LoadTester

tester = LoadTester()

# Standard load test
results = tester.run_load_test(
    num_users=50,
    messages_per_user=10,
    ramp_up_seconds=10,
    include_unsafe_content=True  # Test safety filters
)

# Print results
tester.print_results(results)

# Export to JSON
tester.export_results(results, 'load_test_results.json')
```

### Metrics Collected

- **Total Requests**: Number of operations performed
- **Success Rate**: Percentage of successful requests
- **Response Times:**
  - Average, Min, Max
  - P50 (median), P95, P99 percentiles
- **Throughput**: Requests per second
- **Error Count**: Failed operations
- **Concurrent Users**: Simulated users

### Sample Output

```
================================================================================
LOAD TEST RESULTS: Load Test - 50 users x 10 messages
================================================================================
Duration: 45.23s
Concurrent Users: 50
Total Requests: 500
Successful: 498 (99.6%)
Failed: 2 (0.4%)

Response Times (ms):
  Average: 12.45
  Min: 2.10
  Max: 156.30
  P50: 10.20
  P95: 24.50
  P99: 45.80

Throughput: 11.05 requests/second
================================================================================
```

### Performance Benchmarks

**Expected Performance (single server):**

| Concurrent Users | Avg Response Time | P95 Response Time | Throughput | Success Rate |
|-----------------|-------------------|-------------------|------------|--------------|
| 10              | < 10ms            | < 20ms            | > 50 rps   | > 99.5%      |
| 25              | < 15ms            | < 30ms            | > 100 rps  | > 99%        |
| 50              | < 20ms            | < 40ms            | > 150 rps  | > 98%        |
| 100             | < 30ms            | < 60ms            | > 200 rps  | > 95%        |

---

## 4. Production Monitoring Checklist

### Pre-Deployment

- [ ] Configure SMTP settings in environment variables
- [ ] Test email delivery with `test_connection()`
- [ ] Start email worker thread
- [ ] Enable error tracking decorators on critical functions
- [ ] Run load tests to establish baseline performance
- [ ] Configure alert thresholds

### Daily Operations

- [ ] Review error tracking dashboard
- [ ] Check email delivery queue status
- [ ] Monitor system resource usage
- [ ] Review safety incident alerts
- [ ] Check error resolution rate

### Weekly Reviews

- [ ] Analyze error trends
- [ ] Review most frequent errors
- [ ] Check email delivery success rate
- [ ] Run stress tests to verify performance
- [ ] Update alert thresholds as needed

---

## 5. Troubleshooting

### Email Alerts Not Sending

**Problem:** Emails are not being received

**Solutions:**
1. Check `ENABLE_EMAIL_ALERTS=true` is set
2. Verify SMTP credentials with `test_connection()`
3. Check email worker is running: `email_alert_system.start_worker()`
4. Review logs for SMTP errors
5. Check spam/junk folders
6. Verify parent email addresses in database

### High Error Rates

**Problem:** Error tracking shows many errors

**Solutions:**
1. Review error summary: `error_tracker.get_error_summary()`
2. Check most frequent errors
3. Look for patterns (same module, function)
4. Review stack traces for root cause
5. Mark resolved after fixing: `mark_resolved(error_id, notes)`

### Load Test Failures

**Problem:** Load tests show poor performance

**Solutions:**
1. Check database connection pool size
2. Review concurrent thread limits
3. Increase system resources (RAM, CPU)
4. Optimize database queries
5. Enable database WAL mode (already enabled)
6. Check for slow queries in logs

---

## 6. Integration Example

Complete production setup:

```python
# startup.py
import os
from utils.error_tracking import error_tracker, track_exceptions
from utils.email_alerts import email_alert_system
from utils.data_retention import data_retention_manager

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Start email alerts
if os.getenv('ENABLE_EMAIL_ALERTS', 'false').lower() == 'true':
    email_alert_system.start_worker()
    print("✅ Email alert system started")

# Start data retention scheduler
data_retention_manager.start_scheduler()
print("✅ Data retention scheduler started")

# Application code with error tracking
@track_exceptions(severity='critical')
def main_application_loop():
    try:
        # Your application code
        ...
    except Exception as e:
        error_tracker.capture_exception(e, severity='critical')
        raise

if __name__ == '__main__':
    main_application_loop()
```

---

## 7. API Reference

### Error Tracker

```python
from utils.error_tracking import error_tracker

# Capture exception
error_id = error_tracker.capture_exception(exception, severity, user_id, session_id, context)

# Capture custom error
error_id = error_tracker.capture_error(error_type, error_message, severity, ...)

# Get summary
summary = error_tracker.get_error_summary(days=7)

# Get error details
error = error_tracker.get_error_details(error_id)

# Mark resolved
error_tracker.mark_resolved(error_id, resolution_notes)

# Cleanup old errors
count = error_tracker.cleanup_old_errors(retention_days=90)
```

### Email Alerts

```python
from utils.email_alerts import email_alert_system

# Start worker
email_alert_system.start_worker()

# Send safety alert
email_alert_system.send_safety_alert(parent_email, child_name, incident_type, severity, incident_id)

# Send daily digest
email_alert_system.send_daily_digest(parent_email, parent_name, summary_data)

# Send error alert
email_alert_system.send_error_alert(admin_email, error_summary, error_count)

# Test connection
success = email_alert_system.test_connection()

# Stop worker
email_alert_system.stop_worker()
```

### Load Testing

```python
from tests.load_testing import LoadTester

tester = LoadTester()

# Run load test
results = tester.run_load_test(num_users, messages_per_user, ramp_up_seconds)

# Run stress test
all_results = tester.run_stress_test(max_users, step_size)

# Print results
tester.print_results(results)

# Export results
tester.export_results(results, filename)
```

---

## Summary

✅ **Error Tracking:** Comprehensive exception capture and aggregation
✅ **Email Alerts:** SMTP-based parent notifications with HTML templates
✅ **Load Testing:** 50+ concurrent user simulation with detailed metrics
✅ **Production Ready:** Battle-tested monitoring and alerting system

---

## References

- **Error Tracking:** `utils/error_tracking.py`
- **Email Alerts:** `utils/email_alerts.py`
- **Load Testing:** `tests/load_testing.py`
- **Database Schema:** `storage/database.py` (error_tracking table)
- **Incident Logger:** `safety/incident_logger.py` (email integration)

---

**Document Version:** 1.0
**Last Review:** 2025-12-21
**Next Review:** 2026-01-21
