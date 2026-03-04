# SendGrid SMTP Setup Guide

## Quick Setup (3 Minutes)

### Step 1: Get SendGrid API Key

1. **Sign up**: Go to https://sendgrid.com/free
   - Free tier: 100 emails/day (plenty for testing)
   - Upgrade later if needed

2. **Create API Key**:
   ```
   Dashboard → Settings → API Keys → Create API Key

   Name: snflwr.ai Production
   Permissions: Full Access (or "Mail Send" for security)

   Click "Create & View"
   Copy the key: SG.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

   ⚠️ **IMPORTANT**: Copy this key NOW! You won't see it again!

3. **Verify Sender Email** (Required by SendGrid):
   ```
   Settings → Sender Authentication → Single Sender Verification

   Email: alerts@yourschool.org (or your domain)
   From Name: snflwr.ai Safety Team

   Check your email and verify
   ```

### Step 2: Configure snflwr.ai

#### Option A: Environment Variables (Recommended)

Create `.env` file in project root:

```bash
# .env
SMTP_ENABLED=true
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=SG.your-actual-sendgrid-api-key-here
SMTP_FROM_EMAIL=alerts@yourschool.org
SMTP_FROM_NAME=snflwr.ai Safety Team
```

#### Option B: System Environment Variables

```bash
# Linux/Mac
export SMTP_ENABLED=true
export SMTP_HOST=smtp.sendgrid.net
export SMTP_PORT=587
export SMTP_USERNAME=apikey
export SMTP_PASSWORD=SG.your-actual-sendgrid-api-key-here
export SMTP_FROM_EMAIL=alerts@yourschool.org
export SMTP_FROM_NAME="snflwr.ai Safety Team"

# Windows PowerShell
$env:SMTP_ENABLED="true"
$env:SMTP_HOST="smtp.sendgrid.net"
$env:SMTP_PORT="587"
$env:SMTP_USERNAME="apikey"
$env:SMTP_PASSWORD="SG.your-actual-sendgrid-api-key-here"
$env:SMTP_FROM_EMAIL="alerts@yourschool.org"
$env:SMTP_FROM_NAME="snflwr.ai Safety Team"
```

### Step 3: Test Email Delivery

```bash
# Test script
python -c "
from utils.email_alerts import get_email_system
email_system = get_email_system()

# Send test email to yourself
email_system.send_test_email('your-email@example.com')
print('✓ Test email sent! Check your inbox.')
"
```

Expected output:
```
✓ Test email sent! Check your inbox.
```

Check your email - you should receive:
```
FROM: snflwr.ai Safety Team <alerts@yourschool.org>
TO: your-email@example.com
SUBJECT: Test Email from snflwr.ai

This is a test email from snflwr.ai.
If you received this, your SMTP configuration is working correctly!
```

---

## SendGrid Configuration Details

### For Production Use:

```bash
# SendGrid SMTP Settings (Standard)
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587                    # TLS recommended
SMTP_USERNAME=apikey             # Always "apikey" for SendGrid
SMTP_PASSWORD=SG.xxxxx...        # Your actual API key
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

### Scaling Information:

| Plan | Emails/Day | Emails/Month | Cost |
|------|------------|--------------|------|
| Free | 100 | 3,000 | $0 |
| Essentials | 40,000 | 1.2M | $19.95/mo |
| Pro | 100,000 | 3M | $89.95/mo |

**Calculation for your needs:**
- Average school: 100 parents
- Safety incidents: ~5/day (worst case)
- Daily digests: 100/day
- **Total: ~105 emails/day** → Free tier works!

### Email Templates Used:

1. **Safety Incident Alert** (Critical/Major)
   - Sent immediately when incident detected
   - HTML + Plain text versions
   - Parent action required

2. **Daily Digest** (Optional)
   - Summary of day's activity
   - Sent at 6 PM daily
   - All severity levels

3. **Weekly Report** (Optional)
   - Learning progress summary
   - Safety overview
   - Sent Sundays

---

## Monitoring & Analytics

### SendGrid Dashboard

View in SendGrid:
- Email delivery rates
- Bounce tracking
- Click/open rates
- Spam reports

Access: https://app.sendgrid.com → Statistics

### snflwr.ai Logging

Check logs for email activity:
```bash
# View email logs
tail -f logs/snflwr.log | grep "email"

# Successful sends
grep "Email alert queued" logs/snflwr.log

# Failed sends
grep "Email send failed" logs/snflwr.log
```

---

## Troubleshooting

### Issue 1: "Authentication failed"
**Cause**: Wrong API key or username
**Fix**:
```bash
# Verify username is EXACTLY "apikey"
echo $SMTP_USERNAME  # Should show: apikey

# Verify password starts with SG.
echo $SMTP_PASSWORD | head -c 5  # Should show: SG.xx
```

### Issue 2: "Sender address rejected"
**Cause**: Email not verified in SendGrid
**Fix**:
1. Go to SendGrid → Sender Authentication
2. Verify your sender email
3. Check verification email

### Issue 3: "Connection timed out"
**Cause**: Firewall blocking port 587
**Fix**:
```bash
# Test SendGrid connectivity
nc -zv smtp.sendgrid.net 587

# If blocked, try port 465 (SSL)
export SMTP_PORT=465
export SMTP_USE_SSL=true
export SMTP_USE_TLS=false
```

### Issue 4: Emails going to spam
**Fix**:
1. **SendGrid**: Complete domain authentication (SPF/DKIM)
2. **Content**: Avoid spam trigger words
3. **Reputation**: Start with low volume, ramp up slowly

---

## Security Best Practices

### 1. Protect API Key
```bash
# ❌ NEVER commit .env to git
echo ".env" >> .gitignore

# ✅ Use environment variables
# ✅ Rotate keys quarterly
# ✅ Use different keys for dev/staging/prod
```

### 2. Rate Limiting
```bash
# Prevent abuse - limit to 10 emails/hour per incident
INCIDENT_EMAIL_COOLDOWN_MINUTES=30
```

### 3. Email Validation
```bash
# Verify parent emails before sending
# Already implemented in utils/email_alerts.py:
# - Email format validation
# - Domain verification
# - Bounce handling
```

---

## Advanced Configuration

### Custom Domain (Optional)

Instead of `alerts@yourschool.org`, use `noreply@alerts.yourschool.org`:

1. **Add DNS Records** (in your domain registrar):
   ```
   # SendGrid provides these
   SPF:  v=spf1 include:sendgrid.net ~all
   DKIM: [SendGrid-provided key]
   CNAME: em1234.alerts.yourschool.org → sendgrid.net
   ```

2. **Update Config**:
   ```bash
   SMTP_FROM_EMAIL=noreply@alerts.yourschool.org
   ```

3. **Verify in SendGrid**:
   ```
   Settings → Sender Authentication → Domain Authentication
   ```

**Benefits**:
- Professional branding
- Higher deliverability
- Better spam scores

---

## Production Checklist

Before going live:

- [ ] SendGrid account created
- [ ] API key generated and secured
- [ ] Sender email verified
- [ ] Environment variables set
- [ ] Test email sent successfully
- [ ] Email templates reviewed
- [ ] Spam score checked
- [ ] Monitoring dashboard configured
- [ ] Backup SMTP configured (optional)

---

## Support

**SendGrid Support**:
- Docs: https://docs.sendgrid.com
- Email: support@sendgrid.com
- Status: https://status.sendgrid.com

**snflwr.ai**:
- Email Alerts Code: `utils/email_alerts.py`
- Configuration: `.env` file
- Logs: `logs/snflwr.log`

---

**Next Steps**: Once configured, test the complete flow:
```bash
# Run the comprehensive email test
python tests/test_encrypted_emails.py
```
