# Changelog

All notable changes to snflwr.ai will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-06-27

### Changed
- **CI quality gates hardened** — pylint floor raised `5.0 → 8.0` (code sits at
  8.30); Bandit now *blocks* on medium-severity/medium-confidence findings (was
  high/high in `ci.yml`, advisory `|| true` in `security-scan.yml`); the JSON
  report generation stays non-blocking for artifact upload.
- **mypy now checks untyped function bodies** across `api/core/safety/storage/
  utils` (`check_untyped_defs = True`, removing the per-module opt-outs). Closed
  the resulting ~56 findings with real type annotations — including a latent
  `AttributeError` in `SessionCacheMixin` when Redis init takes the fallback
  path (`_redis` is now a class-level annotated default).

### Added
- **Scheduled DR drill** (`.github/workflows/dr-drill.yml`) — re-runs the
  Postgres backup/restore suite weekly (and on demand) so the restore guarantee
  stays honest between commits, independent of push-triggered CI.

- **Opt-in `gemma4:31b` high-end tutor tier** (`SNFLWR_ENABLE_GEMMA_31B`, GPU
  ≥26GB so it co-resides with the 8B safety classifier). A stronger-judge bake-off
  found it ~tied with `gemma4:e4b` on tutoring quality, so it's for big-GPU
  headroom, not better quality.
- **Hold-back streaming** (`CHAT_STREAMING_ENABLED`) — streams the tutor reply
  once the safety pipeline has vetted it (~1–2s first token vs buffered), without
  weakening output checking.
- **Self-healing GPU watchdog** (`scripts/gpu_watchdog.sh`) — auto-recovers the
  silent GPU→CPU fallback that made the tutor run ~20× slower undetected.
- **Scheduled, verified, alerting backups** — a daily compose `backup-cron`
  sidecar (home) and a k8s `backup-cronjob.yaml` (enterprise); a `verify`
  integrity action and operator-alert-on-failure; opt-in fail-closed off-host
  (rclone) copy.
- **Required disclosures surfaced in the dashboard** (AI-content + crisis/988
  footer; Settings "Safety & Disclosures") — `components/disclosures.js`.
- **Enterprise k8s hardening** — Ollama model `PersistentVolumeClaim` (no
  ~10–20GB re-pull on restart), daily backup CronJob, and load-balancer failover.
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
- **Per-child COPPA consent gate now enforced on the native chat route too** —
  it previously lived only in the Ollama proxy, so an under-13 profile without
  verified consent could reach the tutor via `/api/chat/send`. Shared, fail-closed
  logic in `core/coppa_gate.py`; both paths use it.

### Security
- **Production security gate no longer skippable via env-var inconsistency** —
  `is_production()` (which drives the prod hardening checks) now honors both
  `ENVIRONMENT` and `SNFLWR_ENV`; setting only `SNFLWR_ENV=production` previously
  bypassed the entire gate.
- **Raw Ollama endpoints locked down** — `/api/generate`, `/api/embed*` (raw,
  unfiltered completion) and `/api/pull|delete|copy` (model management) are now
  genuine-admin-only; students can only reach the safety-gated `/api/chat`.
- **Safety classifier fails closed** when unavailable (`SAFETY_CLASSIFIER_REQUIRED`
  defaults true; under-13 always blocked).
- **SMTP/`ADMIN_EMAIL` hard-required in production** — a blank destination/
  credentials now fails startup instead of silently dropping child-safety and
  operator alerts.
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
