"""
Sentry Error Tracking Configuration
Production-grade error monitoring and performance tracking
Helps identify and diagnose issues in production
"""

import logging
import os

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

try:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    _SENTRY_AVAILABLE = True
except ImportError:
    _SENTRY_AVAILABLE = False
    logger.info("sentry-sdk not installed, error tracking unavailable")

# SQLAlchemy integration is optional — only available if sqlalchemy is installed
_SqlalchemyIntegration: type = type(None)
if _SENTRY_AVAILABLE:
    try:
        from sentry_sdk.integrations.sqlalchemy import (  # type: ignore[no-redef]
            SqlalchemyIntegration as _SqlalchemyIntegration,
        )
    except Exception:
        pass


def init_sentry():
    """
    Initialize Sentry SDK for error tracking

    Configuration via environment variables:
    - SENTRY_DSN: Sentry project DSN
    - SENTRY_ENVIRONMENT: Environment name (production, staging, development)
    - SENTRY_TRACES_SAMPLE_RATE: Performance monitoring sample rate (0.0-1.0)
    - SENTRY_PROFILES_SAMPLE_RATE: Profiling sample rate (0.0-1.0)
    """
    sentry_dsn = os.getenv("SENTRY_DSN")
    sentry_enabled = os.getenv("SENTRY_ENABLED", "false").lower() == "true"

    if not _SENTRY_AVAILABLE:
        logger.warning("Sentry SDK not installed, skipping initialization")
        return

    if not sentry_enabled or not sentry_dsn:
        logger.info(
            "Sentry error tracking disabled (set SENTRY_ENABLED=true and SENTRY_DSN to enable)"
        )
        return

    environment = os.getenv(
        "SENTRY_ENVIRONMENT", os.getenv("ENVIRONMENT", "development")
    )
    traces_sample_rate = float(
        os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    )  # 10% of transactions
    profiles_sample_rate = float(
        os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")
    )  # 10% profiling

    # Logging integration (capture log messages as breadcrumbs)
    logging_integration = LoggingIntegration(
        level=logging.INFO,  # Capture info and above as breadcrumbs
        event_level=logging.ERROR,  # Send errors and above as events
    )

    # Build integrations list (SQLAlchemy is optional)
    integrations = [
        logging_integration,
        RedisIntegration(),
        CeleryIntegration(),
    ]
    if _SqlalchemyIntegration is not None:
        integrations.append(_SqlalchemyIntegration())

    # Initialize Sentry SDK
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=environment,
        release=f"snflwr-ai@{system_config.VERSION}",
        # Integrations
        integrations=integrations,
        # Performance Monitoring
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        # Additional Options
        send_default_pii=False,  # COPPA compliance: Don't send PII
        attach_stacktrace=True,  # Always attach stack traces
        include_local_variables=False,  # never capture frame locals (PII)
        max_breadcrumbs=50,  # Number of breadcrumbs to keep
        # Filtering
        before_send=before_send_filter,
        before_breadcrumb=before_breadcrumb_filter,
        # Performance
        enable_tracing=True,
    )

    logger.info(
        f"Sentry initialized: environment={environment}, "
        f"traces_sample_rate={traces_sample_rate}, "
        f"profiles_sample_rate={profiles_sample_rate}"
    )


def _scrub_freetext(event):
    """Wholesale-redact free-text that can carry children's PII (COPPA):
    exception value strings, the top-level message/logentry, and breadcrumb
    messages. Keeps exception ``type`` and stacktrace frames (file/func/line —
    PII-free). Never raises: on any error, coarse-redact and return the event
    rather than dropping it."""
    FILTERED = "[Filtered]"
    try:
        exc = event.get("exception")
        if isinstance(exc, dict):
            for v in exc.get("values") or []:
                if isinstance(v, dict) and v.get("value") is not None:
                    v["value"] = FILTERED
        if event.get("message") is not None:
            event["message"] = FILTERED
        logentry = event.get("logentry")
        if isinstance(logentry, dict):
            for k in ("message", "formatted"):
                if logentry.get(k) is not None:
                    logentry[k] = FILTERED
        bc = event.get("breadcrumbs")
        bc_values = bc.get("values") if isinstance(bc, dict) else bc
        if isinstance(bc_values, list):
            for crumb in bc_values:
                if isinstance(crumb, dict) and crumb.get("message") is not None:
                    crumb["message"] = FILTERED
    except Exception:
        # Never drop an event over a scrub bug; never leak a partially-scrubbed
        # exception. Remove the exception node and blank the message.
        event.pop("exception", None)
        event["message"] = FILTERED
    return event


def before_send_filter(event, hint):
    """
    Filter events before sending to Sentry
    Prevents PII from being sent (COPPA compliance)

    Args:
        event: Sentry event dict
        hint: Additional context

    Returns:
        Modified event or None to drop
    """
    # Drop events from non-production environments if desired
    if os.getenv("SENTRY_SEND_IN_DEV", "false").lower() != "true":
        if os.getenv("ENVIRONMENT", "development").lower() != "production":
            return None

    # Scrub PII from event data
    if "request" in event:
        # Remove sensitive headers
        if "headers" in event["request"]:
            sensitive_headers = ["Authorization", "Cookie", "X-CSRF-Token"]
            for header in sensitive_headers:
                if header in event["request"]["headers"]:
                    event["request"]["headers"][header] = "[Filtered]"

        # Remove query parameters that might contain PII
        if "query_string" in event["request"]:
            event["request"]["query_string"] = "[Filtered]"

        # Wholesale-filter the request body — a POST to a chat/profile route can
        # carry a child's message text, name, or age. We never need it for
        # debugging, so drop it entirely rather than risk leaking child PII.
        if "data" in event["request"]:
            event["request"]["data"] = "[Filtered]"

    # Scrub user data (only keep non-PII identifiers)
    if "user" in event:
        # Keep user_id but remove email, name, etc.
        user_data = event.get("user", {})
        filtered_user = {}
        if "id" in user_data:
            filtered_user["id"] = user_data["id"]
        if "role" in user_data:
            filtered_user["role"] = user_data["role"]
        event["user"] = filtered_user

    # Scrub extra context
    if "extra" in event:
        sensitive_keys = ["email", "password", "token", "secret", "api_key"]
        for key in list(event["extra"].keys()):
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                event["extra"][key] = "[Filtered]"

    # Scrub tags — filter values whose key suggests PII. Tags are meant to be
    # low-cardinality labels, so a child's name/age/message in a tag is a misuse;
    # drop it rather than risk it.
    if isinstance(event.get("tags"), dict):
        pii_tag_keys = (
            "email",
            "name",
            "child",
            "student",
            "profile",
            "message",
            "age",
            "phone",
            "address",
            "user",
            "password",
            "token",
            "secret",
        )
        for key in list(event["tags"].keys()):
            if any(p in key.lower() for p in pii_tag_keys):
                event["tags"][key] = "[Filtered]"

    # Scrub contexts — keep the standard SDK blocks (os/runtime/etc., which are
    # PII-free) and wholesale-filter any custom context block, which is where an
    # app could stash child data.
    if isinstance(event.get("contexts"), dict):
        std_contexts = {
            "trace",
            "os",
            "runtime",
            "device",
            "app",
            "browser",
            "gpu",
            "culture",
            "response",
            "cloud_resource",
            "replay",
        }
        for name in list(event["contexts"].keys()):
            if name.lower() not in std_contexts:
                event["contexts"][name] = "[Filtered]"

    return _scrub_freetext(event)


def before_breadcrumb_filter(crumb, hint):
    """
    Filter breadcrumbs before adding to event
    Prevents PII in breadcrumbs

    Args:
        crumb: Breadcrumb dict
        hint: Additional context

    Returns:
        Modified breadcrumb or None to drop
    """
    # Filter SQL queries that might contain PII
    if crumb.get("category") == "query":
        if "data" in crumb and "query" in crumb["data"]:
            # Only log query type, not the actual query
            query = crumb["data"]["query"]
            if query.strip().upper().startswith("SELECT"):
                crumb["data"]["query"] = "SELECT [filtered]"
            elif query.strip().upper().startswith("INSERT"):
                crumb["data"]["query"] = "INSERT [filtered]"
            elif query.strip().upper().startswith("UPDATE"):
                crumb["data"]["query"] = "UPDATE [filtered]"

    # Filter HTTP request data
    if crumb.get("category") == "httplib":
        if "data" in crumb:
            # Remove sensitive data from HTTP requests
            if "url" in crumb["data"]:
                # Keep only the path, remove query params
                crumb["data"]["url"] = crumb["data"]["url"].split("?")[0]

    return crumb


def set_user_context(user_id: str, role: str = None):
    """
    Set user context for Sentry events
    Only sets non-PII identifiers (COPPA compliant)

    Args:
        user_id: User ID (non-PII identifier)
        role: User role (admin, parent, etc.)
    """
    user_data = {"id": user_id}
    if role:
        user_data["role"] = role

    sentry_sdk.set_user(user_data)


def set_context(context_name: str, context_data: dict):
    """
    Set custom context for Sentry events

    Args:
        context_name: Name of the context
        context_data: Context data dict
    """
    sentry_sdk.set_context(context_name, context_data)


def capture_exception(exception: Exception, **kwargs):
    """
    Manually capture an exception

    Args:
        exception: Exception to capture
        **kwargs: Additional context (tags, extras, etc.)
    """
    with sentry_sdk.push_scope() as scope:
        # Add tags
        if "tags" in kwargs:
            for key, value in kwargs["tags"].items():
                scope.set_tag(key, value)

        # Add extra context
        if "extra" in kwargs:
            for key, value in kwargs["extra"].items():
                scope.set_extra(key, value)

        # Capture exception
        sentry_sdk.capture_exception(exception)


def capture_message(message: str, level: str = "info", **kwargs):
    """
    Capture a message event

    Args:
        message: Message to capture
        level: Log level (debug, info, warning, error, fatal)
        **kwargs: Additional context
    """
    with sentry_sdk.push_scope() as scope:
        # Add tags
        if "tags" in kwargs:
            for key, value in kwargs["tags"].items():
                scope.set_tag(key, value)

        # Add extra context
        if "extra" in kwargs:
            for key, value in kwargs["extra"].items():
                scope.set_extra(key, value)

        # Capture message
        sentry_sdk.capture_message(message, level=level)  # type: ignore[arg-type]


def add_breadcrumb(
    message: str, category: str = "default", level: str = "info", **kwargs
):
    """
    Add a breadcrumb for debugging context

    Args:
        message: Breadcrumb message
        category: Breadcrumb category
        level: Log level
        **kwargs: Additional data
    """
    sentry_sdk.add_breadcrumb(
        message=message, category=category, level=level, data=kwargs.get("data", {})
    )


def start_transaction(name: str, op: str = "http.server"):
    """
    Start a performance transaction

    Args:
        name: Transaction name
        op: Operation type

    Returns:
        Transaction context manager
    """
    return sentry_sdk.start_transaction(name=name, op=op)


def start_span(description: str, op: str = "function"):
    """
    Start a performance span

    Args:
        description: Span description
        op: Operation type

    Returns:
        Span context manager
    """
    return sentry_sdk.start_span(description=description, op=op)


# Export public interface
__all__ = [
    "init_sentry",
    "set_user_context",
    "set_context",
    "capture_exception",
    "capture_message",
    "add_breadcrumb",
    "start_transaction",
    "start_span",
]
