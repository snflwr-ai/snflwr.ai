# Changelog

All notable changes to snflwr.ai will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-07-07

### Added
- **Pedagogy guidance-enforcer (homework integrity)** (#207, #208, #209) — a
  fail-open output post-processor for the one tutoring residual (the tutor
  occasionally hands over the final answer under a "just give me it" push). When
  it detects a revealed answer on a homework turn, it regenerates ONCE to withhold
  it and guide instead. An LLM "did this reveal the answer?" confirm runs on every
  homework turn (a minority of turns; it catches word-form reveals like "equals
  four" that a regex cannot); the rewrite is re-vetted through the safety
  `check_output` before it reaches the child, and any confirm/re-prompt error or
  timeout serves the original — it never blocks a turn. OFF by default
  (`GUIDANCE_ENFORCEMENT_ENABLED`); a blind-judged canary measured homework
  reveals converting pedagogy 0→2 with no correctness/tone regression.
- **Child-safety disclosure banner in the chat UI** — `scripts/owui_connect.py`
  now seeds a persistent, non-dismissible Open WebUI banner combining the
  AI-generated-content notice + the crisis/988 notice (vetted dashboard wording)
  into OWUI's `ui.banners` config, idempotently, for both the sqlite (home) and
  Postgres (k8s) paths. Runs as part of the existing deploy/seed step; closes the
  last chat-UI disclosure gap in `docs/compliance/REQUIRED_DISCLOSURES.md`.
- **Parent onboarding & child identity linking** (#194) — separates the three
  identities the schema was built for: a parent (dashboard account, no chat
  login), a child chat login (Open WebUI user id, stored on
  `child_profiles.owui_user_id`), and a child profile. The safety proxy now
  resolves each child by their own `owui_user_id`; an admin-gated
  `POST /api/admin/families` provisions a parent + per-child OWUI logins +
  linked profiles atomically, with compensating rollback.
- **Open WebUI as a first-class enterprise k8s workload** (#196) — Deployment +
  Service + PVC, its `openwebui` Postgres role/DB provisioned declaratively via
  CNPG `managed.roles` + a `Database` CRD (non-superuser, isolated from
  `snflwr_db`), NetworkPolicies that let OWUI reach only the safety proxy + its
  Postgres (never Ollama directly — enforced at L3/L4), a config-seed Job that
  wires the proxy bearer into OWUI's Postgres config, and a TLS ingress. The
  k8s enterprise stack is no longer API-only.

### Fixed
- **`OPEN_WEBUI_URL` server-to-server gap** (#195) — the `snflwr-api` container
  defaulted to an unreachable `localhost:3000`, breaking the admin OWUI-SSO
  bridge, profile sync, and the new families endpoint. Split into
  `OPEN_WEBUI_INTERNAL_URL` (server→OWUI, defaults to `OPEN_WEBUI_URL`) so the
  browser-facing value stays separate; wired into the compose files.
- **Open WebUI k8s pod would not start** (#197) — the upstream OWUI v0.9.6 image
  runs as root, so `runAsNonRoot: true` made the kubelet refuse the pod
  (`CreateContainerConfigError`). Caught on a live kind+Calico validation
  cluster. Set `runAsNonRoot: false` (capabilities still fully dropped +
  privilege escalation disabled + network-isolated).
- **Open WebUI could not cold-start under its own egress NetworkPolicy** (#198) —
  OWUI fetches its embedding model from HuggingFace at startup, which the egress
  policy blocks. Set `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` so it loads
  the model from the PVC cache; documented the rollout order (first boot caches
  the model before the netpol is applied).

### Changed
- **CNPG off-cluster PITR is now opt-in** (#206) — the CloudNativePG cluster
  shipped an *active* `backup:` block with placeholder `s3://CHANGE-ME-bucket`
  values, so applying it would make CNPG silently fail WAL archiving (reads as
  "PITR is on"). The `backup:` block and `ScheduledBackup` are now commented out
  with step-by-step enablement docs; the default cluster stays 3-node HA with
  local WAL, and off-cluster archiving requires an operator to add real S3 creds.
- **Tutoring-eval scorer calibration** (#203, #204, #205) — fixed a family of
  deterministic-scorer bugs in `evals/tutoring/` (a fraction/word-form
  false-positive in reveal detection; homework refusals docked against a
  full-answer word floor; readability decaying on band width instead of absolute
  grades). Makes the eval's measurement trustworthy; no app/runtime change.

### Security
- **Open WebUI k8s hardening** (#198) — `seccompProfile: RuntimeDefault` on the
  OWUI Deployment + config-seed Job.
- **Version-update nag removed** (#193) — pinned OWUI is a deliberate choice;
  disabled the "new version available" banner.

## [Prior Unreleased] - 2026-06-27

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
  bake-off); low-RAM fallback tiers were later removed (2026-09-10).
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
- **Open WebUI 0.10.x new-surface lockdown** — 0.10 introduced team
  folder-sharing, per-user webhooks, automations, a calendar, and a web/URL
  upload path. The compose env now explicitly denies every new student-reachable
  surface (`USER_PERMISSIONS_CHAT_WEB_UPLOAD/IMPORT`,
  `FEATURES_DIRECT_TOOL_SERVERS/AUTOMATIONS/USER_WEBHOOKS/CALENDAR`,
  `FOLDERS_ALLOW_SHARING`, public-sharing perms) plus global kill-switches
  (`ENABLE_USER_WEBHOOKS/AUTOMATIONS/CALENDAR/NOTES/CHANNELS`) — set even where
  the 0.10 default is already off, so an upstream flip can't reopen them. Kept
  for when 0.10 is adopted (see the revert note below).
- **Reverted the OWUI pin 0.10.2 → 0.9.6.** The 0.10.2 upgrade was attempted and
  fully wired (guarded upgrader, DB migrations, all fixes below), but OWUI 0.10's
  UI **cannot live-render our tutor's streamed answer**: gemma4:e4b emits a
  native `thinking` (reasoning) field, and 0.10's reasoning display leaves the
  answer blank until a page reload (the reply is generated and saved correctly).
  Proven not to be the proxy (direct-to-Ollama fails identically) and not
  OWUI-universal (a plain non-reasoning model renders live on 0.10). 0.9.6
  renders the reasoning stream fine. Re-adopt 0.10 once OWUI fixes its reasoning
  renderer or the tutor moves to a non-reasoning backbone. All proxy fixes and
  the lockdown are version-independent and stay.
- `docs/architecture/REQUEST_FLOW_AND_SAFETY.md`, `docs/compliance/REQUIRED_DISCLOSURES.md`.

### Changed
- **Versioned database migrations** — replaced the ad-hoc schema mechanisms
  (inline CREATE+ALTER on startup, the `schema.sql` differ, and loose `*.sql`
  files) with a single lightweight runner (`database/migrations/`). Migrations
  are ordered, dialect-aware (SQLite + Postgres), reversible to an explicit
  target, and tracked in a `schema_migrations` table. Existing databases are
  auto-detected and stamped at the baseline on first run; fresh installs run
  `0001 → head`. Startup, `database/init_db.py`, and `python -m database.migrate`
  all apply migrations through the runner; `storage/schema.py` is the single
  source of schema truth.

### Fixed
- **Tutor model is pinned server-side for students** (hardening, #190) — the
  Ollama proxy trusted the client-supplied `model` on `/api/chat`, so a crafted
  student request naming the raw backbone could run it unpinned (losing the
  tutor Modelfile's system/safety prompt). Student turns now coerce any model
  outside `_student_visible_models()` to the tutor before forwarding. The safety
  pipeline already ran regardless, so this closes a defense-in-depth gap.
- **Students can see the tutor model on Open WebUI ≥0.10** — 0.10 applies model
  access-control to base Ollama models, so a non-admin student's model dropdown
  came up **empty** and they couldn't start a chat at all. Set
  `BYPASS_MODEL_ACCESS_CONTROL=true` across the compose files: the `snflwr-api`
  proxy is the real enforcement point (it filters `/api/tags` per-student to
  only `snflwr.ai` and runs the full safety pipeline), so OWUI's gate is
  redundant here and only broke student access. Verified end-to-end: a
  provisioned student now selects the tutor, gets a rendered answer, and a
  blocked prompt shows the safe-redirect message.
- **Block / safe-redirect messages render on Open WebUI ≥0.10** — early gate
  responses (rate-limit, circuit, license, no-profile, COPPA, input-safety) were
  returned as a non-streaming `JSONResponse`, which OWUI 0.10 won't render when a
  stream was requested — a blocked child saw a blank bubble instead of the
  988/safe-redirect text. Blocks now honour the requested format: a single NDJSON
  chunk when `stream=True`, JSON otherwise.
- **Tutor answers render on Open WebUI ≥0.10** — the tutor is a reasoning model
  and emits a `message.thinking` field; OWUI 0.10's new reasoning display
  mishandled it on the proxied stream and rendered a blank answer. The proxy now
  strips `thinking` from every response path (streaming + non-streaming), keeping
  the model's reasoning server-side. `content` — the only field output-safety
  vets — is untouched, and the raw chain-of-thought never reaches a child.
- **API image now includes `database/`** — the Dockerfile never copied the
  `database/` package, so an image built from current `main` failed to boot
  (`No module named 'database'` once the migration runner loads at startup).
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
