"""
Authorization Middleware
Provides authentication and authorization for API routes
"""

import hmac
import os
import sqlite3
import threading
import time as _time
from typing import Optional
from fastapi import HTTPException, Header, Depends, status
from functools import wraps

from core.authentication import auth_manager, AuthSession
from core.profile_manager import ProfileManager
from storage.db_adapters import DB_ERRORS
from config import INTERNAL_API_KEY, INTERNAL_API_KEY_PREVIOUS, system_config
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

# Conditional Redis error import
try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # type: ignore[misc,assignment]  # Fallback so except RedisError still works


# ============================================================================
# AUTHENTICATION - Verify Session
# ============================================================================


async def get_current_session(authorization: str = Header(None)) -> AuthSession:
    """
    Verify authentication token and return session

    Args:
        authorization: Bearer token from Authorization header

    Returns:
        AuthSession object

    Raises:
        HTTPException: 401 if token invalid/missing
    """
    if not authorization:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        logger.warning("Invalid Authorization format (not Bearer scheme)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ")[1]

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

    # Validate session
    is_valid, session = auth_manager.validate_session(token)

    if not is_valid or not session:
        logger.warning("Invalid or expired session token presented")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info(f"Authenticated user: {session.user_id} (role: {session.role})")
    return session


async def get_optional_session(
    authorization: str = Header(None),
) -> Optional[AuthSession]:
    """
    Get session if provided, but don't require it

    Args:
        authorization: Optional Bearer token

    Returns:
        AuthSession or None
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.split(" ")[1]
    is_valid, session = auth_manager.validate_session(token)

    return session if is_valid else None


# ============================================================================
# AUTHORIZATION - Role-Based Access Control (RBAC)
# ============================================================================


async def require_admin(
    session: AuthSession = Depends(get_current_session),
) -> AuthSession:
    """
    Require user to be an admin

    Args:
        session: Current authenticated session

    Returns:
        AuthSession if user is admin

    Raises:
        HTTPException: 403 if not admin
    """
    if session.role != "admin":
        logger.warning(
            f"Access denied: User {session.user_id} (role: {session.role}) attempted admin action"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    return session


async def require_parent(
    session: AuthSession = Depends(get_current_session),
) -> AuthSession:
    """
    Require user to be a parent or admin

    Args:
        session: Current authenticated session

    Returns:
        AuthSession if user is parent or admin

    Raises:
        HTTPException: 403 if not parent or admin
    """
    if session.role not in ("parent", "admin"):
        logger.warning(
            f"Access denied: User {session.user_id} (role: {session.role}) not a parent or admin"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Parent access required"
        )

    return session


# ============================================================================
# RESOURCE AUTHORIZATION - Verify Ownership
# ============================================================================


class ResourceAuthorization:
    """Helper class for resource-level authorization"""

    @staticmethod
    async def verify_parent_access(
        parent_id: str, session: AuthSession = Depends(get_current_session)
    ) -> AuthSession:
        """
        Verify user can access parent data

        Admins can access any parent's data
        Parents can only access their own data

        Args:
            parent_id: ID of parent being accessed
            session: Current session

        Returns:
            AuthSession if authorized

        Raises:
            HTTPException: 403 if not authorized
        """
        # Admins can access everything
        if session.role == "admin":
            logger.info(f"Admin {session.user_id} accessing parent {parent_id}")
            return session

        # Parents can only access their own data
        if session.user_id != parent_id:
            logger.warning(
                f"Access denied: User {session.user_id} attempted to access "
                f"parent {parent_id}'s data"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You can only access your own data",
            )

        logger.info(f"Parent {session.user_id} accessing own data")
        return session

    @staticmethod
    async def verify_profile_access(
        profile_id: str, session: AuthSession = Depends(get_current_session)
    ) -> AuthSession:
        """
        Verify user can access child profile

        Admins can access any profile
        Parents can only access their children's profiles

        Args:
            profile_id: ID of profile being accessed
            session: Current session

        Returns:
            AuthSession if authorized

        Raises:
            HTTPException: 403 if not authorized, 404 if profile not found
        """
        # Admins can access everything
        if session.role == "admin":
            logger.info(f"Admin {session.user_id} accessing profile {profile_id}")
            return session

        # Get profile to check ownership
        profile_mgr = ProfileManager(auth_manager.db)
        profile = profile_mgr.get_profile(profile_id)

        if not profile:
            logger.warning(f"Profile not found: {profile_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
            )

        # Verify parent owns this profile
        if profile.parent_id != session.user_id:
            logger.warning(
                f"Access denied: User {session.user_id} attempted to access "
                f"profile {profile_id} (belongs to {profile.parent_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: This is not your child's profile",
            )

        logger.info(
            f"Parent {session.user_id} accessing own child's profile {profile_id}"
        )
        return session

    @staticmethod
    async def verify_session_access(
        session_id: str, session: AuthSession = Depends(get_current_session)
    ) -> AuthSession:
        """
        Verify user can access a conversation session

        Args:
            session_id: Session ID to check
            session: Current authenticated session

        Returns:
            AuthSession if authorized

        Raises:
            HTTPException: 403 if not authorized
        """
        # Admins can access everything
        if session.role == "admin":
            logger.info(f"Admin {session.user_id} accessing session {session_id}")
            return session

        # Get session to check which profile it belongs to
        from core.session_manager import session_manager

        conv_session = session_manager.get_session(session_id)

        if not conv_session:
            logger.warning(f"Session not found: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
            )

        # Get the profile to check parent ownership
        profile_mgr = ProfileManager(auth_manager.db)
        profile = profile_mgr.get_profile(conv_session.profile_id)

        if not profile:
            logger.warning(f"Profile not found for session: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
            )

        # Verify parent owns this profile
        if profile.parent_id != session.user_id:
            logger.warning(
                f"Access denied: User {session.user_id} attempted to access "
                f"session {session_id} (belongs to {profile.parent_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You can only access your own child's sessions",
            )

        logger.info(
            f"Parent {session.user_id} accessing own child's session {session_id}"
        )
        return session

    @staticmethod
    async def verify_alert_access(
        alert_id: str, session: AuthSession = Depends(get_current_session)
    ) -> AuthSession:
        """
        Verify user can access a safety alert

        Args:
            alert_id: Alert ID to check
            session: Current authenticated session

        Returns:
            AuthSession if authorized

        Raises:
            HTTPException: 403 if not authorized
        """
        # Admins can access everything
        if session.role == "admin":
            logger.info(f"Admin {session.user_id} accessing alert {alert_id}")
            return session

        # Get alert to check which parent it belongs to
        from safety.safety_monitor import safety_monitor

        alert = safety_monitor.get_alert(alert_id)

        if not alert:
            logger.warning(f"Alert not found: {alert_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
            )

        # Verify parent owns this alert
        if alert.parent_id != session.user_id:
            logger.warning(
                f"Access denied: User {session.user_id} attempted to access "
                f"alert {alert_id} (belongs to {alert.parent_id})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You can only access your own alerts",
            )

        logger.info(f"Parent {session.user_id} accessing own alert {alert_id}")
        return session


# ============================================================================
# CONVENIENCE DEPENDENCIES
# ============================================================================

# Export commonly used dependencies
CurrentSession = Depends(get_current_session)
OptionalSession = Depends(get_optional_session)
AdminOnly = Depends(require_admin)
ParentOnly = Depends(require_parent)
VerifyParentAccess = ResourceAuthorization.verify_parent_access
VerifyProfileAccess = ResourceAuthorization.verify_profile_access
VerifySessionAccess = ResourceAuthorization.verify_session_access
VerifyAlertAccess = ResourceAuthorization.verify_alert_access


# ============================================================================
# AUDIT LOGGING
# ============================================================================

# Track consecutive audit log failures for alerting.
# Uses Redis when available so the counter is shared across workers;
# falls back to a module-level counter for single-process deployments.
_AUDIT_FAILURE_THRESHOLD = 5  # Alert after this many consecutive failures
_AUDIT_REDIS_KEY = "snflwr:audit_failure_count"
_audit_failure_count_local = 0  # Fallback for non-Redis deployments


def _get_audit_failure_count() -> int:
    """Get the current consecutive audit failure count."""
    try:
        from utils.cache import cache

        if cache.enabled and cache._client:
            val = cache._client.get(_AUDIT_REDIS_KEY)
            return int(val) if val else 0
    except Exception:
        pass
    return _audit_failure_count_local


def _increment_audit_failure_count() -> int:
    """Atomically increment the audit failure counter. Returns the new value."""
    global _audit_failure_count_local
    try:
        from utils.cache import cache

        if cache.enabled and cache._client:
            return cache._client.incr(_AUDIT_REDIS_KEY)
    except Exception:
        pass
    _audit_failure_count_local += 1
    return _audit_failure_count_local


def _reset_audit_failure_count():
    """Reset the audit failure counter to zero."""
    global _audit_failure_count_local
    _audit_failure_count_local = 0
    try:
        from utils.cache import cache

        if cache.enabled and cache._client:
            cache._client.delete(_AUDIT_REDIS_KEY)
    except Exception:
        pass


def _send_audit_failure_alert(failure_count: int, error):
    """Send alert email to admin about audit log failures."""
    try:
        from config import system_config

        if hasattr(system_config, "ADMIN_EMAIL") and system_config.ADMIN_EMAIL:
            from tasks.background_tasks import send_email, safe_dispatch

            safe_dispatch(
                send_email,
                to_email=system_config.ADMIN_EMAIL,
                subject="CRITICAL: snflwr.ai Audit Log Failure",
                html_content=f"""
                <h2>Audit Log Failure Alert</h2>
                <p><strong>Consecutive failures:</strong> {failure_count}</p>
                <p><strong>Last error:</strong> {error}</p>
                <p>This is a compliance risk. Please investigate immediately.</p>
                """,
                fallback_sync=True,
            )
    except Exception as alert_error:
        logger.error(f"Failed to send audit failure alert: {alert_error}")


def audit_log(
    action: str, resource_type: str, resource_id: str, session: AuthSession
) -> bool:
    """
    Log security-sensitive actions for audit trail

    Args:
        action: Action performed (view, create, update, delete)
        resource_type: Type of resource (profile, incident, etc.)
        resource_id: ID of resource
        session: Current session

    Returns:
        True if audit log was written successfully, False otherwise
    """
    from storage.database import db_manager
    from datetime import datetime, timezone

    try:
        db_manager.execute_write(
            """
            INSERT INTO audit_log (
                timestamp, event_type, user_id, user_type,
                action, details, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                "resource_access",
                session.user_id,
                session.role,
                action,
                f"{action} {resource_type}: {resource_id}",
                True,
            ),
        )
        logger.debug(
            f"Audit: {sanitize_log_value(session.user_id)!r} ({sanitize_log_value(session.role)!r}) {sanitize_log_value(action)!r} {sanitize_log_value(resource_type)!r} {sanitize_log_value(resource_id)!r}"
        )
        _reset_audit_failure_count()
        return True
    except Exception as e:
        count = _increment_audit_failure_count()
        if isinstance(e, DB_ERRORS):
            logger.error(f"Database error writing audit log: {e}")
        else:
            logger.exception(f"Unexpected error writing audit log: {e}")

        if count >= _AUDIT_FAILURE_THRESHOLD:
            logger.critical(
                f"ALERT: Audit logging has failed {count} consecutive times. "
                f"This is a compliance risk (COPPA/FERPA). Check database connectivity. "
                f"Last error: {e}"
            )
            _send_audit_failure_alert(count, e)
        return False


# ============================================================================
# RATE LIMITING (Redis-backed for distributed deployments)
# ============================================================================

from datetime import datetime, timedelta, timezone

# Import metrics (optional to avoid circular imports)
_metrics_available = False
try:
    from utils.metrics import record_rate_limit_check

    _metrics_available = True
except ImportError:
    pass


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
        now = _time.time()
        cutoff = now - window_seconds
        cache_key = f"{limit_type}:{key}"

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "DELETE FROM rate_limits WHERE key = ? AND timestamp < ?",
                (cache_key, cutoff),
            )
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


class RedisRateLimiter:
    """
    Redis-backed rate limiter for distributed/multi-instance deployments.
    Uses sliding window algorithm with Redis INCR and EXPIRE for atomic operations.
    Falls back to in-memory limiting if Redis is unavailable.
    """

    def __init__(self):
        self.limits = {
            "default": (100, 60),  # 100 requests per 60 seconds
            "auth": (10, 60),  # 10 login attempts per 60 seconds
            "api": (1000, 60),  # 1000 API calls per 60 seconds
        }
        self._redis = None
        self._fallback_requests = {}  # In-memory fallback if Redis unavailable
        self._fallback_lock = threading.Lock()
        self._redis_healthy = True
        self._redis_alert_sent = False
        self._sqlite_limiter = None
        self._initialize_redis()

        # Use SQLite fallback for persistence when Redis is unavailable
        if not self._redis:
            try:
                from config import DATA_DIR

                db_path = os.path.join(DATA_DIR, "snflwr.db")
                self._sqlite_limiter = SqliteRateLimiter(db_path)
                logger.warning(
                    "Rate limiter using SQLite fallback (single-instance only). "
                    "Enable Redis for distributed rate limiting."
                )
            except Exception as exc:
                logger.error("Failed to init SQLite rate limiter: %s", exc)

    def _initialize_redis(self):
        """Initialize Redis connection for rate limiting"""
        try:
            from utils.cache import cache

            if cache.enabled and cache._client:
                self._redis = cache._client
                logger.info("Rate limiter using Redis (distributed mode)")
            else:
                logger.warning(
                    "Rate limiter using in-memory fallback (single-instance only)"
                )
        except (RedisError, ImportError) as e:
            logger.warning(f"Rate limiter Redis init failed, using fallback: {e}")

    def check_rate_limit(self, key: str, limit_type: str = "default") -> bool:
        """
        Check if request is within rate limit using Redis sliding window.

        Args:
            key: Unique identifier (user_id, IP, etc.)
            limit_type: Type of limit to apply

        Returns:
            True if within limit, False if exceeded
        """
        max_requests, window_seconds = self.limits.get(
            limit_type, self.limits["default"]
        )

        if self._redis:
            result = self._check_redis_rate_limit(
                key, limit_type, max_requests, window_seconds
            )
        else:
            result = self._check_fallback_rate_limit(
                key, limit_type, max_requests, window_seconds
            )

        # Record metrics
        if _metrics_available:
            record_rate_limit_check(result)

        return result

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

            if system_config.REDIS_ENABLED:
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

    def _check_fallback_rate_limit(
        self, key: str, limit_type: str, max_requests: int, window_seconds: int
    ) -> bool:
        """Fallback rate limiting: SQLite if available, else in-memory."""
        if self._sqlite_limiter:
            return self._sqlite_limiter.check(key, limit_type, max_requests, window_seconds)

        # In-memory fallback (per-process only)
        with self._fallback_lock:
            now = datetime.now(timezone.utc)
            cache_key = f"{limit_type}:{key}"

            # Clean old requests
            cutoff = now - timedelta(seconds=window_seconds)
            if cache_key in self._fallback_requests:
                self._fallback_requests[cache_key] = [
                    req_time
                    for req_time in self._fallback_requests[cache_key]
                    if req_time > cutoff
                ]
            else:
                self._fallback_requests[cache_key] = []

            # Check limit
            if len(self._fallback_requests[cache_key]) >= max_requests:
                logger.warning(
                    f"Rate limit exceeded for {key} (limit: {max_requests}/{window_seconds}s)"
                )
                return False

            # Record request
            self._fallback_requests[cache_key].append(now)
            return True

    def get_remaining(self, key: str, limit_type: str = "default") -> int:
        """Get remaining requests in current window"""
        max_requests, window_seconds = self.limits.get(
            limit_type, self.limits["default"]
        )

        if self._redis:
            try:
                redis_key = f"snflwr:ratelimit:{limit_type}:{key}"
                current = self._redis.get(redis_key)
                if current is None:
                    return max_requests
                return max(0, max_requests - int(current))
            except RedisError as e:
                logger.error(f"Redis error getting remaining rate limit: {e}")
                return max_requests
        else:
            cache_key = f"{limit_type}:{key}"
            with self._fallback_lock:
                current = len(self._fallback_requests.get(cache_key, []))
            return max(0, max_requests - current)

    def reset(self, key: str, limit_type: str = "default") -> bool:
        """Reset rate limit counter for a key (admin use)"""
        if self._redis:
            try:
                redis_key = f"snflwr:ratelimit:{limit_type}:{key}"
                self._redis.delete(redis_key)
                return True
            except RedisError as e:
                logger.error(f"Redis error resetting rate limit: {e}")
                return False
        else:
            cache_key = f"{limit_type}:{key}"
            with self._fallback_lock:
                self._fallback_requests.pop(cache_key, None)
            return True


# Global rate limiter instance
_rate_limiter = RedisRateLimiter()


async def check_rate_limit(
    session: AuthSession = Depends(get_current_session), limit_type: str = "api"
):
    """
    Check rate limit for current user

    Args:
        session: Current session
        limit_type: Type of rate limit

    Raises:
        HTTPException: 429 if rate limit exceeded
    """
    if not _rate_limiter.check_rate_limit(session.user_id, limit_type):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
        )


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    "get_current_session",
    "get_optional_session",
    "require_admin",
    "require_parent",
    "ResourceAuthorization",
    "VerifyParentAccess",
    "VerifyProfileAccess",
    "audit_log",
    "check_rate_limit",
    "CurrentSession",
    "OptionalSession",
    "AdminOnly",
    "ParentOnly",
]
