# Data Processing Agreement (DPA)
## For Educational Institutions Using snflwr.ai

> ⚠️ **DRAFT — NOT IN EFFECT.** This DPA contains operator placeholders (governing-law state, subprocessor list) and overstates the operator's current organizational structure (no employees yet). It must not be signed in its current form. The operator's business entity is not yet registered. See `legal/LAWYER_REVIEW_CHECKLIST.md` for the full pre-publication blocker list.

**Effective Date:** *Not in effect — pending entity registration*
**Last Updated:** June 16, 2026

This Data Processing Agreement ("DPA") is entered into between:
- **"School"** (educational institution) and
- **"snflwr.ai"** (service provider)

---

## 1. Purpose and Scope

### 1.1 Purpose

This DPA governs the processing of Student Data by snflwr.ai on behalf of School, ensuring compliance with:
- Family Educational Rights and Privacy Act (FERPA)
- Children's Online Privacy Protection Act (COPPA)
- State student privacy laws
- General Data Protection Regulation (GDPR), if applicable

### 1.2 Definitions

**Student Data:** Any information directly related to a student that is maintained by the School or snflwr.ai, including:
- Student names, ages, grades
- Educational records
- Chat conversations
- Safety incident logs

**Educational Records:** Records directly related to a student maintained by an educational institution (FERPA definition).

**Personal Data:** Any information relating to an identified or identifiable individual (GDPR definition).

---

## 2. Roles and Responsibilities

### 2.1 School as Data Controller

School:
- Determines purposes and means of processing Student Data
- Ensures legal basis for data collection
- Obtains necessary consents from parents
- Provides notice to parents per FERPA/COPPA
- Instructs snflwr.ai on data processing

### 2.2 snflwr.ai as Data Processor

snflwr.ai:
- Processes Student Data only per School's instructions
- Implements appropriate security measures
- Assists with data subject requests
- Maintains confidentiality
- Returns or deletes data upon termination

---

## 3. Data Processing

### 3.1 Authorized Processing

**Purpose:**
- Provide AI tutoring services to students
- Monitor safety and prevent inappropriate content
- Generate usage reports for School
- Comply with legal obligations

**Prohibited Uses:**
- ❌ Commercial advertising or marketing
- ❌ Behavioral profiling for non-educational purposes
- ❌ Selling or renting Student Data
- ❌ Sharing with unauthorized third parties

### 3.2 Data Categories

| Category | Examples | Retention |
|----------|----------|-----------|
| Student Profile | First name, age, grade | Until account deletion or cascade-delete on consent revocation (see §7.1) |
| Educational Content | Chat messages, questions | 180 days rolling, measured from message creation |
| Safety Data | Incidents, alerts | 90 days (resolved) |
| Usage Analytics | Session duration, message count | 730 days (aggregated, non-identifiable) |

The 180-day rolling window for Educational Content runs continuously during enrollment — individual messages roll off after 180 days regardless of whether the student is still enrolled. School may extend or shorten this window via a written amendment to this DPA; the school's setting controls if it conflicts with Privacy Policy §5.1.

### 3.3 Sub-Processors

**Authorized Sub-Processors:**

| Sub-Processor | Service | Location | Purpose |
|---------------|---------|----------|---------|
| [Email Provider] | Email delivery | USA | Parent/teacher notifications |
| [Hosting Provider] | Infrastructure | USA | Application hosting |

**Notification of Changes:**
- 30-day advance notice of new sub-processors
- School may object within 30 days
- Alternative solution or termination if objection

---

## 4. Data Security

### 4.1 Security Measures

**Technical Safeguards:**
- Encryption at rest: Fernet for PII columns; SQLCipher with operator-passphrase-wrapped master key on the SQLite tier
- Encryption in transit (TLS 1.3)
- Password hashing (Argon2)
- Access controls (RBAC, JWT authentication, 24-hour token expiration)
- Dependency hash-pinning in production Docker builds
- Automated backups (encrypted); tested restore path in CI (`tests/test_dr_restore_end_to_end.py`)

**Organizational Safeguards:**
- The operator is currently a solo founder with no employees. Staff-related controls (security training, background checks, role separation) will be implemented before the first employee with student-data access is hired, and this DPA will be amended to reflect them. Schools that require operational-staff controls as a precondition should defer signing until that amendment is in place.
- Documented incident response procedures (`docs/deployment/INCIDENT_RESPONSE_RUNBOOK.md`)
- Documented disaster recovery procedures with tested restore (`docs/guides/DR_RUNBOOK.md`)
- Data minimization practices (first name only, no IP linkage to children, no location data)

### 4.2 Access Control

**Who Has Access:**
- School administrators (read/write)
- Designated school staff (read only)
- snflwr.ai support (limited, logged)
- Parents (own child's data only)

**Access Logging:**
- All access logged with timestamp, user, action
- Logs retained for 365 days
- Available for School audit

---

## 5. Data Subject Rights (FERPA/GDPR)

### 5.1 Student/Parent Rights

**Right to Access:**
- School and parents can access all Student Data
- Response time: Within 45 days (FERPA), 30 days (GDPR)
- Format: JSON export or parent dashboard

**Right to Rectification:**
- Correct inaccurate data
- Complete incomplete data
- Response time: Within 30 days

**Right to Erasure:**
- Delete Student Data upon request
- 30-day grace period for recovery
- Permanent deletion thereafter

**Right to Data Portability:**
- Export in machine-readable format (JSON)
- Includes all Student Data

### 5.2 snflwr.ai Assistance

snflwr.ai will:
- Provide tools for School to exercise rights
- Respond to requests within contracted timeframes
- Notify School of direct requests from parents
- Cooperate with School's compliance efforts

---

## 6. Data Breach Notification

### 6.1 Notification Requirements

**To School:**
- Within 24 hours of discovery
- Details: What happened, data affected, mitigation steps
- Contact: security@snflwr.ai

**To Parents:**
- School determines notification (or delegates to snflwr.ai)
- Within 72 hours (GDPR requirement)
- Clear, plain language explanation

**To Regulators:**
- snflwr.ai assists School with notifications
- Within timeframes required by law

### 6.2 Breach Response

snflwr.ai will:
- Immediately investigate and contain breach
- Preserve evidence for forensic analysis
- Provide detailed incident report
- Implement corrective measures
- Cooperate with School's breach response

---

## 7. Data Retention and Deletion

### 7.1 Retention Schedule

| Data Type | Retention Period | Deletion Method |
|-----------|------------------|-----------------|
| Active student data | While student enrolled | - |
| Graduated/transferred | Per School policy (default: 180 days) | Secure deletion |
| Audit logs | 365 days | Secure deletion |
| Safety incidents | 90 days (resolved) | Secure deletion |

### 7.2 Deletion Methods

**Secure Deletion:**
- Database record deletion (PostgreSQL DELETE)
- Backup overwrite within 30 days
- Verification of deletion
- Certificate of deletion provided upon request

---

## 8. Audit Rights

### 8.1 School Audit Rights

School may:
- Request annual compliance reports
- Conduct audits (with 30-day notice)
- Review security documentation
- Interview snflwr.ai staff
- Inspect physical facilities

### 8.2 Third-Party Audits

**Annual Security Audit:**
- Independent security assessment commitment is **deferred until first paid customer**. Operator commits to commissioning the first audit within 90 days of the first signed Enterprise-tier customer agreement.

**Compliance Certifications:**
- **SOC 2 Type II — not currently held.** Operator does not commit to a fixed delivery date in this DPA. Schools that require an active SOC 2 report as a precondition should defer signing.
- **ISO 27001 — not currently held**, same caveat as SOC 2.
- **COPPA/FERPA compliance attestation** — operator self-attests; see `docs/compliance/FERPA_COPPA_COMPLIANCE_AUDIT.md` for the self-audit detail.

**Compensating controls in lieu of certification:**
- Enhanced school audit rights per §8.1 (no notice cap; school may bring its own assessor at any time during the term).
- Quarterly disaster-recovery drill results delivered to school on request.
- Source code is public (AGPL-3.0); school's own security team may audit it directly.

---

## 9. International Data Transfers

### 9.1 Data Localization

**Default:** All data stored in United States

**EU/UK Schools (GDPR):**
- Standard Contractual Clauses (SCCs) apply
- Transfer Impact Assessment available
- Data Processing Addendum (EU version) available

### 9.2 Transfer Safeguards

If international transfers necessary:
- Adequate level of protection ensured
- Approved transfer mechanisms used (SCCs, etc.)
- School notified and consents to transfer

---

## 10. Term and Termination

### 10.1 Term

- Effective from execution date
- Continues for duration of Service Agreement
- Survives termination where necessary for compliance

### 10.2 Termination

**Upon Termination of Service Agreement:**
- Within 30 days: Return or delete all Student Data
- School chooses: Return or certify deletion
- Exceptions: Legal hold, required retention

**Data Return Options:**
1. Secure download (JSON export)
2. Database backup file
3. Certified deletion (no return)

---

## 11. Liability and Indemnification

### 11.1 snflwr.ai Liability

**Limited to:**
- Fees paid in last 12 months
- Or $50,000, whichever is greater

**Exceptions (Unlimited):**
- Data breaches caused by our negligence
- Violation of confidentiality obligations
- Gross negligence or willful misconduct

### 11.2 Indemnification

snflwr.ai indemnifies School from:
- Our violation of this DPA
- Our failure to comply with FERPA/COPPA
- Our negligent data handling
- Our sub-processor violations

---

## 12. Amendments

### 12.1 Changes to DPA

**Material Changes:**
- 60-day advance notice to School
- School may terminate if disagrees
- New DPA version required for approval

**Legal/Regulatory Changes:**
- Immediate amendment if required by law
- Notice to School as soon as practicable

---

## 13. Governing Law and Jurisdiction

**Governing Law:** Laws of *[operator's state of residence — pending entity registration]*, United States
**Jurisdiction:** Courts of *[operator's county and state of residence — pending entity registration]*
**FERPA Compliance:** U.S. federal law applies

---

## 14. Signatures

**School:**

Name: _______________________________
Title: _______________________________
Signature: ___________________________
Date: ________________________________

**snflwr.ai:**

Name: _______________________________
Title: _______________________________
Signature: ___________________________
Date: ________________________________

---

## Appendices

### Appendix A: List of Sub-Processors
[Maintained at: https://snflwr.ai/subprocessors]

### Appendix B: Security Measures
[Detailed in Security White Paper]

### Appendix C: Standard Contractual Clauses (EU/UK)
[If applicable]

---

**DPA Version:** 1.1 — draft
**Last Updated:** June 16, 2026
**Next Review:** September 16, 2026

---

**© 2026 snflwr.ai. All rights reserved.**
