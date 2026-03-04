# Privacy Policy

**Effective Date:** January 1, 2026
**Last Updated:** December 25, 2025

## Introduction

snflwr.ai ("we," "us," or "our") operates a K-12 Safe AI Learning Platform. We are committed to protecting the privacy of children and complying with the Children's Online Privacy Protection Act (COPPA) and the Family Educational Rights and Privacy Act (FERPA).

**IMPORTANT FOR PARENTS:**
This service is designed for children under 13 and requires verifiable parental consent before collecting any personal information from children.

---

## 1. Information We Collect

### 1.1 Information from Parents

**Account Information:**
- Email address (encrypted at rest)
- Name
- Password (hashed with Argon2)
- Account creation date

**Purpose:** Authenticate parents and send safety alerts.

### 1.2 Information About Children (with Parental Consent)

**Child Profile Information:**
- First name only (no last names)
- Age and grade level
- Assigned tier level (Budget/Standard/Premium)
- Profile creation date

**Conversation Data:**
- Chat messages with AI
- Timestamps
- Model used
- Safety incident flags

**Safety Monitoring Data:**
- Content that triggered safety filters
- Severity levels of incidents
- Parent notification records

### 1.3 Automatically Collected Information

**Technical Information (Non-PII):**
- Session duration
- Message counts
- Usage statistics (aggregated)
- Error logs (anonymized)
- System performance metrics

**We DO NOT collect:**
- ❌ IP addresses linked to children
- ❌ Location data
- ❌ Device identifiers linked to children
- ❌ Browsing history
- ❌ Social media data
- ❌ Biometric data

---

## 2. How We Use Information

### 2.1 Primary Uses

**Safety Monitoring:**
- Detect inappropriate content
- Prevent exposure to harmful material
- Alert parents of concerning patterns
- Maintain audit logs (COPPA requirement)

**Service Delivery:**
- Provide AI tutoring appropriate for child's age/grade
- Track learning progress
- Manage conversation sessions

**Parent Communication:**
- Send safety alerts
- Provide usage reports
- Account management notifications

### 2.2 We DO NOT:
- ❌ Sell or rent children's personal information
- ❌ Share data with third-party advertisers
- ❌ Use data for behavioral advertising
- ❌ Create behavioral profiles for marketing
- ❌ Share data with other parents or users

---

## 3. Legal Basis for Processing (COPPA Compliance)

### 3.1 Verifiable Parental Consent

**Required Before Collection:**
- Parents must register and verify email
- Parents must explicitly consent to child profile creation
- Parents receive detailed notice of data collection
- Parents can revoke consent at any time

**Consent Method:**
- Email verification with unique token
- Explicit checkbox agreement
- Confirmation email sent

**Withdrawal of Consent:**
- Contact privacy@snflwr.ai
- Data deleted within 30 days
- Parent retains right to export data first

### 3.2 FERPA Compliance (for Schools)

**Educational Records Protection:**
- Child data classified as educational records
- Access limited to authorized personnel
- Audit trail of all access
- Annual notification of rights
- No disclosure without consent (except as permitted by FERPA)

---

## 4. Data Storage and Security

### 4.1 Security Measures

**Encryption:**
- Email addresses: Fernet symmetric encryption at rest
- Passwords: Argon2 memory-hard hashing
- Data in transit: TLS 1.3 encryption (HTTPS)
- Database: Encrypted backups

**Access Controls:**
- Role-based access control (RBAC)
- JWT authentication (24-hour expiration)
- Multi-factor authentication available
- Regular security audits

**Infrastructure:**
- Local-first architecture (no cloud dependencies)
- Self-hosted or private cloud deployment
- Regular security patches
- Automated backup with 30-day retention

### 4.2 Data Storage Location

**Primary Storage:**
- United States (or customer's chosen region)
- Database: PostgreSQL or SQLite (encrypted)
- Backups: Encrypted and access-controlled

**No International Transfers:**
- All AI inference runs locally (Ollama)
- No data sent to cloud AI services
- No third-party analytics services

---

## 5. Data Retention

### 5.1 Retention Periods

| Data Type | Retention Period | Justification |
|-----------|------------------|---------------|
| Child conversation data | 180 days | Educational records, parent access |
| Safety incidents | 90 days (resolved) | COPPA compliance, safety monitoring |
| Audit logs | 365 days | Security and compliance review |
| Parent account data | Until account deletion | Account management |
| Aggregated analytics | 730 days | Non-identifiable, service improvement |

### 5.2 Automatic Deletion

**Data Cleanup Schedule:**
- Runs daily at 2 AM system time
- Deletes data past retention period
- Logs deletion actions for audit
- Parents notified before deletion (if email enabled)

**Manual Deletion:**
- Parents can delete data anytime
- 30-day grace period for recovery
- Permanent deletion after grace period

---

## 6. Parental Rights (COPPA)

### 6.1 Right to Access

**Parents can:**
- View all child profile information
- Access full conversation history
- Review all safety incidents
- Export data in machine-readable format (JSON)

**How to Access:**
- Login to parent dashboard
- Navigate to child's profile
- Click "Export Data" button
- Receive JSON file via secure download

### 6.2 Right to Modify

**Parents can:**
- Update child's name, age, grade
- Change tier level
- Modify parental control settings
- Correct inaccurate information

### 6.3 Right to Delete

**Parents can:**
- Delete individual child profiles
- Delete all data associated with profile
- Delete entire parent account
- Request permanent data deletion

**Deletion Process:**
1. Login → Settings → Delete Profile/Account
2. Confirm deletion (enter password)
3. 30-day grace period (data retained for recovery)
4. Permanent deletion after 30 days
5. Confirmation email sent

### 6.4 Right to Refuse Further Collection

**Parents can:**
- Disable data collection (deactivates profile)
- Revoke consent (deletes data)
- Opt-out of email notifications
- Limit specific data collection

---

## 7. Third-Party Services

### 7.1 Services We Use

**Email Delivery (SMTP):**
- Provider: SendGrid or customer's SMTP
- Purpose: Safety alerts, account notifications
- Data shared: Parent email address, notification content
- Privacy policy: https://sendgrid.com/policies/privacy/

**No Other Third Parties:**
- ✅ All AI processing is local (Ollama)
- ✅ No analytics services (Google Analytics, etc.)
- ✅ No advertising networks
- ✅ No social media integrations
- ✅ No payment processors (if service is free/paid via institution)

### 7.2 Service Providers

**Hosting (Optional):**
- If cloud-hosted: AWS, Azure, or customer's choice
- Data Processing Agreement required
- COPPA/FERPA compliance mandated
- Regular security audits

---

## 8. Cookies and Tracking

### 8.1 Cookies We Use

**Essential Cookies Only:**
- Session cookies: Authenticate users, expire on logout
- Security cookies: Prevent CSRF attacks

**We DO NOT use:**
- ❌ Advertising cookies
- ❌ Analytics cookies
- ❌ Social media cookies
- ❌ Third-party tracking cookies

### 8.2 Cookie Management

Parents can:
- Clear cookies via browser settings
- Use private/incognito mode
- Disable cookies (may affect functionality)

---

## 9. Children's Privacy Rights

### 9.1 Age Verification

**Minimum Age:** 5 years (Kindergarten)
**Maximum Age:** 18 years (12th grade)

**Protection for Children Under 13:**
- COPPA compliance mandatory
- Parental consent required
- No direct marketing to children
- Parent controls enforced at backend
- Cannot bypass safety monitoring

### 9.2 Student Privacy Pledge

We commit to:
- ✅ Not sell student information
- ✅ Not use data for behavioral advertising
- ✅ Not build personal profiles for non-educational purposes
- ✅ Not disclose student information without authorization
- ✅ Maintain comprehensive security program
- ✅ Delete student data upon request

Signed pledge: https://studentprivacypledge.org

---

## 10. Data Breach Notification

### 10.1 In the Event of a Breach

**Immediate Actions:**
- Contain breach within 24 hours
- Investigate scope and impact
- Notify affected parties

**Notification Timeline:**
- Parents: Within 72 hours of discovery
- Schools (if applicable): Within 24 hours
- Regulators: As required by law (COPPA, GDPR, state laws)

**Notification Contents:**
- Description of breach
- Types of data affected
- Actions taken to mitigate
- Steps parents should take
- Contact information for questions

---

## 11. Your Privacy Choices

### 11.1 Communication Preferences

**Parents can:**
- Opt-in/opt-out of safety alerts (email)
- Choose daily digest vs. real-time alerts
- Disable all non-critical emails
- Update email address anytime

### 11.2 Data Sharing Preferences

**Parents control:**
- Who can access child's data (default: parent only)
- Whether to share aggregated data for research (opt-in only)
- Whether to participate in product improvements

**Default Settings:**
- ✅ Maximum privacy
- ✅ Minimal data collection
- ✅ No sharing with third parties
- ✅ All safety monitoring enabled

---

## 12. Changes to Privacy Policy

### 12.1 Notification of Changes

**Material Changes:**
- Email notification to all parents (30 days advance notice)
- Prominent notice on website
- Option to review changes before acceptance
- Opportunity to delete account if disagreed

**Non-Material Changes:**
- Posted to website
- Effective date updated
- No action required

### 12.2 Parent Consent for Changes

For changes affecting children under 13:
- Requires new parental consent
- Existing data use unchanged until consent
- Parents can withdraw consent and delete data

---

## 13. International Users

**Primary Jurisdiction:** United States

**EU/UK Users (GDPR):**
- All GDPR rights apply (access, rectification, erasure, portability)
- Lawful basis: Parental consent
- Data Protection Officer: dpo@snflwr.ai
- Supervisory authority: Contact for complaints

**Other Jurisdictions:**
- Comply with local data protection laws
- Additional rights may apply
- Contact privacy team for details

---

## 14. Contact Information

### 14.1 Privacy Questions

**Email:** privacy@snflwr.ai
**Response Time:** Within 5 business days

**Mail:**
snflwr.ai Privacy Team
[Company Address]
[City, State, ZIP]

### 14.2 COPPA Compliance Officer

**Email:** coppa@snflwr.ai
**Phone:** [Phone Number]

### 14.3 Data Protection Officer (GDPR)

**Email:** dpo@snflwr.ai

---

## 15. Additional Resources

**Full Terms of Service:** [Link]
**Data Processing Agreement:** [Link] (for schools)
**COPPA Compliance Details:** [Link]
**Security White Paper:** [Link]
**Parent Guide:** [Link]

---

## Acknowledgments

This Privacy Policy complies with:
- Children's Online Privacy Protection Act (COPPA)
- Family Educational Rights and Privacy Act (FERPA)
- General Data Protection Regulation (GDPR)
- California Consumer Privacy Act (CCPA)
- Student Privacy Pledge

**Last Reviewed:** December 25, 2025
**Next Review:** March 25, 2026 (quarterly)

---

**© 2025 snflwr.ai. All rights reserved.**
