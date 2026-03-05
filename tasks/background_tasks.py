"""
Background Tasks
Celery tasks for asynchronous processing of time-consuming operations
Improves API response times by offloading work to background workers
"""

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import List, Dict, Any, Optional
import json

import smtplib

from utils.celery_config import celery_app
from utils.logger import get_logger, mask_email
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from core.email_service import email_service
from core.email_crypto import get_email_crypto
from config import system_config

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError

logger = get_logger(__name__)


# ============================================================================
# SAFE DISPATCH UTILITY
# ============================================================================

def safe_dispatch(task, *args, fallback_sync=False, **kwargs):
    """
    Dispatch a Celery task with graceful degradation when the broker is unavailable.

    When Redis (the Celery broker) is down, .delay() raises a connection error.
    This wrapper catches that and:
      - If fallback_sync=True, runs the task function synchronously (for
        safety-critical emails like safety alerts and audit failure alerts).
      - Otherwise, logs a warning and silently drops the dispatch.

    Args:
        task: The Celery task object (e.g., send_email)
        *args: Positional arguments for the task
        fallback_sync: If True, execute the task synchronously on failure
        **kwargs: Keyword arguments for the task

    Returns:
        AsyncResult on success, result of synchronous call on fallback, or None on drop
    """
    try:
        return task.delay(*args, **kwargs)
    except (ConnectionError, OSError, RedisError) as exc:
        task_name = getattr(task, 'name', str(task))
        if fallback_sync:
            logger.warning(
                "Celery broker unavailable — running %s synchronously: %s",
                task_name, exc,
            )
            try:
                # Call the underlying function (skip Celery machinery).
                # Bound tasks pass `self` automatically when called via .run();
                # call the original function directly.
                return task(*args, **kwargs)
            except Exception as sync_exc:
                logger.error("Synchronous fallback for %s failed: %s", task_name, sync_exc)
                return None
        else:
            logger.warning(
                "Celery broker unavailable — dropping dispatch of %s: %s",
                task_name, exc,
            )
            return None


# ============================================================================
# EMAIL TASKS
# ============================================================================

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='tasks.background_tasks.send_email'
)
def send_email(
    self,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None
) -> bool:
    """
    Send email asynchronously

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML email content
        text_content: Plain text content (optional)

    Returns:
        True if sent successfully, False otherwise
    """
    try:
        logger.info(f"Sending email to {mask_email(to_email)}: {subject}")

        success = email_service.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

        if success:
            logger.info(f"Email sent successfully to {mask_email(to_email)}")
            return True
        else:
            logger.warning(f"Email sending failed to {mask_email(to_email)}")
            # Retry with exponential backoff
            raise self.retry(countdown=2 ** self.request.retries)

    except (smtplib.SMTPException, ConnectionError, OSError) as exc:
        logger.exception(f"Error sending email to {mask_email(to_email)}: {exc}")
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name='tasks.background_tasks.send_safety_alert'
)
def send_safety_alert(
    self,
    parent_email: str,
    child_name: str,
    incident_type: str,
    severity: str,
    timestamp: str,
    details: str
) -> bool:
    """
    Send safety incident alert to parent

    Args:
        parent_email: Parent's email address
        child_name: Child's name
        incident_type: Type of safety incident
        severity: Incident severity (CRITICAL, MAJOR, MINOR)
        timestamp: When incident occurred
        details: Incident details

    Returns:
        True if sent successfully
    """
    try:
        subject = f"[ALERT] Safety Alert for {child_name}"

        # Escape user-controlled values to prevent stored XSS in email
        safe_child_name = html_escape(child_name)
        safe_severity = html_escape(severity)
        safe_incident_type = html_escape(incident_type)
        safe_timestamp = html_escape(timestamp)
        safe_details = html_escape(details)
        severity_color = '#d63031' if severity == 'CRITICAL' else '#e17055' if severity == 'MAJOR' else '#fdcb6e'

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 2px solid #ff6b6b; border-radius: 8px;">
                <h2 style="color: #ff6b6b;">Safety Incident Alert</h2>

                <p><strong>Child:</strong> {safe_child_name}</p>
                <p><strong>Severity:</strong> <span style="color: {severity_color};">{safe_severity}</span></p>
                <p><strong>Type:</strong> {safe_incident_type}</p>
                <p><strong>Time:</strong> {safe_timestamp}</p>

                <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>Details:</h3>
                    <p>{safe_details}</p>
                </div>

                <p style="margin-top: 20px;">
                    <a href="{system_config.BASE_URL}/dashboard/safety"
                       style="background: #0984e3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                        View in Dashboard
                    </a>
                </p>

                <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">

                <p style="font-size: 12px; color: #666;">
                    This is an automated safety alert from snflwr.ai. Our 4-layer safety monitoring system detected content that requires your attention.
                </p>
            </div>
        </body>
        </html>
        """

        return send_email(to_email=parent_email, subject=subject, html_content=html_content)

    except (smtplib.SMTPException, ConnectionError, OSError) as exc:
        logger.exception(f"Error sending safety alert: {exc}")
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(
    bind=True,
    max_retries=2,
    name='tasks.background_tasks.send_batch_emails'
)
def send_batch_emails(self, emails: List[Dict[str, str]]) -> Dict[str, int]:
    """
    Send multiple emails in batch

    Args:
        emails: List of email dicts with 'to', 'subject', 'html_content'

    Returns:
        dict: Success and failure counts
    """
    success_count = 0
    failure_count = 0

    for email_data in emails:
        try:
            success = send_email.apply_async(
                args=[
                    email_data['to'],
                    email_data['subject'],
                    email_data['html_content']
                ]
            )
            success_count += 1
        except (smtplib.SMTPException, ConnectionError, OSError, RedisError) as e:
            logger.error(f"Failed to queue email to {mask_email(email_data['to'])}: {e}")
            failure_count += 1

    logger.info(f"Batch email: {success_count} queued, {failure_count} failed")
    return {'success': success_count, 'failed': failure_count}


@celery_app.task(name='tasks.background_tasks.send_daily_safety_digests')
def send_daily_safety_digests() -> int:
    """
    Send daily safety digest emails to parents with pending incidents
    Runs daily via Celery Beat

    Returns:
        Number of digests sent
    """
    try:
        logger.info("Starting daily safety digest job")

        # Get all parents with unresolved incidents from last 24 hours
        query = """
            SELECT DISTINCT u.encrypted_email, u.parent_id
            FROM accounts u
            JOIN child_profiles cp ON cp.parent_id = u.parent_id
            JOIN safety_incidents si ON si.profile_id = cp.profile_id
            WHERE si.resolved = 0
              AND si.timestamp >= datetime('now', '-1 day')
              AND si.severity = 'minor'
        """

        parents = db_manager.execute_read(query)
        digest_count = 0

        email_crypto = get_email_crypto()

        for parent in parents:
            parent_email = email_crypto.decrypt_email(parent['encrypted_email'])
            parent_id = parent['parent_id']

            # Get all incidents for this parent's children
            incidents_query = """
                SELECT si.*, cp.name as child_name
                FROM safety_incidents si
                JOIN child_profiles cp ON si.profile_id = cp.profile_id
                WHERE cp.parent_id = ?
                  AND si.resolved = 0
                  AND si.timestamp >= datetime('now', '-1 day')
                  AND si.severity = 'minor'
                ORDER BY si.timestamp DESC
            """

            incidents = db_manager.execute_read(incidents_query, (parent_id,))

            if incidents:
                # Build digest email
                subject = f"Daily Safety Digest - {len(incidents)} incidents"

                incidents_html = ""
                for incident in incidents:
                    safe_child = html_escape(str(incident['child_name']))
                    safe_type = html_escape(str(incident['incident_type']))
                    safe_time = html_escape(str(incident['timestamp']))
                    incidents_html += f"""
                    <div style="background: #f8f9fa; padding: 10px; margin: 10px 0; border-left: 3px solid #fdcb6e;">
                        <strong>{safe_child}</strong> - {safe_type}<br>
                        <small>{safe_time}</small>
                    </div>
                    """

                html_content = f"""
                <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Daily Safety Digest</h2>
                    <p>You have {len(incidents)} minor safety incident(s) from the last 24 hours:</p>
                    {incidents_html}
                    <p><a href="{system_config.BASE_URL}/dashboard/safety">Review all incidents</a></p>
                </body>
                </html>
                """

                send_email.delay(parent_email, subject, html_content)
                digest_count += 1

        logger.info(f"Sent {digest_count} daily safety digests")
        return digest_count

    except (DB_ERRORS + (smtplib.SMTPException, ConnectionError, OSError)) as e:
        logger.exception(f"Error sending daily digests: {e}")
        return 0


# ============================================================================
# MAINTENANCE TASKS
# ============================================================================

@celery_app.task(name='tasks.background_tasks.cleanup_old_messages')
def cleanup_old_messages() -> int:
    """
    Delete conversation messages older than retention period (180 days)
    Runs every 6 hours via Celery Beat

    Returns:
        Number of messages deleted
    """
    try:
        retention_days = 180
        logger.info(f"Cleaning up messages older than {retention_days} days")

        # Use parameterized query - SQLite requires string concatenation for datetime modifiers
        # Validate retention_days is an integer to prevent injection
        if not isinstance(retention_days, int) or retention_days < 0:
            logger.error(f"Invalid retention_days value: {retention_days}")
            return 0

        delete_query = f"""
            DELETE FROM messages
            WHERE timestamp < datetime('now', '-{int(retention_days)} days')
        """

        result = db_manager.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} old messages")
        return count

    except DB_ERRORS as e:
        logger.exception(f"Error cleaning up old messages: {e}")
        return 0


@celery_app.task(name='tasks.background_tasks.cleanup_old_sessions')
def cleanup_old_sessions() -> int:
    """
    Delete expired auth sessions
    Runs every 12 hours via Celery Beat

    Returns:
        Number of sessions deleted
    """
    try:
        logger.info("Cleaning up expired auth sessions")

        delete_query = """
            DELETE FROM auth_tokens
            WHERE expires_at < datetime('now')
              OR is_valid = 0
        """

        result = db_manager.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} expired sessions")
        return count

    except DB_ERRORS as e:
        logger.exception(f"Error cleaning up sessions: {e}")
        return 0


@celery_app.task(name='tasks.background_tasks.cleanup_old_incidents')
def cleanup_old_incidents() -> int:
    """
    Delete resolved safety incidents older than 90 days
    Runs daily via Celery Beat

    Returns:
        Number of incidents deleted
    """
    try:
        retention_days = 90
        logger.info(f"Cleaning up resolved incidents older than {retention_days} days")

        # Validate retention_days is an integer to prevent injection
        if not isinstance(retention_days, int) or retention_days < 0:
            logger.error(f"Invalid retention_days value: {retention_days}")
            return 0

        delete_query = f"""
            DELETE FROM safety_incidents
            WHERE resolved = 1
              AND timestamp < datetime('now', '-{int(retention_days)} days')
        """

        result = db_manager.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} old incidents")
        return count

    except DB_ERRORS as e:
        logger.exception(f"Error cleaning up incidents: {e}")
        return 0


@celery_app.task(
    name='tasks.background_tasks.cleanup_audit_logs',
    bind=True,
    max_retries=2
)
def cleanup_audit_logs(self) -> int:
    """
    Delete audit log entries older than retention period (default 365 days)
    Runs daily via Celery Beat

    Returns:
        Number of audit log records deleted
    """
    try:
        from config import safety_config as _safety_config
        from storage.database import db_manager as _db

        retention_days = getattr(_safety_config, 'AUDIT_LOG_RETENTION_DAYS', 365)
        logger.info(f"Cleaning up audit logs older than {retention_days} days")

        delete_query = f"""
            DELETE FROM audit_log
            WHERE timestamp < datetime('now', '-{int(retention_days)} days')
        """

        result = _db.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} old audit log records")
        return count

    except Exception as e:
        logger.exception(f"Error cleaning up audit logs: {e}")
        raise self.retry(exc=e, countdown=300)


@celery_app.task(
    name='tasks.background_tasks.cleanup_ended_sessions',
    bind=True,
    max_retries=2
)
def cleanup_ended_sessions(self) -> int:
    """
    Delete ended sessions older than retention period (default 180 days)
    Cleans the sessions table (not auth_tokens)
    Runs daily via Celery Beat

    Returns:
        Number of ended sessions deleted
    """
    try:
        from config import safety_config as _safety_config
        from storage.database import db_manager as _db

        retention_days = getattr(_safety_config, 'SESSION_RETENTION_DAYS', 180)
        logger.info(f"Cleaning up ended sessions older than {retention_days} days")

        delete_query = f"""
            DELETE FROM sessions
            WHERE ended_at IS NOT NULL
              AND ended_at < datetime('now', '-{int(retention_days)} days')
        """

        result = _db.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} old ended sessions")
        return count

    except Exception as e:
        logger.exception(f"Error cleaning up ended sessions: {e}")
        raise self.retry(exc=e, countdown=300)


@celery_app.task(
    name='tasks.background_tasks.cleanup_analytics',
    bind=True,
    max_retries=2
)
def cleanup_analytics(self) -> int:
    """
    Delete learning analytics older than retention period (default 730 days)
    Runs weekly via Celery Beat

    Returns:
        Number of analytics records deleted
    """
    try:
        from config import safety_config as _safety_config
        from storage.database import db_manager as _db

        retention_days = getattr(_safety_config, 'ANALYTICS_RETENTION_DAYS', 730)
        logger.info(f"Cleaning up analytics older than {retention_days} days")

        delete_query = f"""
            DELETE FROM learning_analytics
            WHERE timestamp < datetime('now', '-{int(retention_days)} days')
        """

        result = _db.execute_write(delete_query)
        count = result if result else 0

        logger.info(f"Deleted {count} old analytics records")
        return count

    except Exception as e:
        logger.exception(f"Error cleaning up analytics: {e}")
        raise self.retry(exc=e, countdown=300)


@celery_app.task(name='tasks.background_tasks.vacuum_database')
def vacuum_database() -> bool:
    """
    Run VACUUM on SQLite to reclaim disk space after bulk deletions
    PostgreSQL handles autovacuum automatically
    Runs weekly via Celery Beat

    Returns:
        True if vacuum completed successfully
    """
    try:
        from config import system_config as _system_config
        from storage.database import db_manager as _db

        if getattr(_system_config, 'DB_TYPE', 'sqlite') != 'sqlite':
            logger.info("Skipping VACUUM - PostgreSQL handles autovacuum automatically")
            return True

        logger.info("Running VACUUM on SQLite database")
        _db.execute_write("VACUUM")
        logger.info("Database VACUUM completed successfully")
        return True

    except Exception as e:
        logger.exception(f"Error running database VACUUM: {e}")
        return False


# ============================================================================
# AI PROCESSING TASKS
# ============================================================================

@celery_app.task(
    bind=True,
    max_retries=2,
    name='tasks.background_tasks.generate_ai_batch'
)
def generate_ai_batch(self, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate AI responses for multiple requests in batch
    Uses async Ollama client for better performance

    Args:
        requests: List of generation requests

    Returns:
        List of results with responses or errors
    """
    try:
        from utils.async_ollama_client import get_async_ollama_client

        async def process_batch():
            client = await get_async_ollama_client()
            return await client.generate_batch(requests)

        # Run async batch processing
        results = asyncio.run(process_batch())

        logger.info(f"Completed batch AI generation: {len(results)} requests")
        return results

    except (ConnectionError, OSError) as exc:
        logger.exception(f"Error in batch AI generation: {exc}")
        raise self.retry(exc=exc)


# ============================================================================
# DATA EXPORT/DELETE TASKS
# ============================================================================

@celery_app.task(
    bind=True,
    max_retries=1,
    name='tasks.background_tasks.export_user_data'
)
def export_user_data(self, user_id: str, export_format: str = 'json') -> Optional[str]:
    """
    Export all user data (COPPA/GDPR compliance)

    Args:
        user_id: User ID to export
        export_format: Export format (json, csv)

    Returns:
        Path to exported file, or None if failed
    """
    try:
        logger.info(f"Exporting data for user {user_id}")

        # Get user data
        user_query = "SELECT * FROM accounts WHERE parent_id = ?"
        user = db_manager.execute_read(user_query, (user_id,))

        # Get child profiles
        profiles_query = "SELECT * FROM child_profiles WHERE parent_id = ?"
        profiles = db_manager.execute_read(profiles_query, (user_id,))

        # Get all messages for each profile
        all_data = {
            'user': user[0] if user else None,
            'profiles': []
        }

        for profile in profiles:
            profile_id = profile['profile_id']

            # Get conversations
            messages_query = """
                SELECT m.* FROM messages m
                JOIN conversations c ON m.conversation_id = c.conversation_id
                JOIN sessions s ON c.session_id = s.session_id
                WHERE s.profile_id = ?
                ORDER BY m.timestamp DESC
            """
            messages = db_manager.execute_read(messages_query, (profile_id,))

            # Get safety incidents
            incidents_query = "SELECT * FROM safety_incidents WHERE profile_id = ?"
            incidents = db_manager.execute_read(incidents_query, (profile_id,))

            all_data['profiles'].append({
                'profile': profile,
                'messages': messages,
                'incidents': incidents
            })

        # Export to file
        export_dir = system_config.APP_DATA_DIR / 'exports'
        export_dir.mkdir(exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        export_file = export_dir / f"user_{user_id}_{timestamp}.json"

        with open(export_file, 'w') as f:
            json.dump(all_data, f, indent=2, default=str)

        logger.info(f"User data exported to {export_file}")
        return str(export_file)

    except (DB_ERRORS + (OSError,)) as exc:
        logger.exception(f"Error exporting user data: {exc}")
        return None


@celery_app.task(
    bind=True,
    max_retries=0,
    name='tasks.background_tasks.delete_user_data'
)
def delete_user_data(self, user_id: str, grace_period_days: int = 30) -> bool:
    """
    Permanently delete user data (COPPA right to deletion)

    Args:
        user_id: User ID to delete
        grace_period_days: Grace period before permanent deletion

    Returns:
        True if deleted successfully
    """
    try:
        logger.warning(f"Deleting data for user {user_id} (grace period: {grace_period_days} days)")

        # Check if grace period has passed
        deletion_request_query = """
            SELECT deletion_requested_at FROM accounts WHERE parent_id = ?
        """
        result = db_manager.execute_read(deletion_request_query, (user_id,))

        if not result:
            logger.error(f"User {user_id} not found")
            return False

        deletion_requested = result[0].get('deletion_requested_at')
        if not deletion_requested:
            # Mark for deletion
            mark_query = """
                UPDATE accounts
                SET deletion_requested_at = datetime('now')
                WHERE parent_id = ?
            """
            db_manager.execute_write(mark_query, (user_id,))
            logger.info(f"User {user_id} marked for deletion (grace period: {grace_period_days} days)")
            return False

        # Check if grace period passed
        deletion_date = datetime.fromisoformat(deletion_requested)
        if deletion_date.tzinfo is None:
            deletion_date = deletion_date.replace(tzinfo=timezone.utc)
        grace_end = deletion_date + timedelta(days=grace_period_days)

        if datetime.now(timezone.utc) < grace_end:
            logger.info(f"User {user_id} still in grace period (ends {grace_end})")
            return False

        # Grace period passed - permanently delete
        # Delete in order (foreign key constraints)

        # Get child profiles
        profiles_query = "SELECT profile_id FROM child_profiles WHERE parent_id = ?"
        profiles = db_manager.execute_read(profiles_query, (user_id,))

        for profile in profiles:
            profile_id = profile['profile_id']

            # Delete messages
            db_manager.execute_write(
                "DELETE FROM messages WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE profile_id = ?)",
                (profile_id,)
            )

            # Delete sessions
            db_manager.execute_write("DELETE FROM sessions WHERE profile_id = ?", (profile_id,))

            # Delete incidents
            db_manager.execute_write("DELETE FROM safety_incidents WHERE profile_id = ?", (profile_id,))

        # Delete profiles
        db_manager.execute_write("DELETE FROM child_profiles WHERE parent_id = ?", (user_id,))

        # Delete user auth tokens
        db_manager.execute_write("DELETE FROM auth_tokens WHERE parent_id = ?", (user_id,))

        # Delete user
        db_manager.execute_write("DELETE FROM accounts WHERE parent_id = ?", (user_id,))

        logger.warning(f"User {user_id} permanently deleted")
        return True

    except DB_ERRORS as exc:
        logger.exception(f"Error deleting user data: {exc}")
        return False


# Export public interface
__all__ = [
    'safe_dispatch',
    'send_email',
    'send_safety_alert',
    'send_batch_emails',
    'send_daily_safety_digests',
    'cleanup_old_messages',
    'cleanup_old_sessions',
    'cleanup_old_incidents',
    'cleanup_audit_logs',
    'cleanup_ended_sessions',
    'cleanup_analytics',
    'vacuum_database',
    'generate_ai_batch',
    'export_user_data',
    'delete_user_data'
]
