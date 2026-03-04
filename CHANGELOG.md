# Changelog

All notable changes to snflwr.ai will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-01-09

### Added
- **Core Platform**
  - K-12 safe AI learning platform with privacy-first design
  - Offline operation support via USB deployment
  - Multi-child profile management with age-appropriate content filtering

- **Security & Encryption**
  - AES-256 encryption for data at rest
  - Argon2id password hashing with PBKDF2 fallback
  - CSRF protection with double-submit cookie pattern
  - Rate limiting with Redis (in-memory fallback)
  - HSTS header enforcement
  - Generic exception handler to prevent stack trace leakage

- **Safety Features**
  - Multi-layer content filtering for child safety
  - Real-time safety monitoring
  - Incident logging and parent alerts
  - Age-adaptive response validation

- **Authentication & Authorization**
  - JWT-based authentication
  - Parent/child account hierarchy
  - Session management with secure token handling

- **API**
  - 43 REST endpoints
  - WebSocket support for real-time chat
  - Comprehensive input validation via Pydantic

- **Database**
  - SQLite for development/small deployments
  - PostgreSQL support for enterprise scale
  - Optional SQLCipher encryption for SQLite

- **Compliance**
  - COPPA compliance for children under 13
  - FERPA compliance for educational records
  - Parental consent verification
  - Audit logging for all sensitive operations

- **CI/CD**
  - GitHub Actions with 9 CI jobs
  - Security scanning (CodeQL, Trivy, Bandit, Gitleaks)
  - Multi-Python version testing (3.10, 3.11, 3.12)
  - 70% code coverage requirement

- **Documentation**
  - 69 markdown documentation files
  - API reference and examples
  - Deployment guides (Docker, Kubernetes, USB)
  - Compliance documentation

### Security
- All security headers implemented (CSP, X-Frame-Options, X-Content-Type-Options, HSTS)
- No hardcoded secrets
- PII-safe logging

---

## [Unreleased]

### Planned
- Load testing validation
- Additional language model integrations
- Enhanced parent dashboard analytics
