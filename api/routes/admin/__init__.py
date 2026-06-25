"""
Admin Management Routes (package)

Handles syncing Open WebUI admins to Snflwr database and provides admin
dashboard API endpoints for managing accounts, profiles, alerts, activity,
and audit logs.

[LOCKED] SECURED: All routes require admin authentication
- Only admins can access these endpoints
- Prevents unauthorized admin account creation

This package preserves the original public contract of the former
``api/routes/admin.py`` module:

* ``api.routes.admin.router`` exposes the exact same routes at the exact same
  paths under the ``/api/admin`` prefix.
* Shared helpers, Pydantic models, the rate limiter and the symbols the test
  suite patches (``DatabaseManager``, ``audit_log``, ``get_email_crypto``,
  ``auth_manager``, ``set_csrf_cookie``, ``AgeVerificationManager``, the
  ``_owui_*`` helpers, ``_get_owui_token``, ``check_auth_rate_limit``,
  ``rate_limiter``) are re-exported here so ``patch("api.routes.admin.X")``
  continues to work and resolves to the same object the handlers use at
  call-time.
"""

from fastapi import APIRouter

from . import accounts, activity, auth, misc, profiles

# Re-export the names that tests patch and that other modules may import,
# so the public contract of the old single-file module is preserved.
from ._common import (  # noqa: F401
    _ACCOUNT_UPDATE_COLUMNS,
    _PROFILE_UPDATE_COLUMNS,
    DB_ERRORS,
    AdminLoginRequest,
    AdminResponse,
    AdminSyncRequest,
    AgeVerificationManager,
    AuthSession,
    BulkImportRequest,
    CreateAccountRequest,
    CreateProfileRequest,
    DatabaseManager,
    FalsePositiveReview,
    RateLimiter,
    StudentImportRecord,
    UpdateAccountRequest,
    UpdateProfileAdminRequest,
    _get_owui_token,
    _owui_activate_user,
    _owui_create_user,
    _owui_delete_user,
    _owui_find_user_by_email,
    _to_dict,
    audit_log,
    auth_manager,
    check_auth_rate_limit,
    encryption_manager,
    get_email_crypto,
    get_logger,
    hash_session_token,
    logger,
    rate_limiter,
    require_admin,
    sanitize_log_value,
    set_csrf_cookie,
    system_config,
)

# Re-export route handler callables that tests import directly. The handlers
# look up patched symbols (DatabaseManager, audit_log, ...) via the package
# namespace at call-time, so patching ``api.routes.admin.X`` still applies when
# these are invoked directly.
from .accounts import batch_delete_accounts, delete_account  # noqa: F401
from .activity import batch_delete_activity, batch_delete_alerts  # noqa: F401
from .profiles import batch_delete_profiles, delete_profile  # noqa: F401

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Order matters: ``misc`` holds the catch-all ``/{admin_id}`` route and MUST be
# included last so specific routes (/stats, /accounts, ...) match first.
router.include_router(auth.router)
router.include_router(accounts.router)
router.include_router(profiles.router)
router.include_router(activity.router)
router.include_router(misc.router)
