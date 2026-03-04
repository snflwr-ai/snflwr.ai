# COPPA Parental Consent Mechanism
## snflwr.ai K-12 Safety Platform

**Date:** 2025-12-21
**Status:** COMPLIANT ✅
**Regulatory Framework:** COPPA §312.5 (Verifiable Parental Consent)

---

## Overview

snflwr.ai implements a **multi-layered parental consent mechanism** that satisfies COPPA requirements for verifiable parental consent before collecting, using, or disclosing children's personal information.

---

## Consent Mechanism Components

### 1. Email Verification (Primary Consent Layer)

**Implementation:** All parent accounts must verify their email address before creating child profiles.

**Flow:**
1. Parent registers with email address
2. System sends verification email to parent's address
3. Parent clicks verification link (24-hour expiration)
4. Email verified → `users.email_verified = 1`
5. **Login enforcement:** Cannot log in until email verified (`authentication.py:308`)

**COPPA Compliance:**
- ✅ Email verification constitutes "email plus" method under COPPA
- ✅ Verifies parent has access to email account
- ✅ Creates audit trail of parent identity
- ✅ Prevents anonymous profile creation

**Code Location:** `core/authentication.py:524-576` (email verification)

---

### 2. Age-Gated Profile Creation (Secondary Consent Layer)

**Implementation:** Only verified parents can create child profiles, and only for ages 5-18 (K-12).

**Enforcements:**
1. **Email verification required:** Cannot log in without verified email
2. **Parent role required:** Only users with role='parent' can create profiles
3. **Age restriction:** Children must be 5-18 (enforces `MINIMUM_AGE` from config)
4. **No self-registration:** Children cannot create their own accounts

**COPPA Compliance:**
- ✅ Prevents children from creating accounts
- ✅ Parent must take affirmative action (create profile)
- ✅ Age-appropriate for target audience (K-12)
- ✅ Documented consent through parent action

**Code Locations:**
- `core/authentication.py:308` - Email verification check at login
- `core/profile_manager.py:141-145` - Age validation
- `api/routes/profiles.py` - Parent authentication required

---

### 3. Audit Trail (Compliance Documentation)

**Implementation:** All consent-related actions are logged in the audit trail.

**Logged Events:**
1. Parent registration (`event_type: registration`)
2. Email verification (`event_type: email_verification`)
3. Child profile creation (`event_type: profile_creation`)
4. Profile modifications (`event_type: profile_update`)

**COPPA Compliance:**
- ✅ Maintains record of parental consent
- ✅ Timestamps for regulatory audits
- ✅ IP address tracking (where applicable)
- ✅ User agent tracking

**Code Location:** `database/schema.sql` - `audit_log` table

---

## Legal Basis

### COPPA §312.5 Requirements

**Requirement:** "Verifiable parental consent means any reasonable effort (taking into consideration available technology), including a request for authorization for future collection, use, and disclosure described in the notice, to ensure that a parent of a child receives notice of the operator's personal information collection, use, and disclosure practices, and authorizes the collection, use, and disclosure, as applicable, of personal information and the subsequent use of that information before that information is collected from that child."

**snflwr.ai Implementation:**

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| Notice to parent | Email verification email | ✅ Implemented |
| Parent authorization | Verified email + profile creation action | ✅ Implemented |
| Reasonable effort | Email verification (industry standard) | ✅ Implemented |
| Before collection | Profile cannot be created until email verified | ✅ Implemented |
| Audit trail | All actions logged in audit_log table | ✅ Implemented |

---

## Consent Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│ 1. Parent Registration                                   │
│    - Provides email address                              │
│    - Creates account (email_verified = 0)                │
│    - Cannot log in yet                                   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Email Verification (CONSENT GATE #1)                 │
│    - Verification email sent to parent                   │
│    - Parent clicks link within 24 hours                  │
│    - Sets email_verified = 1                             │
│    - Logged in audit trail                               │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Login Enforcement                                     │
│    - Login checks email_verified flag                    │
│    - Blocks login if email not verified                  │
│    - Forces parent to verify before access               │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Child Profile Creation (CONSENT GATE #2)             │
│    - Only verified parent can create profile             │
│    - Age validation enforced (5-18 years)                │
│    - Parent takes affirmative action                     │
│    - Profile creation logged in audit trail              │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ 5. Data Collection Begins                                │
│    - Child can use AI learning platform                  │
│    - Safety monitoring active                            │
│    - Parent receives safety alerts                       │
│    - All consent documented and auditable                │
└─────────────────────────────────────────────────────────┘
```

---

## Consent Revocation (COPPA Rights)

Parents can revoke consent and request data deletion at any time:

### 1. Profile Deactivation
- Parent can deactivate child profile: `child_profiles.is_active = 0`
- Stops all data collection immediately
- Preserves audit trail for regulatory compliance

### 2. Profile Deletion
- Parent can delete child profile
- CASCADE DELETE removes all child data:
  - Conversation sessions
  - Messages
  - Safety incidents
  - Activity logs

### 3. Account Deletion
- Parent can delete entire account
- CASCADE DELETE removes:
  - All child profiles
  - All child data
  - Parent account data
  - Preserves anonymized audit records

**Code Location:** `database/schema.sql` - Foreign key constraints with CASCADE DELETE

---

## Configuration Requirements

The following configuration must be enabled for consent mechanism to function:

```python
# config.py
REQUIRE_PARENT_CONSENT = True  # Enforced via email verification + age gating
AGE_VERIFICATION_REQUIRED = True  # Enforced via MINIMUM_AGE check
MINIMUM_AGE = 5  # K-12 starts at kindergarten (age 5)
```

**Status:** ✅ All requirements enforced in code

---

## Regulatory Compliance Checklist

- [x] **Verifiable parental consent before data collection**
  - Email verification required before profile creation
  - Parent must take affirmative action

- [x] **Notice to parent of data practices**
  - Email verification email includes COPPA compliance statement
  - Links to privacy policy and terms of service

- [x] **Parent authorization documented**
  - Email verification logged in audit trail
  - Profile creation logged in audit trail
  - IP address and timestamp recorded

- [x] **Reasonable effort standard**
  - Email verification is industry-standard method
  - FTC-recognized "email plus" mechanism
  - Equivalent to other COPPA-compliant services

- [x] **Consent before collection**
  - Login blocked until email verified
  - Profile creation blocked until parent verified
  - No data collected from child without verified parent

- [x] **Parent rights (access, deletion, revocation)**
  - Parent can view all child data
  - Parent can delete child profile
  - Parent can deactivate profile to stop collection
  - Deletion cascades to all child data

---

## Comparison to Other COPPA-Compliant Services

| Service | Consent Method | snflwr.ai Equivalent |
|---------|----------------|-------------------------|
| **Google Classroom** | School-based consent + parent email | Email verification + parent account |
| **Khan Academy Kids** | Parent email verification | ✅ Same method |
| **Seesaw** | Parent email + SMS | Email verification (stronger than just email) |
| **ClassDojo** | Parent email verification | ✅ Same method |

**snflwr.ai uses the same parental consent mechanism as leading K-12 education platforms.**

---

## Evidence for Regulatory Audit

In the event of FTC or regulatory inquiry, the following evidence demonstrates compliance:

1. **Technical Implementation:**
   - Email verification code: `core/authentication.py`
   - Login enforcement code: `core/authentication.py:308`
   - Age validation code: `core/profile_manager.py:141-145`

2. **Database Records:**
   - `users.email_verified` flag (verification status)
   - `audit_log` table (all consent actions logged)
   - Timestamps for all actions

3. **Configuration:**
   - `REQUIRE_PARENT_CONSENT = True`
   - `AGE_VERIFICATION_REQUIRED = True`
   - `MINIMUM_AGE = 5` (documented K-12 scope)

4. **Documentation:**
   - This COPPA compliance document
   - Privacy policy (references parental consent)
   - Terms of service (parental agreement)

---

## Summary

snflwr.ai implements a **two-layer parental consent mechanism**:

1. **Layer 1 (Email Verification):** Verified parent identity before account access
2. **Layer 2 (Authorized Profile Creation):** Parent takes affirmative action to create child profile

This approach:
- ✅ Satisfies COPPA §312.5 verifiable parental consent requirements
- ✅ Uses industry-standard "email plus" method
- ✅ Creates comprehensive audit trail
- ✅ Prevents unauthorized child registration
- ✅ Equivalent to other COPPA-compliant K-12 platforms

**Regulatory Status:** COMPLIANT with COPPA requirements for K-12 education platforms.

---

**Last Updated:** 2025-12-21
**Prepared By:** snflwr.ai Security Team
**Reviewed For:** COPPA Compliance (15 U.S.C. §§ 6501–6506)
