# Technical Debt & Future Improvements

**Last Updated**: 2026-01-01
**Status**: Enterprise-Ready Improvements Applied

This document tracks remaining known issues from the comprehensive security audit that are deferred as technical debt. These are lower-priority items that don't block production deployment.

---

## ✅ RECENTLY FIXED

### ENTERPRISE: Redis-Backed Rate Limiting ✅ ADDED
**Location**: `api/middleware/auth.py:437-583`
**Issue**: In-memory rate limiter using `defaultdict` didn't work across multiple API instances
**Fix Applied**:
- Replaced `RateLimiter` with `RedisRateLimiter` class
- Uses Redis INCR + EXPIRE for atomic distributed rate limiting
- Falls back to in-memory if Redis unavailable (with warning)
- Supports horizontal scaling of API instances

---

### ENTERPRISE: Redis-Backed Session Cache ✅ ADDED
**Location**: `core/authentication.py:85-163`
**Issue**: In-memory session cache (`_active_sessions` dict) didn't sync across instances
**Fix Applied**:
- Sessions now stored in Redis with configurable TTL (24 hours)
- Added `_get_session_from_cache()`, `_set_session_in_cache()`, `_delete_session_from_cache()` methods
- Falls back to in-memory if Redis unavailable
- Enables horizontal scaling without session sync issues

---

### ENTERPRISE: Comprehensive Health Checks ✅ ADDED
**Location**: `api/server.py:123-282`
**Fix Applied**:
- `/health` - Basic check for load balancers
- `/health/detailed` - Full dependency status (database, Redis, Celery, Ollama)
- `/health/ready` - Kubernetes readiness probe (503 if not ready)
- `/health/live` - Kubernetes liveness probe

---

### ENTERPRISE: Alembic Database Migrations ✅ ADDED
**Location**: `alembic/`, `alembic.ini`
**Fix Applied**:
- Added Alembic configuration for schema migrations
- Supports both SQLite and PostgreSQL
- Run migrations with: `alembic upgrade head`
- Generate new migrations with: `alembic revision -m "description"`

---

### M-7: Audit Log Failures Silently Swallowed ✅ FIXED
**Location**: `api/middleware/auth.py:361-428`
**Original Issue**: Audit log write failures were logged but not alerted
**Fix Applied**:
- Added consecutive failure tracking with `_audit_failure_count`
- After 5 consecutive failures, logs CRITICAL alert
- Sends email notification to admin (if configured)
- Resets counter on successful write

---

### NEW: Encryption Library Startup Check ✅ ADDED
**Location**: `api/server.py:148-164`
**Issue Found**: The encryption fallback in `storage/encryption.py` uses base64 only (no real encryption) when cryptography library is unavailable
**Fix Applied**:
- Added startup validation that cryptography library is available
- Server fails fast with clear error message if cryptography is missing
- Prevents silent degradation to insecure base64-only encoding

---

### M-10: Celery Beat Now in Production Compose ✅ FIXED
**Location**: `docker-compose.yml`, `DEPLOYMENT.md`
**Issue**: Celery Beat was configured but not included in production docker-compose
**Fix Applied**:
- Added `celery-worker` and `celery-beat` services to `docker-compose.yml`
- Added Celery documentation section to `DEPLOYMENT.md`
- Added `ADMIN_EMAIL` to `.env.example` for compliance alerts

---

## 🟡 MEDIUM Priority (Deferred)

---

## 🟢 LOW Priority (Code Quality)

### L-1: Duplicate Configuration Classes
**Location**: `config.py:16-233` (SystemConfig) vs `config.py:478-533` (_SystemConfig)
**Issue**: Two config classes with overlapping functionality
**Impact**: Developer confusion
**Fix**: Consolidate into single class
**Effort**: Low
**Priority**: Low

---

### L-3: TODO Comments in Production Code
**Location**: Multiple files (9+ occurrences)
**Examples**:
- `api/routes/parental_consent.py` - Fax/print-mail TODOs removed (email-only)
- `ui/launcher.py` - Login window TODO resolved
- `ui/parent_dashboard.py:973, 982, 1008` - UI placeholder dialogs

**Fix**: Either implement TODOs or create tickets and remove comments
**Effort**: Varies (some trivial, some feature work)
**Priority**: Low

---

### L-4: Missing Type Hints
**Location**: Throughout codebase
**Example**: `core/key_management.py:379` - `setup_encryption_interactive():`
**Fix**: Add return type annotations to all functions
**Effort**: Medium (requires review of ~100+ functions)
**Priority**: Low (Python is dynamically typed)

---

### L-5: Inconsistent Error Messages
**Location**: API routes
**Issue**: Some error messages say "Please try again or contact support", others just "Failed to X"
**Fix**: Standardize on format: "Failed to {action}. Please try again or contact support."
**Effort**: Low (find/replace)
**Priority**: Very Low (UX polish)

---

### L-6: Magic Numbers Should Be Constants
**Location**: Throughout codebase
**Examples**:
- `ttl=120` (cache TTL) → `CACHE_TTL_SECONDS = 120`
- `timeout=30` (various) → `DEFAULT_TIMEOUT_SECONDS = 30`
- `max_sessions_per_day=5` → `MAX_SESSIONS_PER_DAY = 5`

**Fix**: Extract to named constants in config
**Effort**: Low
**Priority**: Low (code readability)

---

### L-7: Missing Docstrings
**Location**: Various functions lack documentation
**Fix**: Add docstrings to undocumented functions
**Effort**: Medium
**Priority**: Low

---

### L-8: Inconsistent Naming (Questionable)
**Status**: Unable to verify - codebase mostly follows snake_case convention
**Priority**: Very Low / Not a real issue

---

## ⚪ CODE QUALITY (Nice-to-Have)

### CQ-1: Large Functions
**Location**: `storage/database.py:_initialize_database()` - 430 lines
**Issue**: Function too long, does too much
**Fix**: Refactor into smaller functions:
- `_create_sqlite_tables()`
- `_create_postgresql_tables()`
- `_create_indexes()`

**Effort**: Medium (careful refactoring + testing)
**Priority**: Low (works correctly despite size)

---

### CQ-2: Duplicate Code
**Location**: `storage/database.py:152-358` vs `365-565`
**Issue**: Database schema duplicated for SQLite vs PostgreSQL
**Fix**: Extract common table definitions
**Effort**: Medium
**Priority**: Low

---

### CQ-6: Missing Error Context
**Location**: Many exception handlers
**Issue**: Logs error message but not stack trace
**Example**:
```python
except Exception as e:
    logger.error(f"Error: {e}")  # Missing stack trace
```
**Fix**: Add `exc_info=True` or use `logger.exception()`
**Effort**: Low (find/replace)
**Priority**: Low (helps debugging)

---

## 📊 Summary

| Priority | Count | Status |
|----------|-------|--------|
| Enterprise Fixes | 4 | ✅ Done |
| Bug Fixes | 3 | ✅ Done |
| Low      | 7     | Deferred |
| Code Quality | 3 | Deferred |
| **TOTAL** | **17** | **10 Deferred, 7 Fixed** |

**Enterprise Improvements** (this session):
- Redis-backed rate limiting (distributed) ✅
- Redis-backed session cache (scalable) ✅
- Comprehensive health checks (/health/detailed, /health/ready, /health/live) ✅
- Alembic database migrations ✅

**Bug Fixes**:
- M-7: Audit log failure alerting ✅
- M-10: Celery Beat in production compose ✅
- Encryption library startup check ✅

**Already Fixed** (previous sessions): 12 bugs (6 critical/high + 6 medium/low/quality)
**Remaining**: 10 low-priority items documented as technical debt

---

## 🎯 Recommended Next Steps

**Sprint 1** (High ROI):
1. L-6: Extract magic numbers to constants (readability)
2. CQ-6: Add stack traces to error logs

**Sprint 2** (Code Quality):
3. CQ-1: Refactor large _initialize_database() function
4. L-3: Resolve TODO comments

**Sprint 3** (Polish):
5. M-8: Resolve session token storage architecture
6. L-1: Consolidate config classes
7. L-4: Add missing type hints

**Not Urgent**:
- L-5, L-7, CQ-2: Low impact, can wait

---

## Notes

- All critical and high-priority security bugs have been fixed
- Production deployment is safe with current state
- These items are optimization opportunities, not blockers
- Track progress via GitHub Issues or project board
