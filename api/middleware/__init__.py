"""
API Middleware
Authentication and authorization middleware for API routes
"""

from .auth import (
    get_current_session,
    get_optional_session,
    require_admin,
    require_parent,
    ResourceAuthorization,
    VerifyParentAccess,
    VerifyProfileAccess,
    VerifySessionAccess,
    VerifyAlertAccess,
    audit_log,
    check_rate_limit,
    CurrentSession,
    OptionalSession,
    AdminOnly,
    ParentOnly,
)

__all__ = [
    "get_current_session",
    "get_optional_session",
    "require_admin",
    "require_parent",
    "ResourceAuthorization",
    "VerifyParentAccess",
    "VerifyProfileAccess",
    "VerifySessionAccess",
    "VerifyAlertAccess",
    "audit_log",
    "check_rate_limit",
    "CurrentSession",
    "OptionalSession",
    "AdminOnly",
    "ParentOnly",
]
