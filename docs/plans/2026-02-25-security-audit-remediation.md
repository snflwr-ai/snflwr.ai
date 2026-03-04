# Security Audit Remediation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all critical and high-severity findings from the production readiness audit so the codebase passes COPPA/FERPA compliance review.

**Architecture:** Fixes are grouped by dependency order — COPPA data compliance first (plaintext email, data retention, PII logging), then security hardening (XSS, session tokens, Redis auth, PostgreSQL SSL, health endpoint, nginx, config validation), then dependency updates. Each task is independently committable.

**Tech Stack:** Python 3.11, FastAPI, psycopg2, Redis, pytest, Docker Compose, Nginx

---

## Task 1: Remove plaintext email storage from authentication.py

The `accounts` table has a plaintext `email` column alongside `email_hash` and `encrypted_email`. Two writes and two reads in `core/authentication.py` use the plaintext column. The PostgreSQL schema (`database/schema_postgresql.sql`) is already correct — only the SQLite schema and authentication code have this issue.

**Files:**
- Modify: `core/authentication.py:247-253` (INSERT)
- Modify: `core/authentication.py:536-539` (UPDATE)
- Modify: `core/authentication.py:476-480` (SELECT in validate_session)
- Modify: `core/authentication.py:634-661` (SELECT in get_user_info)
- Modify: `database/schema.sql:10` (email column)
- Modify: `tests/test_authentication.py:110-115` (test asserting plaintext email)
- Reference: `core/email_crypto.py` (EmailCrypto.decrypt_email)

**Step 1: Update test to expect encrypted-only storage**

In `tests/test_authentication.py`, find the test that asserts `result[0]['email'] == "test@example.com"` (around line 110-115). Change it to verify that the plaintext `email` column is NULL and the `encrypted_email` column is populated:

```python
result = auth_manager.db.execute_query(
    "SELECT email, encrypted_email, email_hash FROM accounts WHERE parent_id = ?",
    (parent_id,)
)
assert result[0]['email'] is None, "Plaintext email should not be stored"
assert result[0]['encrypted_email'] is not None, "Encrypted email must be stored"
assert result[0]['email_hash'] is not None, "Email hash must be stored"
```

**Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && pytest tests/test_authentication.py -v -k "test" --no-header -x 2>&1 | head -30`
Expected: FAIL because plaintext email is still being stored.

**Step 3: Fix INSERT in create_parent_account()**

In `core/authentication.py`, change lines 247-253. Replace the plaintext `email` with `None`:

```python
self.db.execute_write(
    "INSERT INTO accounts (parent_id, username, password_hash, email, "
    "email_hash, encrypted_email, device_id, role, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (parent_id, username, password_hash, None,
     email_hash, encrypted_email, device_id, role, created_at)
)
```

**Step 4: Fix UPDATE in update_parent_email()**

In `core/authentication.py`, change lines 536-539. Set `email = NULL` on update:

```python
self.db.execute_write(
    "UPDATE accounts SET email = NULL, email_hash = ?, encrypted_email = ? "
    "WHERE parent_id = ?",
    (new_hash, new_encrypted, parent_id)
)
```

**Step 5: Fix SELECT in validate_session()**

In `core/authentication.py`, change lines 476-480. Read from `encrypted_email` and decrypt:

```python
rows = self.db.execute_query(
    "SELECT encrypted_email, role FROM accounts WHERE parent_id = ?",
    (parent_id,)
)
if not rows:
    return None

from core.email_crypto import EmailCrypto
email_crypto = EmailCrypto()
encrypted = rows[0].get('encrypted_email') or rows[0][0]
email = email_crypto.decrypt_email(encrypted) if encrypted else None
```

Note: Be careful with the row access pattern — the code handles both dict and tuple results. Check how `rows[0]` is used downstream (line 496: `email=email`). The `AuthSession.email` field receives this value.

**Step 6: Fix SELECT in get_user_info()**

In `core/authentication.py`, change lines 634-661. Replace `email` with `encrypted_email` in the SELECT and decrypt:

```python
result = self.db.execute_read(
    """
    SELECT parent_id, username, encrypted_email, created_at, last_login, role, email_verified
    FROM accounts WHERE parent_id = ?
    """,
    (user_id,)
)
```

Then in the processing logic, decrypt the email:

```python
from core.email_crypto import EmailCrypto
email_crypto = EmailCrypto()

# For dict-style rows:
encrypted = row.get('encrypted_email') or row['encrypted_email']
email = email_crypto.decrypt_email(encrypted) if encrypted else None

# For tuple-style rows:
parent_id, username, encrypted_email, created_at, last_login, role, email_verified = row
email = email_crypto.decrypt_email(encrypted_email) if encrypted_email else None
```

**Step 7: Run tests to verify they pass**

Run: `source venv/bin/activate && pytest tests/test_authentication.py -v --no-header -x 2>&1 | tail -20`
Expected: PASS

**Step 8: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: All 425 tests pass.

**Step 9: Commit**

```bash
git add core/authentication.py tests/test_authentication.py
git commit -m "fix: stop storing plaintext email in accounts table (COPPA compliance)"
```

---

## Task 2: Activate data retention scheduler for COPPA compliance

The `DataRetentionManager` has 7 cleanup jobs but `start_scheduler()` is never called. Celery Beat covers only 3 of the 7. Missing: `cleanup_audit_logs`, `cleanup_sessions` (the table, not auth_tokens), `cleanup_analytics`, and `vacuum_database`.

Rather than activating the schedule-based in-process scheduler (which requires the `schedule` package and a daemon thread), the cleaner fix is to add the 4 missing cleanup tasks to Celery Beat — which is already running in production.

**Files:**
- Modify: `tasks/background_tasks.py` (add 4 new Celery tasks)
- Modify: `utils/celery_config.py:93-114` (add to beat_schedule)
- Reference: `utils/data_retention.py` (source of cleanup logic)

**Step 1: Add cleanup_audit_logs task to background_tasks.py**

Add after the existing `cleanup_old_incidents` task (around line 371):

```python
@celery_app.task(name='tasks.background_tasks.cleanup_audit_logs', bind=True, max_retries=2)
def cleanup_audit_logs(self):
    """Remove audit log entries older than retention period (COPPA compliance)."""
    try:
        from storage.database import db_manager
        from config import safety_config
        retention_days = getattr(safety_config, 'AUDIT_LOG_RETENTION_DAYS', 365)
        result = db_manager.execute_write(
            "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
            (f'-{retention_days} days',)
        )
        deleted = result if isinstance(result, int) else 0
        logger.info(f"Cleaned up {deleted} audit log entries older than {retention_days} days")
    except Exception as e:
        logger.error(f"Audit log cleanup failed: {e}")
        raise self.retry(exc=e, countdown=300)
```

**Step 2: Add cleanup_ended_sessions task**

```python
@celery_app.task(name='tasks.background_tasks.cleanup_ended_sessions', bind=True, max_retries=2)
def cleanup_ended_sessions(self):
    """Remove ended session records older than retention period."""
    try:
        from storage.database import db_manager
        from config import safety_config
        retention_days = getattr(safety_config, 'SESSION_RETENTION_DAYS', 180)
        result = db_manager.execute_write(
            "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < datetime('now', ?)",
            (f'-{retention_days} days',)
        )
        deleted = result if isinstance(result, int) else 0
        logger.info(f"Cleaned up {deleted} ended sessions older than {retention_days} days")
    except Exception as e:
        logger.error(f"Ended session cleanup failed: {e}")
        raise self.retry(exc=e, countdown=300)
```

**Step 3: Add cleanup_analytics task**

```python
@celery_app.task(name='tasks.background_tasks.cleanup_analytics', bind=True, max_retries=2)
def cleanup_analytics(self):
    """Remove learning analytics older than retention period."""
    try:
        from storage.database import db_manager
        from config import safety_config
        retention_days = getattr(safety_config, 'ANALYTICS_RETENTION_DAYS', 730)
        result = db_manager.execute_write(
            "DELETE FROM learning_analytics WHERE timestamp < datetime('now', ?)",
            (f'-{retention_days} days',)
        )
        deleted = result if isinstance(result, int) else 0
        logger.info(f"Cleaned up {deleted} analytics records older than {retention_days} days")
    except Exception as e:
        logger.error(f"Analytics cleanup failed: {e}")
        raise self.retry(exc=e, countdown=300)
```

**Step 4: Add vacuum_database task**

```python
@celery_app.task(name='tasks.background_tasks.vacuum_database')
def vacuum_database():
    """Reclaim disk space after bulk deletions (SQLite only)."""
    try:
        from storage.database import db_manager
        if db_manager.db_type == 'sqlite':
            db_manager.adapter.execute_write("VACUUM")
            logger.info("Database VACUUM completed")
        else:
            logger.debug("VACUUM skipped (PostgreSQL handles this automatically)")
    except Exception as e:
        logger.error(f"Database VACUUM failed: {e}")
```

**Step 5: Register new tasks in Celery Beat schedule**

In `utils/celery_config.py`, add to `beat_schedule` dict (after line 114):

```python
'cleanup-audit-logs': {
    'task': 'tasks.background_tasks.cleanup_audit_logs',
    'schedule': timedelta(days=1),
},
'cleanup-ended-sessions': {
    'task': 'tasks.background_tasks.cleanup_ended_sessions',
    'schedule': timedelta(days=1),
},
'cleanup-analytics': {
    'task': 'tasks.background_tasks.cleanup_analytics',
    'schedule': timedelta(days=7),
},
'vacuum-database': {
    'task': 'tasks.background_tasks.vacuum_database',
    'schedule': timedelta(days=7),
},
```

**Step 6: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: All tests pass (new tasks don't break existing tests).

**Step 7: Commit**

```bash
git add tasks/background_tasks.py utils/celery_config.py
git commit -m "fix: add missing COPPA data retention tasks to Celery Beat schedule"
```

---

## Task 3: Sanitize PII from log statements

13+ log lines expose parent email addresses in plaintext. One line logs 50 chars of children's messages. COPPA/FERPA requires PII not appear in application logs.

**Files:**
- Modify: `tasks/background_tasks.py` (lines 60, 70, 73, 78, 183)
- Modify: `core/email_service.py` (lines 430, 433, 483, 486, 536, 539, 669)
- Modify: `core/authentication.py:517`
- Modify: `api/routes/chat.py:149`

**Step 1: Create a PII masking helper**

Add to `utils/logger.py` (at the end, before any module-level singletons):

```python
def mask_email(email: str) -> str:
    """Mask email for safe logging: j***@example.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"
```

**Step 2: Replace all plaintext email logging**

In `tasks/background_tasks.py`, replace each email log:
- Line 60: `f"Sending email to {to_email}: {subject}"` → `f"Sending email to {mask_email(to_email)}: {subject}"`
- Line 70: `f"Email sent successfully to {to_email}"` → `f"Email sent successfully to {mask_email(to_email)}"`
- Line 73: `f"Email sending failed to {to_email}"` → `f"Email sending failed to {mask_email(to_email)}"`
- Line 78: `f"Error sending email to {to_email}: {exc}"` → `f"Error sending email to {mask_email(to_email)}: {exc}"`
- Line 183: `f"Failed to queue email to {email_data['to']}: {e}"` → `f"Failed to queue email to {mask_email(email_data['to'])}: {e}"`

Add import at top: `from utils.logger import mask_email`

Apply the same pattern to `core/email_service.py` (6 lines) and `core/authentication.py:517`.

**Step 3: Remove child message content from chat logs**

In `api/routes/chat.py:149`, change:
```python
logger.info(f"Chat request from profile {request.profile_id}: {request.message[:50]}...")
```
To:
```python
logger.info(f"Chat request from profile {request.profile_id}, length={len(request.message)}")
```

**Step 4: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add utils/logger.py tasks/background_tasks.py core/email_service.py core/authentication.py api/routes/chat.py
git commit -m "fix: mask PII in log statements (COPPA/FERPA compliance)"
```

---

## Task 4: Fix XSS in HTML email templates

14 unescaped f-string interpolation points across `tasks/background_tasks.py` and `core/email_service.py`. Child names, incident details, and URLs are injected into HTML without escaping.

**Files:**
- Modify: `tasks/background_tasks.py` (3 templates)
- Modify: `core/email_service.py` (5 templates)

**Step 1: Add html.escape to all interpolated values in background_tasks.py**

At the top of `tasks/background_tasks.py`, add:
```python
from html import escape as html_escape
```

Then wrap every user-controlled value in `html_escape()`. For example, in `send_safety_alert()` (lines 114-145):
```python
html_content = f"""
...
    <p><strong>Child:</strong> {html_escape(child_name)}</p>
    <p><strong>Type:</strong> {html_escape(incident_type)}</p>
    <p><strong>Time:</strong> {html_escape(str(timestamp))}</p>
    ...
    <p>{html_escape(str(details))}</p>
    ...
"""
```

Do NOT escape `system_config.BASE_URL` in `href` attributes — that's a server-controlled value, not user input. But DO escape it in display text.

Apply the same to the `send_daily_safety_digests()` template (lines 242-257): escape `incident['child_name']`, `incident['incident_type']`, `incident['timestamp']`.

**Step 2: Add html.escape to all interpolated values in core/email_service.py**

At the top of `core/email_service.py`, add:
```python
from html import escape as html_escape
```

Apply to all 5 template methods:
- `safety_alert_critical()`: escape `parent_name`, `child_name`, `severity`, `incident_count`, `description`, `snippet`
- `safety_alert_moderate()`: escape `parent_name`, `child_name`, `severity`, `incident_count`, `description`
- `email_verification()`: escape `user_name` (NOT the `verification_url` in href)
- `password_reset()`: escape `user_name` (NOT the `reset_url` in href)
- `send_parental_consent_request()`: escape `parent_name`, `child_name`, `child_age` (NOT the `consent_url` in href)

**Step 3: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: All tests pass.

**Step 4: Commit**

```bash
git add tasks/background_tasks.py core/email_service.py
git commit -m "fix: escape HTML in email templates to prevent stored XSS"
```

---

## Task 5: Hash session tokens before database storage

Session tokens are stored and compared in plaintext. Verification and password reset tokens are already SHA-256 hashed. Apply the same pattern to session tokens.

**Files:**
- Modify: `core/authentication.py` (INSERT at ~355-359, SELECT at ~418-419, logout at ~389-394)

**Step 1: Hash session token on INSERT**

In `core/authentication.py`, in the login/create_session method, after generating `session_token = secrets.token_hex(32)` (~line 340), add:

```python
import hashlib
session_token_hash = hashlib.sha256(session_token.encode()).hexdigest()
```

Then change the INSERT (~lines 355-359) to store the hash:

```python
self.db.execute_write(
    """INSERT INTO auth_tokens
       (token_id, user_id, parent_id, token_type, session_token, created_at, expires_at, is_valid)
       VALUES (?, ?, ?, 'session', ?, ?, ?, 1)""",
    (token_id, parent_id, parent_id, session_token_hash, ...)
)
```

The return value should still be the raw `session_token` (sent to the client).

**Step 2: Hash session token on SELECT (validation)**

In `validate_session()` or the method at ~lines 418-419, hash the incoming token before query:

```python
session_token_hash = hashlib.sha256(session_token.encode()).hexdigest()
rows = self.db.execute_query(
    "SELECT parent_id, expires_at FROM auth_tokens WHERE session_token = ? AND is_valid = 1",
    (session_token_hash,)
)
```

**Step 3: Hash session token on logout**

In the logout method (~lines 389-394), hash before the UPDATE:

```python
session_token_hash = hashlib.sha256(session_id.encode()).hexdigest()
self.db.execute_write(
    "UPDATE auth_tokens SET is_valid = 0 WHERE session_token = ?",
    (session_token_hash,)
)
```

**Step 4: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: All tests pass. Session-related tests use mocks, so the hashing should be transparent.

**Step 5: Commit**

```bash
git add core/authentication.py
git commit -m "fix: hash session tokens before database storage"
```

---

## Task 6: Add Redis authentication to default Docker Compose

Redis has no `--requirepass` in the default production compose stack.

**Files:**
- Modify: `docker/compose/docker-compose.yml` (Redis service + env vars for API/Celery services)
- Modify: `.env.production.example` (ensure REDIS_PASSWORD is documented)

**Step 1: Add --requirepass to Redis service**

In `docker/compose/docker-compose.yml`, change the Redis command (~line 149):

```yaml
command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru --requirepass ${REDIS_PASSWORD}
```

Update the healthcheck to authenticate:
```yaml
healthcheck:
  test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
```

**Step 2: Pass REDIS_PASSWORD to all services that connect to Redis**

Add `REDIS_PASSWORD=${REDIS_PASSWORD}` to the environment section of:
- `snflwr-api` service (~line 67)
- `celery-worker` service (~line 165)
- `celery-beat` service (~line 200)

**Step 3: Commit**

```bash
git add docker/compose/docker-compose.yml
git commit -m "fix: require Redis authentication in default Docker Compose stack"
```

---

## Task 7: Add PostgreSQL SSL support

Neither `db_adapters.py` nor `connection_pool.py` passes `sslmode` to psycopg2. Add a config variable and pass it through.

**Files:**
- Modify: `config.py` (~line 76, after POSTGRES_PASSWORD)
- Modify: `storage/db_adapters.py:246-259`
- Modify: `storage/connection_pool.py:71-87`

**Step 1: Add POSTGRES_SSLMODE config**

In `config.py`, after `POSTGRES_PASSWORD` (~line 75):

```python
POSTGRES_SSLMODE: str = os.getenv('POSTGRES_SSLMODE', 'prefer')
```

**Step 2: Add sslmode to production validation**

In `validate_production_security()`, add a check (after the PostgreSQL password check):

```python
if self.POSTGRES_SSLMODE in ('disable', 'allow', 'prefer'):
    warnings.append(
        f"POSTGRES_SSLMODE is '{self.POSTGRES_SSLMODE}'. "
        "Production should use 'require' or 'verify-full' for encrypted connections."
    )
```

**Step 3: Pass sslmode in db_adapters.py**

In `storage/db_adapters.py`, add to the `ThreadedConnectionPool` constructor (~line 253):

```python
sslmode=system_config.POSTGRES_SSLMODE,
```

**Step 4: Pass sslmode in connection_pool.py**

In `storage/connection_pool.py`, add to `connection_params` dict (~line 77):

```python
'sslmode': system_config.POSTGRES_SSLMODE,
```

**Step 5: Add to .env.production.example**

Add after POSTGRES_PASSWORD:
```
# SSL mode for PostgreSQL connections (use 'require' or 'verify-full' in production)
POSTGRES_SSLMODE=require
```

**Step 6: Run tests**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`
Expected: Pass (SQLite tests don't exercise PostgreSQL code paths).

**Step 7: Commit**

```bash
git add config.py storage/db_adapters.py storage/connection_pool.py .env.production.example
git commit -m "fix: add PostgreSQL SSL mode support with production validation"
```

---

## Task 8: Fix health endpoint info leakage

`/health/ready` returns `str(e)` which can leak DB connection strings and internal paths.

**Files:**
- Modify: `api/server.py:870-884`

**Step 1: Replace str(e) with generic messages**

Change both error handlers in the `/health/ready` endpoint:

```python
except DB_ERRORS as e:
    logger.error(f"Database not ready: {e}")
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "error": "database unavailable"}
    )
except Exception as e:
    logger.exception(f"Unexpected error in readiness check: {e}")
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "error": "service unavailable"}
    )
```

**Step 2: Commit**

```bash
git add api/server.py
git commit -m "fix: remove error details from unauthenticated health endpoint"
```

---

## Task 9: Add server_tokens off to Nginx configs

All Nginx configs leak the server version.

**Files:**
- Modify: `nginx.conf` (add to http block)
- Modify: `enterprise/nginx/nginx.conf` (add to http block)
- Modify: `docker/compose/ollama/nginx.conf` (add to http block, if applicable)

**Step 1: Add server_tokens off**

In `nginx.conf`, add inside the `http {` block (after line 11):
```nginx
server_tokens off;
```

In `enterprise/nginx/nginx.conf`, add inside the `http {` block:
```nginx
server_tokens off;
```

In `docker/compose/ollama/nginx.conf`, add inside the `http {` block if one exists, or the `server {` block:
```nginx
server_tokens off;
```

**Step 2: Commit**

```bash
git add nginx.conf enterprise/nginx/nginx.conf docker/compose/ollama/nginx.conf
git commit -m "fix: disable nginx server version disclosure"
```

---

## Task 10: Expand production config validation

Production validation misses `CSRF_COOKIE_SECURE`, `ENCRYPT_CONVERSATIONS`, and the rate limiter fail-open behavior. Also set `ENCRYPT_CONVERSATIONS` to `True` for production.

**Files:**
- Modify: `config.py` (validate_production_security method, ~lines 241-354)
- Modify: `.env.production.example` (add CSRF_COOKIE_SECURE)

**Step 1: Add missing checks to validate_production_security()**

In `config.py`, inside `validate_production_security()`, add before the return statement:

```python
# CSRF cookie must be secure in production
csrf_config = self.CSRF_CONFIG if hasattr(self, 'CSRF_CONFIG') else {}
if not csrf_config.get('csrf_cookie_secure', False):
    warnings.append(
        "CSRF_COOKIE_SECURE is not enabled. Set CSRF_COOKIE_SECURE=true "
        "for production to prevent CSRF cookie transmission over HTTP."
    )
```

**Step 2: Add CSRF_COOKIE_SECURE=true to .env.production.example**

Add in the Security section:
```
# CSRF cookie must be sent only over HTTPS
CSRF_COOKIE_SECURE=true
```

**Step 3: Run tests**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`

**Step 4: Commit**

```bash
git add config.py .env.production.example
git commit -m "fix: expand production validation to check CSRF cookie security"
```

---

## Task 11: Update pinned dependencies

FastAPI 0.104.1, Starlette 0.27.0, aiohttp 3.9.1, and uvicorn 0.22.0 have known CVEs. Starlette 0.27.0 has CVE-2024-24762 (multipart DoS).

**Files:**
- Modify: `requirements.txt`

**Step 1: Update versions**

Key changes:
- `fastapi==0.104.1` → `fastapi>=0.115.0,<1.0.0` (latest 0.115.x is safe, avoids 0.128+ breaking changes for now)
- **Remove** the standalone `starlette==0.27.0` pin — let FastAPI manage its Starlette dependency
- `aiohttp==3.9.1` → `aiohttp>=3.11.0,<4.0.0`
- `uvicorn[standard]==0.22.0` → `uvicorn[standard]>=0.32.0,<1.0.0`
- `sentry-sdk==1.39.2` → `sentry-sdk>=1.45.0,<2.0.0` (stay on 1.x — the 2.x migration requires significant code changes and should be a separate task)

Important: Do NOT update to FastAPI 0.128+ yet — it removes Pydantic v1 compat and may have breaking changes to `strict_content_type`. The 0.115.x line is the safe upgrade target that fixes security issues without breaking changes.

**Step 2: Recreate venv and install**

```bash
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

**Step 3: Run full test suite**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -20`
Expected: All 425 tests pass. If failures occur, investigate and adjust version pins.

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "fix: update FastAPI, aiohttp, uvicorn to patch known CVEs"
```

---

## Task 12: Fix rate limiter to fail closed for auth endpoints

The rate limiter returns `True` (allow) on Redis errors. For auth endpoints specifically, this should fail closed to prevent brute-force during Redis outages.

**Files:**
- Modify: `api/middleware/auth.py:574-577`

**Step 1: Add fail-closed option to rate limiter**

In `api/middleware/auth.py`, modify the `_check_redis_rate_limit` method to accept a `fail_closed` parameter:

```python
async def _check_redis_rate_limit(self, key: str, max_requests: int,
                                   window_seconds: int, fail_closed: bool = False) -> bool:
    ...
    except RedisError as e:
        logger.error(f"Redis error during rate limit check: {e}")
        if fail_closed:
            logger.warning("Rate limiter failing closed — request denied for safety")
            return False
        return True
```

Then find where auth endpoints call the rate limiter and pass `fail_closed=True`. This requires tracing the call path from the auth rate limit zone.

**Step 2: Run tests**

Run: `source venv/bin/activate && pytest tests/ -m "not integration" --no-header -x 2>&1 | tail -10`

**Step 3: Commit**

```bash
git add api/middleware/auth.py
git commit -m "fix: rate limiter fails closed for auth endpoints during Redis outage"
```

---

## Out of Scope (Infrastructure / DevOps — separate tracks)

These findings require infrastructure changes, not application code:

- **K8s security contexts** (enterprise/k8s/*.yaml) — add `securityContext` with `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`
- **K8s network policies** — create NetworkPolicy resources to restrict pod-to-pod communication
- **K8s namespace pod security** — add `pod-security.kubernetes.io/enforce: restricted` label
- **K8s Redis probe fix** — add `-a ${REDIS_PASSWORD}` to liveness/readiness probe commands
- **K8s Ollama persistent volumes** — replace `emptyDir` with PVC
- **K8s image pinning** — replace `:latest` with specific versions
- **Docker Compose resource limits** — add `deploy.resources.limits` to all services
- **ELK stack security** — enable `xpack.security.enabled: true`
- **Container image signing / SBOM** — integrate cosign + syft in CI
- **Sentry SDK 2.x migration** — major version upgrade requiring code changes

These should be tracked as separate issues/tasks.
