# Changelog

All notable changes to snflwr.ai will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-06-18

### Added
- **Tutor backbone switched to `gemma4:e4b`** (won the June 2026 tutoring
  bake-off); qwen3.5 tiers remain as the low-RAM fallback.
- **Guarded upgrade framework** — `./deploy.sh --upgrade <owui|ollama|model>`
  pulls, snapshots, smoke-tests, and auto-rolls-back per component. New scripts:
  `guarded_upgrade.sh`, `gh_latest_release.py`, `model_canary.py`,
  `owui_connect.py`. See `docs/guides/UPGRADE_FRAMEWORK.md`.
- **Semantic safety classifier enabled** — `deploy.sh` now pulls `SAFETY_MODEL`
  (default `llama-guard3:8b`) so the ML safety layer is on by default; an
  operator alert fires if it is disabled at startup.
- **Crisis escalation wired into the proxy path** students use — self-harm/major
  blocks now record a DB incident + parent alert (previously only on the
  unused `chat.py` route). Fail-safe.
- Open WebUI pinned via `OWU_IMAGE_TAG` (v0.9.6), Ollama via `OLLAMA_IMAGE_TAG`
  (0.30.10); `ENABLE_INITIAL_ADMIN_SIGNUP` for first-admin creation.
- `docs/architecture/REQUEST_FLOW_AND_SAFETY.md`, `docs/compliance/REQUIRED_DISCLOSURES.md`.

### Fixed
- **Database now actually encrypted at rest** — the image lacked a SQLCipher
  driver, so encryption silently fell back to plaintext. Ship `sqlcipher3-binary`;
  fix the schema-init and VACUUM paths that bypassed the encrypted adapter;
  persist `DB_ENCRYPTION_KEY` in `.env.home`.
- Open WebUI ↔ proxy authentication (model list was empty); student model
  visibility (students no longer see backbone/backup model variants);
  `deploy.sh` API wait-loop abort; safety-incident log file permissions.

### Security
- Note: the `[1.0.0]` "AES-256 encryption for data at rest" entry was
  aspirational — at-rest encryption is actually enforced as of this release.

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
