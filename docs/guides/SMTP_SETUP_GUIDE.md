---
---

# SMTP Email Setup Guide
## Configuring Parent Safety Alert Notifications

**Last Updated:** 2025-12-21
**Required for:** COPPA compliance - Parents must be notified of safety incidents

---

## Overview

snflwr.ai sends automated email alerts to parents when safety incidents are detected. This guide explains how to configure SMTP email delivery using various email providers.

### Alert Types

1. **Critical Alerts** 🚨
   - Self-harm or suicide-related content
   - Child exploitation attempts
   - Immediate parent notification required

2. **High Priority Alerts** ⚠️
   - Multiple major safety incidents
   - Pattern of concerning behavior
   - Requires parent review

3. **Moderate Alerts** ℹ️
   - Informational safety notifications
   - Normal filtering activity
   - No immediate action required

---

## Quick Start (SendGrid - Recommended)

**Why SendGrid?**
- Free tier: 100 emails/day (sufficient for most families/small schools)
- Reliable delivery
- Easy setup
- Good reputation (avoids spam folders)

### SendGrid Setup

1. **Create Account**
   - Go to [https://signup.sendgrid.com/](https://signup.sendgrid.com/)
   - Sign up for free account
   - Verify your email address

2. **Create API Key**
   - Go to Settings → API Keys
   - Click "Create API Key"
   - Name: "snflwr.ai"
   - Permissions: "Full Access" (or "Mail Send" only)
   - Copy the API key (you'll only see it once!)

3. **Verify Sender Identity**
   - Go to Settings → Sender Authentication
   - Click "Verify a Single Sender"
   - Enter your email address (e.g., admin@yourschool.org)
   - Check your email and click verification link

4. **Configure snflwr.ai**
   ```bash
   # Set environment variables
   export SMTP_ENABLED=true
   export SMTP_HOST=smtp.sendgrid.net
   export SMTP_PORT=587
   export SMTP_USERNAME=apikey
   export SMTP_PASSWORD=SG.your_api_key_here
   export SMTP_FROM_EMAIL=your_verified_email@example.com
   export SMTP_FROM_NAME="snflwr.ai Safety Monitor"
   export SMTP_USE_TLS=true
   ```

5. **Test Email Delivery**
   ```bash
   python << 'EOF'
   from core.email_service import email_service

   # Test SMTP connection
   success, error = email_service.test_connection()

   if success:
       print("✅ SMTP connection successful!")
   else:
       print(f"❌ SMTP connection failed: {error}")
   EOF
   ```

---

## Gmail Setup (Alternative)

**Limitations:**
- Maximum 500 emails/day
- May require "Less Secure Apps" enabled
- Not recommended for production (use SendGrid instead)

### Gmail Configuration

1. **Enable 2-Factor Authentication**
   - Go to myaccount.google.com/security
   - Enable 2-Step Verification

2. **Generate App Password**
   - Go to myaccount.google.com/apppasswords
   - Select app: "Mail"
   - Select device: "Other (Custom name)"
   - Name: "snflwr.ai"
   - Click "Generate"
   - Copy the 16-character password

3. **Configure Environment**
   ```bash
   export SMTP_ENABLED=true
   export SMTP_HOST=smtp.gmail.com
   export SMTP_PORT=587
   export SMTP_USERNAME=your.email@gmail.com
   export SMTP_PASSWORD=your_16_char_app_password
   export SMTP_FROM_EMAIL=your.email@gmail.com
   export SMTP_FROM_NAME="snflwr.ai Safety Monitor"
   export SMTP_USE_TLS=true
   ```

---

## Microsoft 365 / Outlook Setup

### Outlook.com Configuration

1. **Enable App Passwords**
   - Go to account.microsoft.com/security
   - Click "Advanced security options"
   - Enable "App passwords"
   - Create new app password for "snflwr.ai"

2. **Configure Environment**
   ```bash
   export SMTP_ENABLED=true
   export SMTP_HOST=smtp-mail.outlook.com
   export SMTP_PORT=587
   export SMTP_USERNAME=your.email@outlook.com
   export SMTP_PASSWORD=your_app_password
   export SMTP_FROM_EMAIL=your.email@outlook.com
   export SMTP_FROM_NAME="snflwr.ai Safety Monitor"
   export SMTP_USE_TLS=true
   ```

### Microsoft 365 (School/Enterprise)

1. **Get SMTP Credentials from IT**
   - Contact your IT administrator
   - Request SMTP relay credentials
   - Typical host: smtp.office365.com:587

2. **Configure with IT-provided credentials**

---

## Custom SMTP Server

If you have your own SMTP server:

```bash
export SMTP_ENABLED=true
export SMTP_HOST=mail.yourserver.com
export SMTP_PORT=587  # or 465 for SSL, 25 for non-TLS
export SMTP_USERNAME=smtp_username
export SMTP_PASSWORD=smtp_password
export SMTP_FROM_EMAIL=noreply@yourserver.com
export SMTP_FROM_NAME="snflwr.ai Safety Monitor"
export SMTP_USE_TLS=true  # Set to false for port 25
```

---

## Environment Variable Configuration

### Option 1: .env File (Recommended)

Create `.env` file in project root:

```bash
# Email Configuration
SMTP_ENABLED=true
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=SG.your_api_key_here
SMTP_FROM_EMAIL=admin@example.com
SMTP_FROM_NAME=snflwr.ai Safety Monitor
SMTP_USE_TLS=true
```

### Option 2: System Environment Variables

**Linux/macOS:**
```bash
# Add to ~/.bashrc or ~/.bash_profile
export SMTP_ENABLED=true
export SMTP_HOST=smtp.sendgrid.net
export SMTP_PORT=587
export SMTP_USERNAME=apikey
export SMTP_PASSWORD=SG.your_api_key_here
export SMTP_FROM_EMAIL=admin@example.com
export SMTP_FROM_NAME="snflwr.ai Safety Monitor"
export SMTP_USE_TLS=true
```

**Windows:**
```cmd
# Add to system environment variables
setx SMTP_ENABLED true
setx SMTP_HOST smtp.sendgrid.net
setx SMTP_PORT 587
setx SMTP_USERNAME apikey
setx SMTP_PASSWORD SG.your_api_key_here
setx SMTP_FROM_EMAIL admin@example.com
setx SMTP_FROM_NAME "snflwr.ai Safety Monitor"
setx SMTP_USE_TLS true
```

---

## Testing Email Delivery

### Test 1: SMTP Connection

```python
from core.email_service import email_service

# Test connection
success, error = email_service.test_connection()
print(f"Connection: {'✅ Success' if success else '❌ Failed - ' + error}")
```

### Test 2: Send Test Alert

```python
from core.email_service import email_service

# Send test safety alert
success, error = email_service.send_safety_alert(
    parent_id="test_parent_id",
    child_name="Test Child",
    severity="high",
    incident_count=1,
    description="Test alert - ignore",
    snippet="This is a test message"
)

print(f"Email sent: {'✅ Success' if success else '❌ Failed - ' + error}")
```

---

## Parent Email Preferences

Parents can control email notifications through the parent dashboard:

### Enable/Disable Notifications

```sql
-- Enable email notifications for a parent
UPDATE users
SET email_notifications_enabled = 1
WHERE user_id = 'parent_id';

-- Disable email notifications for a parent
UPDATE users
SET email_notifications_enabled = 0
WHERE user_id = 'parent_id';
```

### API Endpoint (Future)

```http
POST /api/parents/preferences
Authorization: Bearer <parent_token>

{
  "email_notifications_enabled": true
}
```

---

## Email Templates

### Critical Alert Email

```
Subject: 🚨 URGENT: Safety Alert for [Child Name]

Dear [Parent Name],

This is an urgent safety alert regarding your child's use of snflwr.ai.

Alert Details:
• Child Profile: [Child Name]
• Severity: CRITICAL
• Incident Count: [Count]
• Time: [Timestamp]

Reason: [Description]

Conversation Excerpt:
[Snippet]

Recommended Action:
• Review your child's recent activity in the parent dashboard
• Have a conversation with your child about safe AI use
• Review and adjust safety settings if needed

[View Dashboard Button]

What happens next?
The snflwr.ai safety filter automatically blocks inappropriate content
and redirects your child to educational topics. No unsafe content reaches
your child. This alert is for your awareness and to help you support your
child's digital safety.
```

---

## Troubleshooting

### "SMTP connection failed"

**Check:**
1. SMTP credentials are correct
2. Firewall allows outbound connections on port 587
3. SMTP_HOST and SMTP_PORT are correct
4. SMTP_USE_TLS matches your provider's requirements

**Test manually:**
```bash
telnet smtp.sendgrid.net 587
# Should connect successfully
```

### "Authentication failed"

**Check:**
1. SMTP_USERNAME is correct
2. SMTP_PASSWORD is correct (for SendGrid, should start with "SG.")
3. For Gmail, ensure you're using App Password, not regular password
4. Account is not locked or suspended

### "Email not received"

**Check:**
1. Parent email address is correct in database
2. Check spam/junk folder
3. Verify sender email is verified with your provider
4. Check email service logs for delivery failures

**View audit log:**
```sql
SELECT *
FROM audit_log
WHERE event_type = 'email_notification'
ORDER BY timestamp DESC
LIMIT 10;
```

### "Rate limit exceeded"

**SendGrid Free Tier:** 100 emails/day
- Reduce alert frequency
- Upgrade to paid plan
- Batch daily summary instead of individual alerts

**Gmail:** 500 emails/day
- Switch to SendGrid
- Use multiple Gmail accounts
- Upgrade to Google Workspace

---

## Security Best Practices

### 1. Never Commit Credentials

❌ **Never do this:**
```python
SMTP_PASSWORD = "SG.actual_password_here"  # DON'T!
```

✅ **Always do this:**
```bash
# Use environment variables
export SMTP_PASSWORD=SG.actual_password_here
```

### 2. Use App Passwords

- Never use your main account password
- Always generate app-specific passwords
- Rotate passwords regularly

### 3. Monitor Email Logs

```sql
-- Check recent email attempts
SELECT
    timestamp,
    user_id,
    action,
    details,
    success
FROM audit_log
WHERE event_type = 'email_notification'
AND timestamp > datetime('now', '-7 days')
ORDER BY timestamp DESC;
```

### 4. Limit Email Frequency

Parents can receive many alerts. Consider:
- Daily digest emails instead of individual alerts
- Severity-based filtering (only send critical alerts)
- Time-based throttling (max 1 email per hour)

---

## Production Checklist

Before going live:

- [ ] SMTP provider account created (SendGrid recommended)
- [ ] API key or app password generated
- [ ] Sender email address verified
- [ ] Environment variables configured
- [ ] SMTP connection tested successfully
- [ ] Test email sent and received
- [ ] Email templates reviewed and customized
- [ ] Parent email preferences configured
- [ ] Audit logging verified
- [ ] Spam folder checked (ensure delivery)
- [ ] Email rate limits understood
- [ ] Backup email provider configured (optional)

---

## Email Audit Trail

All email sends are logged to the `audit_log` table for compliance:

```sql
SELECT
    timestamp,
    user_id as parent_id,
    details,
    success
FROM audit_log
WHERE event_type = 'email_notification'
ORDER BY timestamp DESC;
```

**Logged Information:**
- Timestamp of email attempt
- Parent ID (user_id)
- Email address (partial, for privacy)
- Status (sent/failed/skipped/not_sent)
- Error message (if failed)

---

## FAQ

**Q: How many emails will parents receive?**
A: Depends on child activity and safety incidents. Typically 0-2 per week for normal usage. Critical incidents trigger immediate emails.

**Q: Can parents opt out of emails?**
A: Yes, parents can disable email notifications in their dashboard. However, we recommend keeping them enabled for safety.

**Q: What if SMTP is not configured?**
A: Alerts are still created and visible in the parent dashboard. No emails are sent. This is logged in the audit trail.

**Q: Can I customize email templates?**
A: Yes! Edit `core/email_service.py` → `EmailTemplate` class to customize subject lines and HTML content.

**Q: How do I test without spamming parents?**
A: Set `SMTP_ENABLED=false` during development. Emails are logged but not sent.

**Q: What's the best email provider?**
A: SendGrid (free tier) for most deployments. Reliable, free, and easy to set up.

---

## Support

**Documentation:**
- Email Service Code: `core/email_service.py`
- Safety Monitor Integration: `safety/safety_monitor.py`
- Configuration: `config.py` (lines 90-98)

**Logs:**
```bash
# View email-related logs
tail -f logs/snflwr_ai.log | grep -i email
```

**Get Help:**
- Check audit_log table for email attempts
- Test SMTP connection manually
- Verify environment variables are loaded
- Check email provider status pages

---

## Next Steps

After SMTP setup:

1. **Test with real parent account**
   - Create parent account
   - Trigger safety incident
   - Verify email received

2. **Monitor delivery rates**
   - Check audit logs daily
   - Ensure high delivery success rate
   - Fix any recurring failures

3. **Customize templates**
   - Add school logo
   - Update contact information
   - Adjust tone/messaging

4. **Set up monitoring**
   - Alert on email failures
   - Track delivery rates
   - Monitor rate limits

Email notifications are now configured!
