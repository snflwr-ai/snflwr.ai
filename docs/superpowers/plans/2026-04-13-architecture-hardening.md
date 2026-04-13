# Architecture Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden three operational weaknesses — API key rotation, rate limiting resilience, and Stage 4 classifier recovery — so degradation is visible, recoverable, and safe-by-default.

**Architecture:** Dual-key overlap for zero-downtime key rotation. SQLite-backed rate limiter for home mode with fail-closed Redis in production. State machine for the semantic classifier with background health probing and email alerting on state transitions.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, SQLite, Redis, SMTP (existing email_service)

**Spec:** `docs/superpowers/specs/2026-04-13-architecture-hardening-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `config.py` | New env vars for key rotation; reject insecure default in prod |
| `api/middleware/auth.py` | Dual-key auth check; `SqliteRateLimiter`; fail-closed Redis |
| `api/server.py` | Key rotation age check background task; classifier probe startup |
| `safety/pipeline.py` | Classifier state machine; background health probe; alerting |
| `core/email_service.py` | New `send_operator_alert()` method for admin notifications |
| `frontend/open-webui/backend/open_webui/middleware/snflwr.py` | Remove hardcoded default key |
| `tests/test_architecture_hardening.py` | All unit tests for this feature set |
| `tests/test_middleware_integration.py` | Dual-key + rate limiter integration tests |
| `tests/test_e2e_real_stack.py` | Health endpoint field validation |

---

### Task 1: Operator Alert Method in EmailService

**Files:**
- Modify: `core/email_service.py:892` (before the global instance)
- Test: `tests/test_architecture_hardening.py` (new file)

This is a prerequisite — all three features need a way to email the operator.

- [ ] **Step 1: Write the failing test**

Create `tests/test_architecture_hardening.py`:

```python
"""Tests for architecture hardening: key rotation, rate limiting, classifier recovery."""

import pytest
from unittest.mock import patch, MagicMock


class TestOperatorAlert:
    """Operator alert email delivery."""

    @patch("core.email_service.EmailService._send_email", return_value=(True, None))
    def test_send_operator_alert_delivers_email(self, mock_send):
        from core.email_service import email_service

        with patch("core.email_service.system_config") as cfg:
            cfg.ADMIN_EMAIL = "admin@school.edu"
            cfg.SMTP_FROM_NAME = "snflwr.ai"
            cfg.SMTP_FROM_EMAIL = "noreply@snflwr.ai"
            result, err = email_service.send_operator_alert(
                subject="Test alert",
                description="Something happened",
            )
        assert result is True
        assert err is None
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[1]["to_email"] == "admin@school.edu"
        assert "Test alert" in call_args[1]["subject"]

    def test_send_operator_alert_skips_when_no_admin_email(self):
        from core.email_service import email_service

        with patch("core.email_service.system_config") as cfg:
            cfg.ADMIN_EMAIL = ""
            result, err = email_service.send_operator_alert(
                subject="Test", description="test"
            )
        assert result is True  # Not a failure, just skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestOperatorAlert -v`
Expected: FAIL — `AttributeError: 'EmailService' object has no attribute 'send_operator_alert'`

- [ ] **Step 3: Implement send_operator_alert**

Add to `core/email_service.py` before line 891 (before `email_service = EmailService()`):

```python
    def send_operator_alert(
        self,
        subject: str,
        description: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Send operational alert to the admin/operator.

        Uses ADMIN_EMAIL from config. Skips silently if not configured
        or SMTP is not enabled — the caller always logs regardless.
        """
        admin_email = getattr(system_config, "ADMIN_EMAIL", "")
        if not admin_email:
            logger.info("No ADMIN_EMAIL configured — operator alert skipped")
            return True, None

        if not self.enabled:
            logger.warning(
                "SMTP not configured — operator alert not sent (logged only)"
            )
            return True, None

        html_body = (
            f"<h2>snflwr.ai Operator Alert</h2>"
            f"<p><strong>{html_escape(subject)}</strong></p>"
            f"<p>{html_escape(description)}</p>"
            f"<p><small>This is an automated alert from the snflwr.ai system.</small></p>"
        )

        return self._send_email(
            to_email=admin_email,
            subject=f"[snflwr.ai] {subject}",
            html_body=html_body,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestOperatorAlert -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/email_service.py tests/test_architecture_hardening.py
git commit -m "feat: add send_operator_alert method to EmailService"
```

---

### Task 2: Config — New Key Rotation Environment Variables

**Files:**
- Modify: `config.py:630-638` (INTERNAL_API_KEY section)
- Modify: `config.py:408-425` (production validation)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
import os
from datetime import datetime, timezone, timedelta


class TestKeyRotationConfig:
    """INTERNAL_API_KEY rotation config validation."""

    def test_previous_key_defaults_to_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERNAL_API_KEY_PREVIOUS", None)
            # Re-import to pick up env
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_PREVIOUS is None

    def test_previous_key_reads_from_env(self):
        with patch.dict(
            os.environ, {"INTERNAL_API_KEY_PREVIOUS": "old-key-abc123"}, clear=False
        ):
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_PREVIOUS == "old-key-abc123"

    def test_max_age_days_defaults_to_90(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERNAL_API_KEY_MAX_AGE_DAYS", None)
            import importlib
            import config as _cfg
            importlib.reload(_cfg)
            assert _cfg.INTERNAL_API_KEY_MAX_AGE_DAYS == 90

    def test_insecure_default_rejected_in_prod(self):
        """Production validation must reject snflwr-internal-dev-key."""
        from config import ProductionConfigValidator
        with patch.dict(
            os.environ,
            {"INTERNAL_API_KEY": "snflwr-internal-dev-key", "SNFLWR_ENV": "production"},
            clear=False,
        ):
            validator = ProductionConfigValidator()
            errors, _warnings = validator.validate()
            assert any("insecure" in e.lower() for e in errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestKeyRotationConfig -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'INTERNAL_API_KEY_PREVIOUS'`

- [ ] **Step 3: Add config variables**

In `config.py`, after line 638 (after the INTERNAL_API_KEY block), add:

```python
INTERNAL_API_KEY_PREVIOUS: Optional[str] = os.getenv("INTERNAL_API_KEY_PREVIOUS")

INTERNAL_API_KEY_MAX_AGE_DAYS: int = int(
    os.getenv("INTERNAL_API_KEY_MAX_AGE_DAYS", "90")
)

_created_at_raw = os.getenv("INTERNAL_API_KEY_CREATED_AT")
INTERNAL_API_KEY_CREATED_AT: Optional[datetime] = (
    datetime.fromisoformat(_created_at_raw) if _created_at_raw else None
)
```

Ensure `from datetime import datetime` is present at top of file (it already is at line 15).

The production validation at lines 408-425 already rejects `snflwr-internal-dev-key` — no change needed there.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestKeyRotationConfig -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_architecture_hardening.py
git commit -m "feat: add INTERNAL_API_KEY rotation config vars"
```

---

### Task 3: Auth Middleware — Dual-Key Check

**Files:**
- Modify: `api/middleware/auth.py:63-72` (hmac.compare_digest block)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
class TestDualKeyAuth:
    """Dual-key authentication in auth middleware."""

    @patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary")
    @patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", None)
    def test_primary_key_authenticates(self):
        from api.middleware.auth import authenticate_request
        from unittest.mock import AsyncMock
        import asyncio

        request = MagicMock()
        request.headers = {"Authorization": "Bearer new-key-primary"}
        result = asyncio.get_event_loop().run_until_complete(
            authenticate_request(request)
        )
        assert result is not None
        assert result.user_id == "internal_service"

    @patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary")
    @patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", "old-key-previous")
    def test_previous_key_authenticates_during_rotation(self):
        from api.middleware.auth import authenticate_request
        import asyncio

        request = MagicMock()
        request.headers = {"Authorization": "Bearer old-key-previous"}
        result = asyncio.get_event_loop().run_until_complete(
            authenticate_request(request)
        )
        assert result is not None
        assert result.user_id == "internal_service"

    @patch("api.middleware.auth.INTERNAL_API_KEY", "new-key-primary")
    @patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", "old-key-previous")
    def test_random_key_rejected(self):
        from api.middleware.auth import authenticate_request
        import asyncio

        request = MagicMock()
        request.headers = {"Authorization": "Bearer totally-wrong-key"}
        result = asyncio.get_event_loop().run_until_complete(
            authenticate_request(request)
        )
        # Should fall through to JWT/session auth — not return internal_service
        assert result is None or result.user_id != "internal_service"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestDualKeyAuth -v`
Expected: FAIL — `INTERNAL_API_KEY_PREVIOUS` not importable from auth module

- [ ] **Step 3: Implement dual-key check**

In `api/middleware/auth.py`, add the import near the top (with other config imports):

```python
from config import INTERNAL_API_KEY, INTERNAL_API_KEY_PREVIOUS
```

Replace lines 63-72 (the single hmac.compare_digest block) with:

```python
    # Check for internal API key (server-to-server calls from Open WebUI)
    # Use constant-time comparison to prevent timing side-channel attacks.
    # Accept both current and previous key for zero-downtime rotation.
    if hmac.compare_digest(token, INTERNAL_API_KEY):
        logger.info("Authenticated via internal API key (Open WebUI middleware)")
        return AuthSession(
            user_id="internal_service",
            role="admin",
            session_token=token,
            email="internal@snflwr.ai",
        )
    if INTERNAL_API_KEY_PREVIOUS and hmac.compare_digest(
        token, INTERNAL_API_KEY_PREVIOUS
    ):
        logger.warning(
            "Request authenticated with previous API key "
            "-- rotation in progress or stale config"
        )
        return AuthSession(
            user_id="internal_service",
            role="admin",
            session_token=token,
            email="internal@snflwr.ai",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestDualKeyAuth -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add api/middleware/auth.py tests/test_architecture_hardening.py
git commit -m "feat: dual-key auth for zero-downtime API key rotation"
```

---

### Task 4: Key Rotation Age Check Background Task

**Files:**
- Modify: `api/server.py:129-336` (lifespan handler)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
from unittest.mock import AsyncMock


class TestKeyRotationAgeCheck:
    """Background task warns when API key is overdue for rotation."""

    @patch("core.email_service.email_service.send_operator_alert")
    def test_warns_when_key_overdue(self, mock_alert):
        mock_alert.return_value = (True, None)
        from api.server import check_key_rotation_age

        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        with patch("api.server.INTERNAL_API_KEY_CREATED_AT", old_date), \
             patch("api.server.INTERNAL_API_KEY_MAX_AGE_DAYS", 90):
            import asyncio
            asyncio.get_event_loop().run_until_complete(check_key_rotation_age())

        mock_alert.assert_called_once()
        call_args_str = str(mock_alert.call_args)
        assert "100" in call_args_str

    @patch("core.email_service.email_service.send_operator_alert")
    def test_no_warning_when_key_fresh(self, mock_alert):
        from api.server import check_key_rotation_age

        recent_date = datetime.now(timezone.utc) - timedelta(days=10)
        with patch("api.server.INTERNAL_API_KEY_CREATED_AT", recent_date), \
             patch("api.server.INTERNAL_API_KEY_MAX_AGE_DAYS", 90):
            import asyncio
            asyncio.get_event_loop().run_until_complete(check_key_rotation_age())

        mock_alert.assert_not_called()

    @patch("core.email_service.email_service.send_operator_alert")
    def test_no_warning_when_created_at_not_set(self, mock_alert):
        from api.server import check_key_rotation_age

        with patch("api.server.INTERNAL_API_KEY_CREATED_AT", None):
            import asyncio
            asyncio.get_event_loop().run_until_complete(check_key_rotation_age())

        mock_alert.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestKeyRotationAgeCheck -v`
Expected: FAIL — `cannot import name 'check_key_rotation_age' from 'api.server'`

- [ ] **Step 3: Implement the age check function and wire it into lifespan**

Add to `api/server.py` imports (near top):

```python
from config import (
    INTERNAL_API_KEY_CREATED_AT,
    INTERNAL_API_KEY_MAX_AGE_DAYS,
)
```

Add the function before the lifespan handler:

```python
async def check_key_rotation_age() -> None:
    """Warn operator if INTERNAL_API_KEY is overdue for rotation."""
    if INTERNAL_API_KEY_CREATED_AT is None:
        logger.info(
            "INTERNAL_API_KEY_CREATED_AT not set — "
            "set it to enable key rotation age warnings."
        )
        return

    age = datetime.now(timezone.utc) - INTERNAL_API_KEY_CREATED_AT
    age_days = age.days
    if age_days > INTERNAL_API_KEY_MAX_AGE_DAYS:
        msg = (
            f"Internal API key is {age_days} days old "
            f"(max recommended: {INTERNAL_API_KEY_MAX_AGE_DAYS}). "
            f"Rotate it with: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
        logger.warning(msg)
        from core.email_service import email_service
        email_service.send_operator_alert(
            subject="API key rotation overdue",
            description=msg,
        )
```

Inside the lifespan handler, after the existing startup logic completes (before `yield`), add:

```python
    # Check API key rotation age
    await check_key_rotation_age()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestKeyRotationAgeCheck -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add api/server.py tests/test_architecture_hardening.py
git commit -m "feat: background check warns when API key rotation is overdue"
```

---

### Task 5: Remove Hardcoded Default Key from OWU Middleware

**Files:**
- Modify: `frontend/open-webui/backend/open_webui/middleware/snflwr.py:19`
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_architecture_hardening.py`:

```python
class TestOWUMiddlewareKeyDefault:
    """OWU middleware must not use a hardcoded insecure default key."""

    def test_no_hardcoded_default_key(self):
        with open(
            "frontend/open-webui/backend/open_webui/middleware/snflwr.py"
        ) as f:
            content = f.read()

        # Must not contain the insecure default
        assert "snflwr-internal-dev-key" not in content, (
            "Hardcoded insecure default key still present in OWU middleware"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestOWUMiddlewareKeyDefault -v`
Expected: FAIL — `AssertionError: Hardcoded insecure default key still present`

- [ ] **Step 3: Replace the hardcoded default**

In `frontend/open-webui/backend/open_webui/middleware/snflwr.py`, replace line 19:

```python
SNFLWR_INTERNAL_KEY = os.getenv("INTERNAL_API_KEY", "snflwr-internal-dev-key")
```

with:

```python
SNFLWR_INTERNAL_KEY = os.getenv("INTERNAL_API_KEY", "")
if not SNFLWR_INTERNAL_KEY:
    raise RuntimeError(
        "INTERNAL_API_KEY environment variable is required. "
        "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestOWUMiddlewareKeyDefault -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/open-webui/backend/open_webui/middleware/snflwr.py tests/test_architecture_hardening.py
git commit -m "fix(security): remove hardcoded insecure default from OWU middleware"
```

---

### Task 6: SQLite Rate Limiter for Home Mode

**Files:**
- Modify: `api/middleware/auth.py` (add `SqliteRateLimiter` class, wire into init)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
import tempfile
import os as _os


class TestSqliteRateLimiter:
    """SQLite-backed rate limiter for home mode."""

    def test_enforces_limit(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _os.path.join(tmp, "test.db")
            limiter = SqliteRateLimiter(db_path)
            # 3 requests allowed in 60s window
            for _ in range(3):
                assert limiter.check("user1", "default", 3, 60) is True
            # 4th should be blocked
            assert limiter.check("user1", "default", 3, 60) is False

    def test_persists_across_instances(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _os.path.join(tmp, "test.db")
            limiter1 = SqliteRateLimiter(db_path)
            for _ in range(3):
                limiter1.check("user1", "default", 3, 60)

            # New instance, same DB — should still be at limit
            limiter2 = SqliteRateLimiter(db_path)
            assert limiter2.check("user1", "default", 3, 60) is False

    def test_cleans_expired_entries(self):
        from api.middleware.auth import SqliteRateLimiter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = _os.path.join(tmp, "test.db")
            limiter = SqliteRateLimiter(db_path)

            # Fill up the limit
            for _ in range(3):
                limiter.check("user1", "default", 3, 1)  # 1-second window

            import time
            time.sleep(1.1)

            # After window expires, should be allowed again
            assert limiter.check("user1", "default", 3, 1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestSqliteRateLimiter -v`
Expected: FAIL — `ImportError: cannot import name 'SqliteRateLimiter'`

- [ ] **Step 3: Implement SqliteRateLimiter**

Add to `api/middleware/auth.py`, before the `RedisRateLimiter` class:

```python
import sqlite3


class SqliteRateLimiter:
    """Persistent sliding-window rate limiter backed by SQLite.

    Designed for home/single-instance deployments where Redis is not available.
    Survives container restarts, unlike the in-memory fallback.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS rate_limits "
            "(key TEXT NOT NULL, timestamp REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limits_key "
            "ON rate_limits (key)"
        )
        conn.commit()
        conn.close()

    def check(
        self, key: str, limit_type: str, max_requests: int, window_seconds: int
    ) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        import time

        now = time.time()
        cutoff = now - window_seconds
        cache_key = f"{limit_type}:{key}"

        conn = sqlite3.connect(self._db_path)
        try:
            # Clean expired entries
            conn.execute(
                "DELETE FROM rate_limits WHERE key = ? AND timestamp < ?",
                (cache_key, cutoff),
            )
            # Count current window
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_limits WHERE key = ? AND timestamp >= ?",
                (cache_key, cutoff),
            ).fetchone()
            count = row[0] if row else 0

            if count >= max_requests:
                conn.commit()
                return False

            conn.execute(
                "INSERT INTO rate_limits (key, timestamp) VALUES (?, ?)",
                (cache_key, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestSqliteRateLimiter -v`
Expected: 3 passed

- [ ] **Step 5: Wire SqliteRateLimiter into RedisRateLimiter init**

In `RedisRateLimiter.__init__` (line 544), after `self._initialize_redis()`, add logic to use SQLite when Redis is disabled:

```python
    # After self._initialize_redis()
    self._sqlite_limiter = None
    if not self._redis:
        from config import DATA_DIR
        import os
        db_path = os.path.join(DATA_DIR, "snflwr.db")
        try:
            self._sqlite_limiter = SqliteRateLimiter(db_path)
            logger.warning(
                "Rate limiter using SQLite fallback (single-instance only). "
                "Enable Redis for distributed rate limiting."
            )
        except Exception as exc:
            logger.error("Failed to init SQLite rate limiter: %s", exc)
```

In `_check_fallback_rate_limit` (line 630), add at the top of the method body before the existing in-memory logic:

```python
    if self._sqlite_limiter:
        return self._sqlite_limiter.check(key, limit_type, max_requests, window_seconds)
```

This way if SQLite init failed, the existing in-memory fallback still works.

- [ ] **Step 6: Run full test suite for auth middleware**

Run: `pytest tests/test_architecture_hardening.py tests/test_auth_middleware.py -v`
Expected: All passed

- [ ] **Step 7: Commit**

```bash
git add api/middleware/auth.py tests/test_architecture_hardening.py
git commit -m "feat: SQLite-backed rate limiter for home mode persistence"
```

---

### Task 7: Fail-Closed Redis Rate Limiting in Production

**Files:**
- Modify: `api/middleware/auth.py:625-628` (RedisError catch)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
from unittest.mock import PropertyMock


class TestRedisFailClosed:
    """Production rate limiting must block on Redis errors."""

    @patch("api.middleware.auth.REDIS_ENABLED", True)
    def test_blocks_request_on_redis_error(self):
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter.__new__(RedisRateLimiter)
        limiter.limits = {"default": (100, 60)}
        limiter._redis = MagicMock()
        limiter._redis.pipeline.side_effect = Exception("Connection refused")
        limiter._fallback_requests = {}
        limiter._fallback_lock = MagicMock()
        limiter._sqlite_limiter = None
        limiter._redis_healthy = True
        limiter._redis_alert_sent = False

        # In production with REDIS_ENABLED=True, Redis error should block
        result = limiter._check_redis_rate_limit("user1", "default", 100, 60)
        assert result is False  # Blocked, not allowed

    @patch("api.middleware.auth.REDIS_ENABLED", True)
    def test_redis_recovery_clears_flag(self):
        from api.middleware.auth import RedisRateLimiter

        limiter = RedisRateLimiter.__new__(RedisRateLimiter)
        limiter.limits = {"default": (100, 60)}
        limiter._redis_healthy = False
        limiter._redis_alert_sent = True

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [1]  # count=1, under limit
        limiter._redis = MagicMock()
        limiter._redis.pipeline.return_value = mock_pipe

        result = limiter._check_redis_rate_limit("user1", "default", 100, 60)
        assert result is True
        assert limiter._redis_healthy is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestRedisFailClosed -v`
Expected: FAIL — `_redis_healthy` attribute doesn't exist / behavior is fail-open

- [ ] **Step 3: Implement fail-closed Redis error handling**

In `RedisRateLimiter.__init__`, add after `self._fallback_lock`:

```python
        self._redis_healthy = True
        self._redis_alert_sent = False
```

Add import at top of file:

```python
from config import REDIS_ENABLED
```

Replace `_check_redis_rate_limit` method (lines 600-628) with:

```python
    def _check_redis_rate_limit(
        self, key: str, limit_type: str, max_requests: int, window_seconds: int
    ) -> bool:
        """Redis-based rate limiting with atomic increment."""
        try:
            redis_key = f"snflwr:ratelimit:{limit_type}:{key}"
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds)
            results = pipe.execute()
            current_count = results[0]

            # Redis recovered — clear unhealthy flag
            if not self._redis_healthy:
                self._redis_healthy = True
                self._redis_alert_sent = False
                logger.info("Redis connection recovered — rate limiting restored")

            if current_count > max_requests:
                logger.warning(
                    "Rate limit exceeded: %s (%s) — %d/%d in %ds",
                    key, limit_type, current_count, max_requests, window_seconds,
                )
                return False
            return True
        except Exception as exc:
            self._redis_healthy = False
            logger.error("Redis error during rate limit check: %s", exc)

            if REDIS_ENABLED:
                # Production: fail closed — block the request
                if not self._redis_alert_sent:
                    self._redis_alert_sent = True
                    try:
                        from core.email_service import email_service
                        email_service.send_operator_alert(
                            subject="Redis rate limiter failure — failing closed",
                            description=(
                                f"Redis is unreachable. Rate limiting is blocking all "
                                f"requests as a safety measure. Error: {exc}"
                            ),
                        )
                    except Exception:
                        pass  # Alert is best-effort; the block is mandatory
                return False
            else:
                # Home mode: fall through to SQLite/in-memory fallback
                return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestRedisFailClosed -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add api/middleware/auth.py tests/test_architecture_hardening.py
git commit -m "fix(security): fail-closed rate limiting on Redis errors in production"
```

---

### Task 8: Health Endpoint — Rate Limiter + Classifier Fields

**Files:**
- Modify: `api/server.py:923-926` (/health endpoint)
- Test: `tests/test_architecture_hardening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_architecture_hardening.py`:

```python
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Health endpoint reports rate limiter and classifier state."""

    def test_health_includes_rate_limiter_field(self):
        from api.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        data = resp.json()
        assert "rate_limiter" in data
        assert data["rate_limiter"] in ("redis", "sqlite", "memory")

    def test_health_includes_classifier_field(self):
        from api.server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        data = resp.json()
        assert "safety_classifier" in data
        assert data["safety_classifier"] in ("available", "degraded", "disabled")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_architecture_hardening.py::TestHealthEndpoint -v`
Expected: FAIL — `"rate_limiter" not in data`

- [ ] **Step 3: Expand the health endpoint**

Replace the health endpoint in `api/server.py` (lines 923-926):

```python
@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    from config import REDIS_ENABLED

    # Determine rate limiter backend
    if REDIS_ENABLED:
        rate_backend = "redis"
    elif hasattr(app.state, "rate_limiter") and getattr(
        app.state.rate_limiter, "_sqlite_limiter", None
    ):
        rate_backend = "sqlite"
    else:
        rate_backend = "memory"

    # Determine classifier state
    classifier_state = "disabled"
    classifier_since = None
    try:
        from safety.pipeline import safety_pipeline
        if hasattr(safety_pipeline, "_classifier"):
            clf = safety_pipeline._classifier
            classifier_state = getattr(clf, "_state", "disabled")
            classifier_since = getattr(clf, "_state_since", None)
            if classifier_since:
                classifier_since = classifier_since.isoformat()
    except Exception:
        pass

    return {
        "status": "healthy",
        "rate_limiter": rate_backend,
        "rate_limiter_healthy": rate_backend != "redis" or REDIS_ENABLED,
        "safety_classifier": classifier_state,
        "safety_classifier_since": classifier_since,
    }
```

Note: The `_state` and `_state_since` attributes on the classifier will be added in Task 9. For now the health endpoint will fall back to reporting `"disabled"` until Task 9 is complete.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestHealthEndpoint -v`
Expected: 2 passed (classifier will report "disabled" until Task 9)

- [ ] **Step 5: Commit**

```bash
git add api/server.py tests/test_architecture_hardening.py
git commit -m "feat: health endpoint reports rate limiter and classifier state"
```

---

### Task 9: Classifier State Machine + Background Health Probe

**Files:**
- Modify: `safety/pipeline.py:826-925` (_SemanticClassifier class)
- Test: `tests/test_architecture_hardening.py`

This is the largest task — the state machine, background probe, and alerting.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_architecture_hardening.py`:

```python
class TestClassifierStateMachine:
    """Stage 4 classifier state machine and recovery."""

    def test_initial_state_disabled_when_ollama_unreachable(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._available = False
        clf._client = None
        clf._state = "disabled"
        assert clf._state == "disabled"

    def test_classify_returns_none_when_degraded(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._available = False
        clf._client = None
        clf._state = "degraded"
        result = clf.classify("test input")
        assert result is None

    def test_classify_blocks_and_transitions_on_error_while_available(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._available = True
        clf._state = "available"
        clf._model = "test-model"
        clf._state_since = datetime.now(timezone.utc)

        mock_client = MagicMock()
        mock_client.generate.side_effect = Exception("connection lost")
        clf._client = mock_client
        clf._OllamaError = Exception

        with patch.object(clf, "_transition_state") as mock_transition:
            result = clf.classify("test input")
            assert result is not None  # Should BLOCK
            assert result.is_safe is False
            mock_transition.assert_called_with("degraded")

    @patch("core.email_service.email_service.send_operator_alert")
    def test_transition_to_degraded_sends_alert(self, mock_alert):
        mock_alert.return_value = (True, None)
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "available"
        clf._state_since = datetime.now(timezone.utc)
        clf._available = True

        clf._transition_state("degraded")
        assert clf._state == "degraded"
        assert clf._available is False
        mock_alert.assert_called_once()

    @patch("core.email_service.email_service.send_operator_alert")
    def test_transition_to_available_sends_recovery_alert(self, mock_alert):
        mock_alert.return_value = (True, None)
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "degraded"
        clf._state_since = datetime.now(timezone.utc)
        clf._available = False
        clf._model = "test-model"

        clf._transition_state("available")
        assert clf._state == "available"
        assert clf._available is True
        mock_alert.assert_called_once()
        call_str = str(mock_alert.call_args)
        assert "recover" in call_str.lower()

    def test_probe_ollama_returns_true_when_model_available(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._model = "llama-guard3:8b"

        mock_client = MagicMock()
        mock_client.check_connection.return_value = (True, "0.1")
        mock_client.list_models.return_value = (
            True,
            [{"name": "llama-guard3:8b"}],
            None,
        )
        clf._client = mock_client

        assert clf._probe_ollama() is True

    def test_probe_ollama_returns_false_when_unreachable(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._model = "llama-guard3:8b"

        mock_client = MagicMock()
        mock_client.check_connection.return_value = (False, None)
        clf._client = mock_client

        assert clf._probe_ollama() is False

    def test_health_state_exposed(self):
        from safety.pipeline import _SemanticClassifier
        clf = _SemanticClassifier.__new__(_SemanticClassifier)
        clf._state = "available"
        clf._state_since = datetime(2026, 4, 13, tzinfo=timezone.utc)
        assert clf._state == "available"
        assert clf._state_since.year == 2026
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_architecture_hardening.py::TestClassifierStateMachine -v`
Expected: FAIL — `_transition_state` not found, `_probe_ollama` not found

- [ ] **Step 3: Implement state machine and probe**

In `safety/pipeline.py`, rewrite the `_SemanticClassifier` class `__init__` (lines 826-883). Replace the entire method:

```python
    def __init__(self) -> None:
        self._available = False
        self._model: Optional[str] = None
        self._client = None
        self._state = "disabled"  # "available", "degraded", "disabled"
        self._state_since = datetime.now(timezone.utc)
        self._probe_task: Optional[asyncio.Task] = None

        try:
            from utils.ollama_client import (
                OllamaClient as _OllamaClient,
                OllamaError as _OE,
            )

            self._client = _OllamaClient(timeout=45, max_retries=1)
            self._OllamaError = _OE

            ok, _version = self._client.check_connection()
            if not ok:
                logger.warning(
                    "Ollama not reachable at init; semantic classifier disabled."
                )
                return

            self._model = self._find_model()
            if self._model:
                self._transition_state("available")
                logger.info("Semantic classifier ready (model=%s)", self._model)
            else:
                logger.warning(
                    "No suitable safety model found; semantic classifier disabled."
                )
        except ImportError:
            logger.warning(
                "ollama_client not available; semantic classifier disabled."
            )
        except Exception as exc:
            logger.warning("Semantic classifier init failed: %s", exc)
```

Add these new methods to the class:

```python
    def _find_model(self) -> Optional[str]:
        """Find the best available safety model from preferred + fallbacks."""
        preferred = getattr(safety_config, "SAFETY_MODEL", "llama-guard3:8b")
        fallbacks = getattr(
            safety_config, "SAFETY_MODEL_FALLBACKS", ["llama-guard3:1b"]
        )
        success, models, _err = self._client.list_models()
        if success and models:
            names = [m.get("name", "") for m in models]
            if preferred in names:
                return preferred
            for fb in fallbacks:
                if fb in names:
                    return fb
        return None

    def _transition_state(self, new_state: str) -> None:
        """Transition classifier state with logging and alerting."""
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state
        self._state_since = datetime.now(timezone.utc)

        if new_state == "available":
            self._available = True
            logger.info(
                "Safety classifier state: %s -> available", old_state
            )
            try:
                from core.email_service import email_service
                email_service.send_operator_alert(
                    subject="Safety classifier recovered",
                    description=(
                        f"Semantic classification re-enabled (was {old_state}). "
                        f"Model: {self._model}"
                    ),
                )
            except Exception:
                pass  # Alert is best-effort
        else:
            self._available = False
            logger.warning(
                "Safety classifier state: %s -> %s", old_state, new_state
            )
            if old_state == "available":
                try:
                    from core.email_service import email_service
                    email_service.send_operator_alert(
                        subject="Safety classifier degraded",
                        description=(
                            "Semantic classifier lost Ollama connection. "
                            "Deterministic safety stages (1-3, 5) still protecting. "
                            "Auto-recovery probing every 60s."
                        ),
                    )
                except Exception:
                    pass

    def _probe_ollama(self) -> bool:
        """Lightweight health check: is Ollama reachable and does the model exist?"""
        if self._client is None:
            return False
        try:
            ok, _version = self._client.check_connection()
            if not ok:
                return False
            model = self._find_model()
            if model:
                self._model = model
                return True
            return False
        except Exception:
            return False

    async def run_health_probe(self) -> None:
        """Background task: probe Ollama periodically, auto-recover."""
        while True:
            try:
                interval = 60 if self._state in ("degraded", "disabled") else 300
                await asyncio.sleep(interval)

                healthy = await asyncio.get_event_loop().run_in_executor(
                    None, self._probe_ollama
                )
                if healthy and self._state != "available":
                    self._transition_state("available")
                elif not healthy and self._state == "available":
                    self._transition_state("degraded")
                elif not healthy:
                    logger.debug(
                        "Classifier probe: still %s", self._state
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Classifier probe error: %s", exc)
```

Update the `classify` method (lines 887-925) — add `self._transition_state("degraded")` in both error paths:

```python
    def classify(self, text: str, age: Optional[int] = None) -> Optional[SafetyResult]:
        """Classify text via the Ollama safety model."""
        if not self._available or self._client is None:
            return None  # skip -- deterministic stages still protect

        try:
            prompt = self._build_prompt(text, age)
            success, response, _meta = self._client.generate(
                model=self._model,
                prompt=prompt,
                options={"temperature": 0.0, "num_predict": 250},
            )

            if not success or response is None:
                logger.error("Ollama generation failed; failing closed.")
                self._transition_state("degraded")
                return _block(
                    Severity.MAJOR,
                    Category.CLASSIFIER_ERROR,
                    "Semantic classifier generation failed (fail closed).",
                    stage="classifier",
                )

            return self._parse_response(response)

        except Exception as exc:
            logger.error(
                "Stage 4 (classifier) error, failing closed: %s",
                exc,
                exc_info=True,
            )
            self._transition_state("degraded")
            return _block(
                Severity.MAJOR,
                Category.CLASSIFIER_ERROR,
                "Semantic classifier error (fail closed).",
                stage="classifier",
            )
```

Add `import asyncio` near the top of `safety/pipeline.py` if not already present. Ensure `from datetime import datetime, timezone` is imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_architecture_hardening.py::TestClassifierStateMachine -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add safety/pipeline.py tests/test_architecture_hardening.py
git commit -m "feat: classifier state machine with background probe and alerting"
```

---

### Task 10: Start the Background Probe at Server Startup

**Files:**
- Modify: `api/server.py` (lifespan handler)

- [ ] **Step 1: Wire the probe into the lifespan handler**

In `api/server.py`, inside the lifespan handler, after the key rotation check and before `yield`, add:

```python
    # Start classifier health probe
    classifier_probe_task = None
    try:
        from safety.pipeline import safety_pipeline
        if hasattr(safety_pipeline, "_classifier"):
            clf = safety_pipeline._classifier
            classifier_probe_task = asyncio.create_task(clf.run_health_probe())
            logger.info(
                "Classifier health probe started (state=%s)", clf._state
            )
    except Exception as exc:
        logger.warning("Could not start classifier health probe: %s", exc)
```

After `yield` (in the shutdown section), add:

```python
    # Cancel classifier probe
    if classifier_probe_task:
        classifier_probe_task.cancel()
        try:
            await classifier_probe_task
        except asyncio.CancelledError:
            pass
        logger.info("Classifier health probe stopped")
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/test_architecture_hardening.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add api/server.py
git commit -m "feat: start classifier health probe at server startup"
```

---

### Task 11: Integration Tests

**Files:**
- Modify: `tests/test_middleware_integration.py`

- [ ] **Step 1: Add integration tests**

Append to `tests/test_middleware_integration.py`:

```python
class TestDualKeyIntegration:
    """Integration: dual-key auth during rotation window."""

    @patch("open_webui.middleware.snflwr.SNFLWR_INTERNAL_KEY", "old-rotation-key")
    @patch("open_webui.middleware.snflwr.SNFLWR_API_URL", "http://localhost:39150")
    def test_previous_key_accepted_during_rotation(self):
        """Middleware using old key should still authenticate after primary key rotates."""
        with patch("api.middleware.auth.INTERNAL_API_KEY", "new-primary-key"), \
             patch("api.middleware.auth.INTERNAL_API_KEY_PREVIOUS", "old-rotation-key"):
            from api.middleware.auth import authenticate_request
            import asyncio

            request = MagicMock()
            request.headers = {"Authorization": "Bearer old-rotation-key"}
            result = asyncio.get_event_loop().run_until_complete(
                authenticate_request(request)
            )
            assert result is not None
            assert result.user_id == "internal_service"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_middleware_integration.py::TestDualKeyIntegration -v`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_middleware_integration.py
git commit -m "test: integration test for dual-key auth rotation"
```

---

### Task 12: E2E Health Endpoint Test

**Files:**
- Modify: `tests/test_e2e_real_stack.py`

- [ ] **Step 1: Add E2E test for health fields**

Add a new test class to `tests/test_e2e_real_stack.py`:

```python
class TestHealthEndpoint:
    """Health endpoint reports operational state."""

    def test_health_includes_operational_fields(self, stack):
        """Health endpoint must report rate_limiter and safety_classifier."""
        import subprocess
        import json

        result = subprocess.run(
            [
                "docker", "exec", SNFLWR_CONTAINER,
                "python", "-c",
                "import urllib.request, json; "
                "r = urllib.request.urlopen('http://localhost:39150/health'); "
                "print(json.dumps(json.loads(r.read())))"
            ],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Health check failed: {result.stderr}"
        data = json.loads(result.stdout.strip())
        assert "rate_limiter" in data
        assert data["rate_limiter"] in ("redis", "sqlite", "memory")
        assert "safety_classifier" in data
        assert data["safety_classifier"] in ("available", "degraded", "disabled")
```

- [ ] **Step 2: Run test (requires live stack)**

Run: `pytest tests/test_e2e_real_stack.py::TestHealthEndpoint -m e2e -v -o "addopts="`
Expected: 1 passed (only if stack is running)

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_real_stack.py
git commit -m "test(e2e): verify health endpoint reports operational state"
```

---

### Task 13: Final Validation

- [ ] **Step 1: Run the full CI test suite locally**

```bash
pytest tests/ -m "not e2e" -v --tb=short
```

Expected: All existing + new tests pass, coverage >= 85%

- [ ] **Step 2: Run Black formatting check**

```bash
black --check --diff api/ core/ safety/ storage/ utils/
```

Fix any formatting issues.

- [ ] **Step 3: Run the E2E suite against the live stack (if running)**

```bash
pytest tests/test_e2e_real_stack.py -m e2e -v -o "addopts="
```

- [ ] **Step 4: Final commit if any formatting fixes were needed**

```bash
git add -A
git commit -m "style: fix formatting for architecture hardening"
```
