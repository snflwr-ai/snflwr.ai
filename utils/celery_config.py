"""
Celery Configuration
Background task queue for asynchronous processing
Offloads time-consuming operations from API requests

Features:
- Dead letter queue for permanently failed tasks
- Automatic retry with exponential backoff
- Task failure alerting and metrics
- Comprehensive task monitoring
"""

import os
import json
from celery import Celery, signals
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from kombu import Queue, Exchange
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from config import system_config
from utils.logger import get_logger

try:
    from redis.exceptions import RedisError
except ImportError:
    RedisError = OSError  # type: ignore[misc,assignment]

logger = get_logger(__name__)

# Track failed tasks for alerting
_failed_task_counts: Dict[str, int] = {}
_ALERT_THRESHOLD = int(os.getenv("CELERY_FAILURE_ALERT_THRESHOLD", "3"))


# Initialize Celery app
celery_app = Celery(
    "snflwr_tasks", broker=system_config.REDIS_URL, backend=system_config.REDIS_URL
)


# Celery Configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task execution settings
    task_acks_late=True,  # Acknowledge task after completion
    task_reject_on_worker_lost=True,  # Reject task if worker dies
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,  # 4 minutes soft limit
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_persistent=True,  # Persist results to disk
    # Worker settings
    worker_prefetch_multiplier=4,  # Number of tasks to prefetch
    worker_max_tasks_per_child=1000,  # Recycle workers after N tasks
    worker_disable_rate_limits=False,
    # Routing
    task_routes={
        "tasks.background_tasks.send_email": {"queue": "email"},
        "tasks.background_tasks.send_safety_alert": {"queue": "email"},
        "tasks.background_tasks.send_batch_emails": {"queue": "email"},
        "tasks.background_tasks.send_daily_safety_digests": {"queue": "email"},
        "tasks.background_tasks.cleanup_old_messages": {"queue": "maintenance"},
        "tasks.background_tasks.cleanup_old_sessions": {"queue": "maintenance"},
        "tasks.background_tasks.cleanup_old_incidents": {"queue": "maintenance"},
        "tasks.background_tasks.cleanup_audit_logs": {"queue": "maintenance"},
        "tasks.background_tasks.cleanup_ended_sessions": {"queue": "maintenance"},
        "tasks.background_tasks.cleanup_analytics": {"queue": "maintenance"},
        "tasks.background_tasks.vacuum_database": {"queue": "maintenance"},
        "tasks.background_tasks.generate_ai_batch": {"queue": "ai"},
        "tasks.background_tasks.export_user_data": {"queue": "data"},
        "tasks.background_tasks.delete_user_data": {"queue": "data"},
    },
    # Queue definitions with dead letter queue for failed tasks
    task_queues=(
        Queue("default", Exchange("default"), routing_key="default"),
        Queue("email", Exchange("email"), routing_key="email", priority=8),
        Queue("ai", Exchange("ai"), routing_key="ai", priority=6),
        Queue("data", Exchange("data"), routing_key="data", priority=5),
        Queue(
            "maintenance",
            Exchange("maintenance"),
            routing_key="maintenance",
            priority=3,
        ),
        # Dead letter queue for permanently failed tasks
        Queue(
            "dead_letter",
            Exchange("dead_letter"),
            routing_key="dead_letter",
            priority=1,
        ),
    ),
    # Beat schedule (periodic tasks)
    beat_schedule={
        "cleanup-old-messages": {
            "task": "tasks.background_tasks.cleanup_old_messages",
            "schedule": timedelta(hours=6),  # Every 6 hours
            "options": {"queue": "maintenance"},
        },
        "cleanup-old-sessions": {
            "task": "tasks.background_tasks.cleanup_old_sessions",
            "schedule": timedelta(hours=12),  # Every 12 hours
            "options": {"queue": "maintenance"},
        },
        "cleanup-old-incidents": {
            "task": "tasks.background_tasks.cleanup_old_incidents",
            "schedule": timedelta(days=1),  # Daily
            "options": {"queue": "maintenance"},
        },
        "send-daily-safety-digests": {
            "task": "tasks.background_tasks.send_daily_safety_digests",
            "schedule": timedelta(days=1),  # Daily at midnight UTC
            "options": {"queue": "email"},
        },
        "cleanup-audit-logs": {
            "task": "tasks.background_tasks.cleanup_audit_logs",
            "schedule": timedelta(days=1),  # Daily
            "options": {"queue": "maintenance"},
        },
        "cleanup-ended-sessions": {
            "task": "tasks.background_tasks.cleanup_ended_sessions",
            "schedule": timedelta(days=1),  # Daily
            "options": {"queue": "maintenance"},
        },
        "cleanup-analytics": {
            "task": "tasks.background_tasks.cleanup_analytics",
            "schedule": timedelta(days=7),  # Weekly
            "options": {"queue": "maintenance"},
        },
        "vacuum-database": {
            "task": "tasks.background_tasks.vacuum_database",
            "schedule": timedelta(days=7),  # Weekly
            "options": {"queue": "maintenance"},
        },
    },
)


# Import tasks to register them
celery_app.autodiscover_tasks(["tasks"])


# Celery event handlers
@celery_app.task(bind=True)
def debug_task(self):
    """Debug task to test Celery configuration"""
    logger.info(f"Request: {self.request!r}")
    return f"Celery is working! Task ID: {self.request.id}"


# Task monitoring
@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    """Setup periodic tasks after Celery configuration"""
    logger.info("Celery periodic tasks configured")


# ==============================================================================
# Celery Signal Handlers for Task Lifecycle Management
# ==============================================================================


@signals.task_failure.connect
def handle_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **kw,
):
    """
    Handle task failures with alerting and dead letter queue routing.

    This signal fires when a task raises an exception and fails permanently
    (after all retries are exhausted).
    """
    task_name = sender.name if sender else "unknown"

    # Track failure count for this task
    _failed_task_counts[task_name] = _failed_task_counts.get(task_name, 0) + 1
    failure_count = _failed_task_counts[task_name]

    # Log the failure with full context
    logger.error(
        f"Task failed permanently",
        extra={
            "task_id": task_id,
            "task_name": task_name,
            "exception": str(exception),
            "args": args,
            "kwargs": kwargs,
            "failure_count": failure_count,
        },
    )

    # Send to dead letter queue for later inspection/replay
    try:
        dead_letter_payload = {
            "task_id": task_id,
            "task_name": task_name,
            "exception": str(exception),
            "exception_type": type(exception).__name__,
            "args": args,
            "kwargs": kwargs,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "traceback": str(einfo) if einfo else None,
        }
        store_failed_task.delay(dead_letter_payload)
    except (ConnectionError, OSError, RedisError) as e:
        logger.exception(f"Failed to send task to dead letter queue: {e}")

    # Alert if failure threshold exceeded
    if failure_count >= _ALERT_THRESHOLD:
        _send_failure_alert(task_name, failure_count, exception, task_id)

    # Update Prometheus metrics if available
    try:
        from utils.metrics import celery_task_failures

        celery_task_failures.labels(task_name=task_name).inc()
    except ImportError:
        pass


@signals.task_retry.connect
def handle_task_retry(sender=None, request=None, reason=None, einfo=None, **kw):
    """
    Handle task retries for monitoring and logging.

    This signal fires when a task is being retried after a failure.
    """
    task_name = sender.name if sender else "unknown"
    task_id = request.id if request else "unknown"
    retry_count = request.retries if request else 0

    logger.warning(
        f"Task retry attempt",
        extra={
            "task_id": task_id,
            "task_name": task_name,
            "retry_count": retry_count,
            "reason": str(reason),
        },
    )

    # Update Prometheus metrics if available
    try:
        from utils.metrics import celery_task_retries

        celery_task_retries.labels(task_name=task_name).inc()
    except ImportError:
        pass


@signals.task_success.connect
def handle_task_success(sender=None, result=None, **kw):
    """
    Handle task success - reset failure counts.

    This signal fires when a task completes successfully.
    """
    task_name = sender.name if sender else "unknown"

    # Reset failure count on success (task is working again)
    if task_name in _failed_task_counts:
        _failed_task_counts[task_name] = 0

    # Update Prometheus metrics if available
    try:
        from utils.metrics import celery_task_successes

        celery_task_successes.labels(task_name=task_name).inc()
    except ImportError:
        pass


@signals.task_revoked.connect
def handle_task_revoked(
    sender=None, request=None, terminated=None, signum=None, expired=None, **kw
):
    """Handle task revocation (cancellation)."""
    task_name = sender.name if sender else "unknown"
    task_id = request.id if request else "unknown"

    logger.warning(
        f"Task revoked",
        extra={
            "task_id": task_id,
            "task_name": task_name,
            "terminated": terminated,
            "expired": expired,
        },
    )


def _send_failure_alert(
    task_name: str, failure_count: int, exception: Exception, task_id: str
):
    """
    Send alert for repeated task failures.

    Args:
        task_name: Name of the failing task
        failure_count: Number of consecutive failures
        exception: The exception that caused the failure
        task_id: ID of the failed task
    """
    alert_message = (
        f"ALERT: Task '{task_name}' has failed {failure_count} times. "
        f"Last error: {exception}. Task ID: {task_id}"
    )

    logger.critical(alert_message)

    # Try to send email alert if email task is available
    try:
        celery_app.send_task(
            "tasks.background_tasks.send_email",
            kwargs={
                "to": os.getenv("ADMIN_EMAIL", "admin@snflwr.ai"),
                "subject": f"[CRITICAL] Task Failure Alert: {task_name}",
                "body": alert_message,
            },
            queue="email",
        )
    except (ConnectionError, OSError, RedisError) as e:
        logger.error(f"Failed to send failure alert email: {e}")


@celery_app.task(bind=True, queue="dead_letter")
def store_failed_task(self, payload: dict):
    """
    Store failed task information in dead letter queue.

    This task receives failed task metadata for later inspection,
    debugging, or replay.

    Args:
        payload: Dictionary containing failed task metadata
    """
    logger.info(
        f"Stored failed task in dead letter queue",
        extra={
            "task_id": payload.get("task_id"),
            "task_name": payload.get("task_name"),
            "failed_at": payload.get("failed_at"),
        },
    )

    # Store in Redis for persistence if available
    try:
        import redis

        redis_url = system_config.REDIS_URL
        if redis_url:
            r = redis.from_url(redis_url)
            key = f"dlq:{payload.get('task_name')}:{payload.get('task_id')}"
            r.setex(key, timedelta(days=7), json.dumps(payload))
    except (RedisError, ConnectionError, OSError) as e:
        logger.debug(f"Could not store in Redis (optional): {e}")

    return payload


def get_dead_letter_tasks(task_name: Optional[str] = None, limit: int = 100) -> list:
    """
    Retrieve tasks from the dead letter queue.

    Args:
        task_name: Filter by task name (optional)
        limit: Maximum number of tasks to retrieve

    Returns:
        List of failed task payloads
    """
    try:
        import redis

        redis_url = system_config.REDIS_URL
        if not redis_url:
            return []

        r = redis.from_url(redis_url)
        pattern = f"dlq:{task_name}:*" if task_name else "dlq:*"

        keys = r.keys(pattern)[:limit]
        tasks = []

        for key in keys:
            data = r.get(key)
            if data:
                tasks.append(json.loads(data))

        return sorted(tasks, key=lambda x: x.get("failed_at", ""), reverse=True)

    except (RedisError, ConnectionError, OSError, json.JSONDecodeError) as e:
        logger.exception(f"Error retrieving dead letter tasks: {e}")
        return []


def replay_dead_letter_task(task_id: str) -> Optional[str]:
    """
    Replay a task from the dead letter queue.

    Args:
        task_id: The original task ID to replay

    Returns:
        New task ID if replayed successfully, None otherwise
    """
    try:
        import redis

        redis_url = system_config.REDIS_URL
        if not redis_url:
            logger.error("Redis not configured for dead letter queue")
            return None

        r = redis.from_url(redis_url)

        # Find the task in DLQ
        for key in r.keys("dlq:*"):
            data = r.get(key)
            if data:
                payload = json.loads(data)
                if payload.get("task_id") == task_id:
                    # Replay the task
                    result = celery_app.send_task(
                        payload["task_name"],
                        args=payload.get("args", []),
                        kwargs=payload.get("kwargs", {}),
                    )

                    # Remove from DLQ
                    r.delete(key)

                    logger.info(
                        f"Replayed dead letter task",
                        extra={
                            "original_task_id": task_id,
                            "new_task_id": result.id,
                            "task_name": payload["task_name"],
                        },
                    )

                    return result.id

        logger.warning(f"Task {task_id} not found in dead letter queue")
        return None

    except (RedisError, ConnectionError, OSError, json.JSONDecodeError) as e:
        logger.exception(f"Error replaying dead letter task: {e}")
        return None


# Error handling
@celery_app.task(bind=True, max_retries=3)
def handle_task_error(self, exc, task_id, args, kwargs, einfo):
    """Handle task errors with retry logic"""
    logger.error(
        f"Task {task_id} failed: {exc}\n"
        f"Args: {args}\n"
        f"Kwargs: {kwargs}\n"
        f"Traceback: {einfo}"
    )

    # Retry with exponential backoff
    try:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    except MaxRetriesExceededError:
        logger.critical(f"Task {task_id} exceeded max retries")


# Health check
def check_celery_health() -> dict:
    """
    Check Celery cluster health

    Returns:
        dict: Health status of workers, queues, and tasks
    """
    try:
        # Inspect workers
        inspect = celery_app.control.inspect()

        # Get active workers
        active_workers = inspect.active()
        registered_tasks = inspect.registered()
        active_queues = inspect.active_queues()

        # Count workers
        worker_count = len(active_workers) if active_workers else 0

        # Check if workers are responding
        stats = inspect.stats()
        healthy = stats is not None and len(stats) > 0

        return {
            "healthy": healthy,
            "worker_count": worker_count,
            "workers": list(active_workers.keys()) if active_workers else [],
            "registered_tasks": (
                list(registered_tasks.values())[0] if registered_tasks else []
            ),
            "queues": list(active_queues.values())[0] if active_queues else [],
        }

    except (ConnectionError, OSError, RedisError) as e:
        logger.exception(f"Error checking Celery health: {e}")
        return {"healthy": False, "error": str(e)}


# Queue statistics
def get_queue_stats() -> dict:
    """
    Get statistics for all queues

    Returns:
        dict: Queue statistics (pending, active, completed)
    """
    try:
        inspect = celery_app.control.inspect()

        # Get reserved (active) tasks
        reserved = inspect.reserved()
        active = inspect.active()

        # Count tasks by queue
        queue_stats = {}

        if reserved:
            for worker, tasks in reserved.items():
                for task in tasks:
                    queue = task.get("delivery_info", {}).get("routing_key", "default")
                    if queue not in queue_stats:
                        queue_stats[queue] = {"reserved": 0, "active": 0}
                    queue_stats[queue]["reserved"] += 1

        if active:
            for worker, tasks in active.items():
                for task in tasks:
                    queue = task.get("delivery_info", {}).get("routing_key", "default")
                    if queue not in queue_stats:
                        queue_stats[queue] = {"reserved": 0, "active": 0}
                    queue_stats[queue]["active"] += 1

        return queue_stats

    except (ConnectionError, OSError, RedisError) as e:
        logger.exception(f"Error getting queue stats: {e}")
        return {}


# Task utilities
def purge_queue(queue_name: str) -> int:
    """
    Purge all tasks from a specific queue

    Args:
        queue_name: Name of queue to purge

    Returns:
        Number of tasks purged
    """
    try:
        with celery_app.connection_or_acquire() as conn:
            count = conn.default_channel.queue_purge(queue_name) or 0
        logger.warning(f"Purged {count} tasks from queue: {queue_name}")
        return count
    except (ConnectionError, OSError, RedisError) as e:
        logger.exception(f"Error purging queue {queue_name}: {e}")
        return 0


# Export public interface
__all__ = [
    "celery_app",
    "check_celery_health",
    "get_queue_stats",
    "purge_queue",
    "debug_task",
    "get_dead_letter_tasks",
    "replay_dead_letter_task",
    "store_failed_task",
]
