# Architecture Hardening: Operational Resilience

**Date:** 2026-04-13
**Scope:** API key rotation, rate limiting resilience, Stage 4 classifier recovery
**Out of scope:** Open WebUI decoupling (separate spec)

---

## Problem

Three operational weaknesses share a pattern: graceful fallback with near-zero observability. When things degrade, the system keeps running but nobody knows it's running in a weakened state.

1. **Single INTERNAL_API_KEY** with no rotation mechanism, a hardcoded insecure default in the OWU middleware, and full admin access if leaked.
2. **Home mode rate limiting** uses an in-memory dict that resets on container restart. Production mode fails open on Redis errors, silently allowing requests through.
3. **Stage 4 semantic classifier** disables itself at init when Ollama is unreachable and never recovers. No email alert, no health endpoint visibility, no auto-retry.

## Design

### 1. API Key Rotation (Dual-Key Overlap)

#### Config (`config.py`)

New environment variables:

| Variable | Default | Required | Description |
|---|---|---|---|
| `INTERNAL_API_KEY_PREVIOUS` | `None` | No | Previous key, accepted during rotation window |
| `INTERNAL_API_KEY_MAX_AGE_DAYS` | `90` | No | Days before rotation warning fires |
| `INTERNAL_API_KEY_CREATED_AT` | `None` | No | ISO 8601 timestamp of when current key was set |

Production validation additions:
- Reject startup if `INTERNAL_API_KEY` equals `snflwr-internal-dev-key`
- Warn if `INTERNAL_API_KEY_CREATED_AT` is not set (recommend operator track it)

#### Auth Middleware (`api/middleware/auth.py`)

Update the internal API key check (currently line 65):
- `hmac.compare_digest` against primary key first
- If no match and `INTERNAL_API_KEY_PREVIOUS` is set, check previous key
- If authenticated via previous key, log WARNING: `"Request authenticated with previous API key -- rotation in progress or stale config"`
- If neither matches, reject as today

#### Rotation Age Check (background task in `api/server.py`)

- On startup + daily interval: if `INTERNAL_API_KEY_CREATED_AT` is set and older than `MAX_AGE_DAYS`, log WARNING + email alert: "Internal API key is N days old, rotation recommended"
- If `CREATED_AT` is not set, log one-time INFO recommending the operator set it

#### OWU Middleware (`frontend/.../snflwr.py`)

- Remove the hardcoded `snflwr-internal-dev-key` default
- If `INTERNAL_API_KEY` env var is missing, fail startup with a clear error

#### Rotation Procedure (documented)

1. Generate new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. In `.env`: set `INTERNAL_API_KEY=<new>`, `INTERNAL_API_KEY_PREVIOUS=<old>`, `INTERNAL_API_KEY_CREATED_AT=<now ISO 8601>`
3. Restart containers in any order (dual-key acceptance means zero downtime)
4. Once stable, remove `INTERNAL_API_KEY_PREVIOUS` from `.env`

---

### 2. Rate Limiting Resilience

#### Home Mode: SQLite-Backed Rate Limiter (`api/middleware/auth.py`)

New class `SqliteRateLimiter`:
- Table schema: `rate_limits (key TEXT, timestamp REAL)`
- Same sliding window logic as the existing in-memory fallback
- Uses the existing snflwr.db path (same database as accounts/profiles)
- Cleans expired entries on each check
- Single-writer is acceptable -- home mode is single-process
- Replaces the `_fallback_requests` in-memory dict when `REDIS_ENABLED=false`

#### Production Mode: Fail Closed on Redis Error (`api/middleware/auth.py`)

- When `RedisError` is caught during a rate limit check (currently line 627-628), block the request (return 429) instead of allowing it
- Track `_redis_healthy` flag
- On first Redis failure: log ERROR + email alert (debounced to 1 alert per 5 minutes)
- On Redis recovery (next successful check): log INFO, clear unhealthy flag

#### Alerting

- Home mode: log WARNING at startup that rate limiting is SQLite-backed (single-instance only)
- Production mode: email alert on Redis failure (debounced 5min), log ERROR per failed check, log INFO on recovery

#### Health Endpoint

Add to `/health` response:
```json
{
  "rate_limiter": "redis" | "sqlite" | "memory",
  "rate_limiter_healthy": true | false
}
```

---

### 3. Stage 4 Classifier Recovery

#### State Machine (`safety/pipeline.py`)

Three states:
- **`available`**: Ollama reachable, model loaded, classifying normally
- **`degraded`**: Was available, lost connection or model. Actively probing for recovery
- **`disabled`**: Never came up at init (Ollama not configured or unreachable on first boot). Still probing.

Transitions:
- `disabled -> available`: background probe succeeds
- `available -> degraded`: classify() call fails or health probe fails
- `degraded -> available`: background probe succeeds, model responds
- `disabled -> disabled`: probe fails, stay disabled
- `degraded -> degraded`: probe fails, stay degraded

Every transition logs at WARNING (to degraded/disabled) or INFO (to available). Email alert fires on transitions to `degraded` (from available) and on recovery to `available` (from degraded or disabled).

#### Background Health Probe

- `asyncio` task started at pipeline init
- When `degraded` or `disabled`: probe every 60 seconds
- When `available`: heartbeat probe every 5 minutes (catches silent Ollama death between classify calls)
- Probe logic: lightweight Ollama health check + verify safety model exists (same logic as current `__init__` model discovery)
- On success: set `_available = True`, update state to `available`, log + email recovery
- On failure: stay in current state, log at DEBUG (avoid spam)

#### Per-Request Behavior (unchanged safety posture)

- `available`: classify normally
- `degraded` / `disabled`: return `None` (skip to next stage) -- same as today
- If classify() raises while `available`: transition to `degraded`, return BLOCK on the failing request (fail closed) -- same as today

#### Health Endpoint

Add to `/health` response:
```json
{
  "safety_classifier": "available" | "degraded" | "disabled",
  "safety_classifier_since": "2026-04-13T02:30:00Z"
}
```

#### Email Alerts (via existing `email_alerts` system)

- To `degraded`: "Safety classifier lost Ollama connection. Deterministic stages still protecting. Auto-recovery probing every 60s."
- To `available` (from degraded/disabled): "Safety classifier recovered. Semantic classification re-enabled."

---

## Testing

### Unit Tests (`tests/test_architecture_hardening.py`)

**API Key Rotation (6 tests):**
- Primary key authenticates successfully
- Previous key authenticates successfully when set
- Random key rejected
- `snflwr-internal-dev-key` rejected in production mode
- Rotation age warning fires when key is overdue
- No warning when `CREATED_AT` is within window

**Rate Limiting (6 tests):**
- `SqliteRateLimiter` enforces limits across simulated restarts (persistence)
- `SqliteRateLimiter` cleans expired entries
- Production mode returns 429 on Redis error (fail closed)
- Redis recovery clears the unhealthy flag
- Health endpoint reports correct backend (`redis`/`sqlite`/`memory`)
- Home mode startup logs SQLite-backed warning

**Stage 4 Recovery (8 tests):**
- State transitions: `available -> degraded -> available`
- State transitions: `disabled -> available` on probe success
- Heartbeat probe interval is 5min when available, 60s when degraded
- Email alert fires on transition to `degraded`
- Email alert fires on recovery to `available`
- `classify()` returns `None` when degraded (skip, not block)
- `classify()` returns BLOCK when error occurs while `available` (fail closed)
- Health endpoint reports classifier state and timestamp

### Integration Tests (extend `test_middleware_integration.py`)

- Dual-key auth: request with previous key succeeds during rotation window
- Rate limiter fallback: SQLite rate limiter activates when Redis unavailable

### E2E Tests (extend `test_e2e_real_stack.py`, `@pytest.mark.e2e`)

- Health endpoint includes `rate_limiter` and `safety_classifier` fields with valid values

### Coverage

Floor stays at 85%. These ~22 new tests should increase coverage, not require lowering the floor.

---

## Files Modified

| File | Change |
|---|---|
| `config.py` | Add `INTERNAL_API_KEY_PREVIOUS`, `_MAX_AGE_DAYS`, `_CREATED_AT`; reject insecure default in prod |
| `api/middleware/auth.py` | Dual-key check; `SqliteRateLimiter` class; fail-closed Redis error handling; `_redis_healthy` flag |
| `api/server.py` | Key rotation age check background task |
| `api/routes/health.py` (or equivalent) | Add rate_limiter and safety_classifier to health response |
| `safety/pipeline.py` | Classifier state machine; background health probe; state transition alerting |
| `frontend/.../snflwr.py` | Remove hardcoded default key; fail on missing env var |
| `tests/test_architecture_hardening.py` | New: ~20 unit tests |
| `tests/test_middleware_integration.py` | Extend: 2 integration tests |
| `tests/test_e2e_real_stack.py` | Extend: 1 E2E test |
