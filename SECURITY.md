# Security Policy

snflwr.ai is a children's safety platform used in K-12 schools. Security vulnerabilities can directly impact minors' privacy and safety. We take every report seriously.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest `main` | Yes |
| Older releases | Best-effort |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

### How to Report

Email **security@snflwr.ai** with:

1. **Description** of the vulnerability
2. **Steps to reproduce** (as detailed as possible)
3. **Impact assessment** - what an attacker could do
4. **Affected component** (e.g., `safety/pipeline.py`, `api/middleware/auth.py`)
5. **Suggested fix** (if you have one)

### What to Expect

- **Acknowledgment** within 48 hours
- **Initial assessment** within 5 business days
- **Resolution timeline** shared once we've triaged severity
- **Credit** in the release notes (unless you prefer anonymity)

### Severity Classification

| Severity | Description | Target Response |
|----------|-------------|-----------------|
| **Critical** | Safety pipeline bypass, auth bypass, student data exfiltration | 24 hours |
| **High** | Privilege escalation, encryption weakness, PII exposure | 3 business days |
| **Medium** | CSRF bypass, rate limiting bypass, information disclosure | 7 business days |
| **Low** | Non-exploitable issues, hardening suggestions | Next release |

### What Counts as Critical

Given that this is a children's platform, the following are always treated as critical:

- Any bypass of the content safety pipeline that could expose minors to harmful content
- Any way to access student data without proper authentication/authorization
- Any way to impersonate a parent or guardian
- Any way to bypass parental consent requirements
- Decryption of stored student conversations or personal data

## Compliance

snflwr.ai is designed for K-12 educational environments and follows these compliance frameworks:

- **COPPA** (Children's Online Privacy Protection Act) - All child data is collected with verifiable parental consent, minimized by default, and subject to automatic retention-based cleanup.
- **FERPA** (Family Educational Rights and Privacy Act) - Student educational records are protected and only accessible to authorized parents/guardians and school administrators.

## Security Measures

### Authentication & Access Control
- Passwords hashed with Argon2id (PBKDF2-HMAC-SHA256 fallback)
- Account lockout after 5 failed login attempts (30-minute cooldown)
- JWT tokens with 24-hour expiry
- Parent-scoped access control for child profiles
- CSRF double-submit cookie on all state-changing operations

### Data Protection
- SQLCipher AES-256 database encryption at rest
- Email addresses stored encrypted with separate hash for lookups
- Student conversations encrypted at rest
- All secrets generated at install time via `secrets.token_hex()`
- No hardcoded credentials in application code

### Content Safety
- 5-stage safety pipeline: input validation, normalization, pattern matching, semantic classification, age gate
- **Fail-closed design** - if any stage errors, content is blocked
- Real-time safety incident logging with severity classification
- Parental alert system for safety events

### Data Retention
- COPPA-compliant automated data cleanup on configurable schedules
- Conversation retention: 180 days (configurable)
- Safety incident logs: 90 days after resolution
- Audit logs: 365 days
- Parents can export and delete child data at any time

### Infrastructure
- CORS restricted to configured origins
- Rate limiting via Redis (fails closed in production)
- Correlation ID tracking on all API requests
- All inference runs locally via Ollama - no data sent to external APIs

## Scope

### In Scope
- snflwr.ai API (`api/`, `core/`, `safety/`, `storage/`, `utils/`)
- Authentication and authorization
- Content safety pipeline
- Data encryption and key management
- COPPA/FERPA compliance logic
- Docker configuration

### Out of Scope
- Open WebUI upstream vulnerabilities (report to [Open WebUI](https://github.com/open-webui/open-webui/security))
- Ollama vulnerabilities (report to [Ollama](https://github.com/ollama/ollama/security))
- Issues requiring physical access to the server
- Social engineering attacks
- Denial of service via resource exhaustion (unless it bypasses rate limiting)

## Security Design Principles

For contributors and auditors, these are the security invariants the codebase maintains:

1. **Safety pipeline fails closed** - If any filtering stage errors, content is blocked, not allowed through
2. **No plaintext PII storage** - Emails stored as `encrypted_email` with `email_hash` for lookups
3. **Student conversations encrypted at rest** - SQLCipher (SQLite) or SSL (PostgreSQL)
4. **Parental consent required before data collection** - COPPA age gate enforced server-side
5. **Audit trail on all data access** - Encrypted incident logs with parent notification
6. **Rate limiting on all auth endpoints** - Brute-force protection via Redis (fails closed in production)
7. **No third-party data sharing** - All inference runs locally via Ollama
