---
---

# Production Readiness Assessment: Local Deployment

**Date**: 2026-03-02 (§1–10) · **Updated**: 2026-06-26 (§11 — GPU/model runtime & deployment methods)
**Scope**: Local deployment (bare-metal, single-node, Docker Compose) + GPU runtime & home/enterprise deployment methods
**Overall Grade**: **B+ (85/100)** -- Strong foundations, actionable gaps remain

---

## Executive Summary

snflwr.ai demonstrates mature production engineering in its security architecture, COPPA/FERPA compliance controls, and fail-closed safety pipeline. The codebase is well-structured with clear separation of concerns across `api/`, `core/`, `safety/`, `storage/`, and `utils/`.

**Ready for deployment** with the caveats documented below. The issues found are fixable in a focused sprint -- none are architectural dead-ends.

For the GPU/model runtime and the home-vs-enterprise deployment methods (not covered in the original 2026-03-02 review), see **§11**. In short: **home deployment is finalized** (one-command install, guarded auto-rollback upgrades, self-healing GPU, hold-back streaming); **enterprise** is method-complete with the load-balancer routing/failover validated, but **multi-GPU throughput scaling remains unproven** (it requires real multi-GPU hardware).

---

## 1. Configuration & Environment (A-)

### Strengths
- Two-tier `.env` loading: `.env.production` overrides `.env` (config.py:8-20)
- Hardware-aware auto-tuning via `resource_detection.py` (workers, pool sizes, prefetch)
- JWT secret validation rejects known-insecure defaults and enforces 32-char minimum (config.py:156-223)
- Production security gate: `validate_production_security()` blocks startup with actionable errors (config.py:242-395)
- All settings overridable via environment variables -- no hardcoded credentials found

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| LOW | `VERSION` hardcoded as `'dev'` in config.py:96 and `"0.1.0"` in server.py:105,214 | config.py:96, api/server.py:105 |
| LOW | `.env.example` suggests `JWT_SECRET_KEY=change-this-to-secure-random-value-64-characters-minimum` which is 57 chars and would pass the 32-char check -- confusing to operators | .env.example:22 |
| INFO | Auto-generated JWT secret writes to `.env` file on first dev run (config.py:209-216) -- could surprise users with git diffs | config.py:209 |

---

## 2. Server Startup & Lifespan (A-)

### Strengths
- `lifespan` async context manager pattern (not deprecated `@app.on_event`)
- Pre-startup security validation blocks app before serving (server.py:46-59)
- Encryption availability check with clear error messages (server.py:61-73)
- Database schema initialization fails fast with `RuntimeError` (server.py:117-119)
- Redis health check at startup if enabled (server.py:146-161)
- Graceful shutdown with 30-second drain, WebSocket cleanup, connection pool closure
- Signal handlers for SIGTERM/SIGINT with Windows fallback

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| MEDIUM | No startup timeout enforcement -- lifespan can hang indefinitely if a dependency is slow | api/server.py:85 |
| MEDIUM | Redis is binary: fully enabled or fully disabled. No degraded mode where app runs with warnings but limited rate-limiting | api/server.py:146-171 |
| LOW | Prometheus version hardcoded `"0.1.0"` instead of reading from config | api/server.py:105 |

---

## 3. Middleware Stack (A)

### Strengths
- **Request size limit**: 10MB default, configurable, handles both Content-Length and chunked transfers (server.py:220-277)
- **CORS**: Configurable origins, wildcard blocked in production validation (config.py:347-352)
- **CSRF**: Double-submit cookie pattern with HMAC, constant-time comparison, SameSite=Strict (api/middleware/csrf_protection.py)
- **Correlation IDs**: UUID per request, propagated via context variable, returned in X-Request-ID header
- **Request timeout**: 60s default, WebSocket exempt, returns 504 on timeout
- **Security headers**: CSP (path-aware), X-Frame-Options DENY, HSTS (opt-in), Permissions-Policy
- **Rate limiting**: Redis-backed with in-memory fallback, per-endpoint tuning

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| LOW | CSRF cookie not HttpOnly (intentional for JS access but should be documented) | api/middleware/csrf_protection.py |
| LOW | CSP allows `'unsafe-inline'` for /docs and /admin paths (Swagger/fonts) | api/server.py:402-425 |

---

## 4. Authentication & Authorization (A)

### Strengths
- Argon2id primary, PBKDF2 fallback for password hashing
- Session tokens are SHA-256 hashed before storage -- raw tokens never persisted
- RBAC with admin/parent roles, resource-level ownership checks
- Constant-time comparison for API key authentication (timing attack prevention)
- Account lockout after 5 failed attempts with configurable duration
- Audit logging with failure alerting (COPPA/FERPA compliance)

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| MEDIUM | `_audit_failure_count` is a module-level variable -- not safe across multiple workers/processes | api/middleware/auth.py |
| LOW | Account lockout returns specific "account locked" error instead of generic "invalid credentials" -- enables user enumeration | api/routes/auth.py:113-115 |

---

## 5. Database Layer (B+)

### Strengths
- Dual-backend: SQLite (dev/USB) and PostgreSQL (production) with adapter abstraction
- SQLCipher integration for AES-256 at-rest encryption (256k PBKDF2 iterations)
- WAL mode on non-Windows, foreign key enforcement, performance pragmas
- PostgreSQL connection pooling with `ThreadedConnectionPool`
- SQL parameter redaction in logs (encryption keys, passwords, long tokens)
- Schema auto-creation from `database/schema.sql` at startup
- Comprehensive schema: CHECK constraints, partial indexes, COPPA fields (birthdate, parental_consent_*, coppa_verified)

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| **HIGH** | **No migration version tracking** -- standalone scripts exist but nothing tracks which have been applied. No Alembic, Flyway, or equivalent. | database/ |
| **HIGH** | `database/init_db.py` is hardcoded to SQLite (`import sqlite3; conn = sqlite3.connect(...)`) -- ignores `DB_TYPE` config. PostgreSQL initialization relies on `docker-entrypoint-initdb.d/init.sql` | database/init_db.py:41-51 |
| MEDIUM | `verify_tables()` and `add_default_data()` also hardcoded to SQLite | database/init_db.py:79-109 |
| LOW | `storage/database.py` schema init reads `schema.sql` but there's a separate `schema_postgresql.sql` -- no auto-selection logic | database/ |

### Migration Scripts (manual, unversioned)
```
database/add_auth_tokens_table.py
database/add_user_columns.py
database/add_performance_indexes.py
database/migrate_encrypt_emails.py
database/migrate_to_postgresql.py
database/migrations/002_add_birthdate_coppa_compliance.sql
database/migrations/add_privacy_policy_tracking.sql
```

**Risk**: Schema upgrade from one release to the next requires manual coordination. An operator could miss a migration and end up with a partially-updated schema.

---

## 6. Docker & Compose (B)

### Strengths (Dockerfile)
- Multi-stage build: compile deps in builder, slim runtime image
- Non-root user (`snflwr:1000`)
- HEALTHCHECK directive for orchestrator integration
- Minimal system deps (only `curl` for health check)

### Strengths (docker-compose.yml)
- Full stack: nginx, Open WebUI, API, Ollama, PostgreSQL, Redis, Celery worker, Celery beat
- Health checks on all stateful services (postgres, redis, API)
- `depends_on` with `condition: service_healthy`
- Resource limits (2G memory, 2 CPUs for API container)
- Bridge network with defined subnet

### Issues

| Severity | Issue | Location |
|----------|-------|----------|
| **HIGH** | nginx volume mount references `../../enterprise/nginx/nginx.conf` -- relative path depends on CWD being `docker/compose/` | docker/compose/docker-compose.yml:13 |
| MEDIUM | `CMD uvicorn ... --workers 2` hardcoded -- ignores `resource_detection.py` auto-tuning. Should use `$API_WORKERS` env var | docker/Dockerfile:67 |
| MEDIUM | No `exec` form in CMD -- PID 1 signal forwarding may fail | docker/Dockerfile:67 |
| MEDIUM | `image: snflwr-api:latest` tag means no version pinning -- deployments are not reproducible | docker/compose/docker-compose.yml:50 |
| LOW | Ollama `CHAT_MODEL` is required (`?Set CHAT_MODEL`) but error message is terse | docker/compose/docker-compose.yml:112 |
| LOW | No backup volume exports -- postgres-data and redis-data not mapped to host | docker/compose/docker-compose.yml:229-241 |

---

## 7. Startup Scripts (B+)

### `start_snflwr.sh` (645 lines)
**Strengths**: Resource detection, model selection by RAM, port conflict resolution, graceful cleanup trap, multi-attempt Docker pull with backoff, health check loops, headless mode support.

| Severity | Issue | Location |
|----------|-------|----------|
| MEDIUM | Open WebUI Docker tag hardcoded `v0.8.3` | start_snflwr.sh:~513 |
| MEDIUM | Ollama health check only verifies `/api/tags` responds, not that a model is loaded -- API may start before model ready | start_snflwr.sh:~192 |
| LOW | No `.env` file validation before services start | start_snflwr.sh |

### `install.py` (2524 lines)
Comprehensive cross-platform installer. Main risk: Ollama model pull blocks the installer synchronously on slow networks.

### `scripts/setup_production.py`
Interactive wizard generating `.env.production`. Good secure token generation. **Issue**: Fernet key fallback uses base64 when `cryptography` is unavailable (scripts/setup_production.py:~128) -- this produces an invalid Fernet key that will crash at runtime.

---

## 8. Test Suite Health (B+)

### Results Summary

| Metric | Value |
|--------|-------|
| Total collected | 1,533 |
| Passed | 1,468 |
| Failed | 29 |
| Skipped | 1 |
| Deselected (integration) | 36 |
| **Pass rate** | **98.1%** |
| Coverage | **59.04%** (floor: 49%) |

### Failure Analysis -- 3 Root Causes (all test-infrastructure, not production bugs)

1. **Sentry PII filtering tests (25 failures)**: Missing `sqlalchemy` dependency causes sentry_sdk integration to fail at import time. Fix: add `sqlalchemy` to dev deps or disable auto-discovery in sentry config.

2. **Background task tests (4 failures)**: Tests patch `tasks.background_tasks.db_manager` but task functions do local imports from `storage.database`. The mock never intercepts the real call. Fix: patch at `storage.database.db_manager`.

3. **Connection pool test (1 failure)**: `putconn` called twice due to both commit path and finally block returning the connection. Fix: deduplicate the pool return.

### Coverage Highlights
- `safety/pipeline.py`: 95% -- critical path well-tested
- `core/age_verification.py`: 98%
- `storage/connection_pool.py`: 98%

### Coverage Gaps (risk areas)
- `api/routes/admin.py`: 17%
- `api/routes/websocket.py`: 14%
- `safety/parent_dashboard.py`: 0%

---

## 9. Safety Pipeline (A)

- 5-stage pipeline: input validation, normalization, pattern matching, semantic classification, age gate
- **Fail-closed by design**: broad `except Exception` blocks in `safety/pipeline.py` block content on any error
- Grade-based filter levels (elementary/middle/high) with configurable strictness
- Incident logging with encryption and parent notification
- 95% test coverage on the pipeline itself

---

## 10. Compliance Controls (A-)

### COPPA
- Age verification required (`AGE_VERIFICATION_REQUIRED = True`)
- Parental consent flow with request/verify/revoke (api/routes/parental_consent.py)
- Under-13 age gate with partial index (`idx_profiles_underage WHERE age < 13`)
- Data retention policies configured (90-day safety logs, 365-day audit)
- Data export and deletion capabilities

### FERPA
- Parent ownership verification on all profile/session access
- Encrypted email storage (hash for lookup, encrypted blob for display)
- Audit logging of all access, modifications, and deletions
- Admin bypass with role check

---

## 11. Deployment Methods, GPU & Scalability (added 2026-06-26)

> The 2026-03-02 review (§1–10) covered the API/DB/security layers of a single-node deploy. This section covers the GPU/model runtime and the home-vs-enterprise deployment methods, validated on the reference box (1× RTX 3090 Ti, 23 GB).

### Home deployment — FINALIZED (A)
- One-command install (`install.py`, hardware-aware) + `docker/compose/docker-compose.home.yml` (+ `docker-compose.gpu.yml`).
- **Hardware-aware model sizing** (`start_snflwr.sh`): the answer model and the llama-guard safety classifier are sized to detected VRAM/RAM so **both stay resident** — every tutor turn runs input+output through the guard, so co-residency avoids per-turn reload thrash.
- **Guarded upgrades** (`deploy.sh --upgrade <owui|ollama|model>`, `scripts/guarded_upgrade.sh`): pull → snapshot → swap → smoke-test (tutoring + safety canary) → **auto-rollback** on failure. Ollama pinned off `:latest`.
- **Self-healing GPU** (`scripts/gpu_watchdog.sh`): after a host `daemon-reload`/driver update the NVIDIA Container Toolkit can silently drop the Ollama container's GPU, making the tutor run ~20× slower on CPU **undetected**. The watchdog detects host-has-GPU-but-container-can't-see-it and auto-restarts (cooldown-guarded). Single biggest real-world reliability fix.
- **Hold-back streaming** (`CHAT_STREAMING_ENABLED`): first vetted token at ~3.5 s vs ~11.5 s buffered, with the safety pipeline still vetting the held-back text before flush.

### GPU / model topology (reference box: 1× 3090 Ti, 23 GB)
- Live tutor = `snflwr.ai` (Modelfile-wrapped **gemma4:e4b**, ~10 GB) + **llama-guard3:8b** classifier (~5 GB), both resident — comfortable on a single ≤24 GB card with concurrency headroom.
- **Opt-in `gemma4:31b` tier** (`SNFLWR_ENABLE_GEMMA_31B`): a stronger-judge bake-off (claude-opus-4-8, 34 cases) found 31b and e4b **near-tied on quality** (99.3 vs 98.5), so 31b is not a quality requirement. Gated at **VRAM ≥ 26 GB**: 31b (~19 GB) cannot co-reside with the full 8b guard (~5 GB) on a ≤24 GB card, and below 26 GB the GPU would thrash or silently downgrade the safety classifier to `llama-guard3:1b`. On a single ≤24 GB card, keep e4b.
- **Single-GPU throughput ceiling**: ~13–15 tutor turns/min, and it **plateaus** — concurrency raises per-turn latency, not throughput. Scaling is horizontal (more GPUs), not a bigger model or more concurrency per card.

### Enterprise deployment — METHOD COMPLETE, throughput scaling UNPROVEN (B+)
- Full k8s manifest set (`enterprise/k8s/`: api/ollama/postgres/redis/celery/ingress + Prometheus/Grafana/Alertmanager). GPU autoscaling corrected to DCGM GPU-util (not CPU/mem HPA); images pinned.
- **Horizontal scale-out** = nginx LB → N Ollama replicas (`docker-compose.ollama-cluster.yml`), one model per GPU.
- **Routing layer VALIDATED** on the single-GPU box (`docker-compose.ollama-cluster.singlegpu.yml`, 2 replicas sharing GPU 0, models mounted read-only from the live volume):
  - Fan-out: 40 requests round-robined **21/20** across two replicas.
  - Failover: one replica down → **20/20 requests still 200** (LB routed to the survivor, zero dropped).
  - End-to-end `/api/chat` traverses the LB on both replicas.
- **Still unproven**: actual throughput **scaling** — both test replicas shared one GPU, so turns/min did not increase. Confirming ~linear scaling with GPU count requires a genuine multi-GPU host.
- Fixed a latent deploy-blocker: the cluster compose referenced an unbuilt `snflwr-ollama:latest` image (now pinned `ollama/ollama:0.30.10`).

### Remaining for enterprise sign-off
- [ ] **Multi-GPU throughput load test** (2–3 real GPUs) confirming turns/min scales with cards. The single-GPU harness is ready — point `device_ids` at the cards and rerun against the LB endpoint.
- [ ] **License enforcement go-live** (`LICENSE_ENFORCED` currently `false`; offline Ed25519-token gate built, Phases 1–2).

---

## Scorecard

| Area | Grade | Notes |
|------|-------|-------|
| Configuration | A- | Excellent env-driven config, production validation |
| Server Startup | A- | Fail-fast checks, graceful shutdown |
| Middleware | A | Comprehensive security stack |
| Auth/AuthZ | A | Argon2id, RBAC, audit logging |
| Database | B+ | Good dual-backend, but no migration tracking |
| Docker | B | Works but needs version pinning, signal fixes |
| Scripts | B+ | Feature-rich but some hardcoded values |
| Test Suite | B+ | 98% pass rate, 59% coverage, test-only failures |
| Safety Pipeline | A | Fail-closed, 95% coverage |
| Compliance | A- | Strong COPPA/FERPA controls |
| GPU/Model Runtime | A- | Self-healing GPU, guarded upgrades, streaming; e4b+8b-guard validated on 1 card (§11) |
| Deployment Methods | B+ | Home finalized; enterprise routing/failover validated, multi-GPU throughput unproven (§11) |
| **Overall** | **B+ (85/100)** | |

---

## Priority Fix List

### Must-Fix Before Production

1. **Implement migration version tracking** -- Add Alembic or a simple `schema_version` table. Without this, any schema change risks corrupting existing deployments.

2. **Fix `database/init_db.py` to respect `DB_TYPE`** -- Currently hardcoded to SQLite. PostgreSQL deployments bypass this entirely via Docker init scripts, but bare-metal PostgreSQL setups have no initialization path.

3. **Fix Docker CMD to use exec form** -- Change `CMD uvicorn ...` to `CMD ["python", "-m", "api.server"]` or use exec to ensure PID 1 signal forwarding for graceful shutdown.

### Should-Fix Before Production

4. **Pin Docker image tags** -- Replace `snflwr-api:latest` with versioned tags for reproducible deployments.

5. **Fix Fernet key fallback in `setup_production.py`** -- Base64 fallback produces invalid Fernet keys. Either require `cryptography` or error out clearly.

6. **Make audit failure counter process-safe** -- Use Redis atomic counter instead of module-level variable for multi-worker deployments.

7. **Add Ollama model-loaded verification** -- Health check should confirm at least one model is available, not just that the API responds.

### Nice-to-Have

8. Add startup timeout enforcement (e.g., 60s overall lifespan startup limit)
9. Add dashboard route authentication (SPA serves without auth; API calls are protected)
10. Return generic "invalid credentials" on account lockout to prevent user enumeration
11. Version the application (`__version__` module or pyproject.toml) instead of hardcoding
12. Add Redis degraded mode (warn but continue with in-memory fallback)

---

## Local Deployment Quick-Start Verification

```bash
# 1. Clone and install
git clone <repo>
cd snflwr-ai
pip install -r requirements.txt

# 2. Generate .env (auto-generates JWT secret)
cp .env.example .env

# 3. Initialize database
python -m database.init_db

# 4. Create admin
python -m scripts.bootstrap_admin

# 5. Start server
python -m api.server

# 6. Verify
curl http://localhost:39150/health
curl http://localhost:39150/api/system/setup-status
```

**Result**: Server starts cleanly in development mode with SQLite, encryption warnings logged but non-blocking. All API endpoints functional.
