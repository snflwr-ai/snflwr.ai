# utils/error_tracking.py
"""
Production Error Tracking and Monitoring System
Comprehensive error tracking, aggregation, and alerting for production deployment
"""

import os
import traceback
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import defaultdict
import threading
import hashlib

from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ErrorRecord:
    """Detailed error record"""
    error_id: int
    error_hash: str
    error_type: str
    error_message: str
    module: str
    function: str
    line_number: int
    stack_trace: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    severity: str
    resolved: bool
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    context: Optional[Dict] = None


class ErrorTracker:
    """
    Production-grade error tracking system
    Aggregates, deduplicates, and alerts on application errors
    """

    def __init__(self):
        """Initialize error tracker"""
        self.db = db_manager
        self._error_cache: Dict[str, List[datetime]] = defaultdict(list)
        self._cache_lock = threading.Lock()

        # Alert thresholds
        self.CRITICAL_ERROR_THRESHOLD = 10  # Alert after 10 occurrences in 1 hour
        self.ERROR_TIME_WINDOW = 3600  # 1 hour in seconds

        logger.info("Error Tracker initialized")

    def capture_exception(
        self,
        exception: Exception,
        severity: str = 'error',
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> int:
        """
        Capture and log an exception with full context

        Args:
            exception: The exception object
            severity: 'critical', 'error', 'warning'
            user_id: Optional user ID
            session_id: Optional session ID
            context: Additional context data

        Returns:
            Error ID
        """
        try:
            # Extract exception details
            exc_type = type(exception).__name__
            exc_message = str(exception)
            exc_tb = exception.__traceback__

            # Get stack trace
            stack_trace = ''.join(traceback.format_exception(
                type(exception), exception, exc_tb
            ))

            # Extract location info
            if exc_tb:
                frame = traceback.extract_tb(exc_tb)[-1]
                module = os.path.basename(frame.filename)
                function = frame.name
                line_number = frame.lineno
            else:
                module = 'unknown'
                function = 'unknown'
                line_number = 0

            # Generate error hash for deduplication
            error_hash = self._generate_error_hash(
                exc_type, exc_message, module, function, line_number
            )

            # Check if this error exists
            existing_error = self._get_error_by_hash(error_hash)

            if existing_error:
                # Update existing error
                error_id = self._update_error_occurrence(existing_error['error_id'])
            else:
                # Create new error record
                error_id = self._create_error_record(
                    error_hash=error_hash,
                    error_type=exc_type,
                    error_message=exc_message,
                    module=module,
                    function=function,
                    line_number=line_number,
                    stack_trace=stack_trace,
                    severity=severity,
                    user_id=user_id,
                    session_id=session_id,
                    context=context
                )

            # Check if we should alert
            self._check_alert_threshold(error_hash, severity)

            # Log to standard logger
            logger.error(
                f"Exception captured: {exc_type} in {module}:{line_number}",
                extra={
                    'error_id': error_id,
                    'error_hash': error_hash,
                    'user_id': user_id
                }
            )

            return error_id

        except Exception as e:  # Intentional catch-all: error tracker must not crash
            # Fallback logging if error tracking fails
            logger.critical(f"Error tracker failed: {e}")
            logger.exception(exception)
            return -1

    def capture_error(
        self,
        error_type: str,
        error_message: str,
        severity: str = 'error',
        module: Optional[str] = None,
        function: Optional[str] = None,
        line_number: Optional[int] = None,
        stack_trace: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> int:
        """
        Capture a custom error without exception object

        Args:
            error_type: Type/category of error
            error_message: Error description
            severity: 'critical', 'error', 'warning'
            module: Module name
            function: Function name
            line_number: Line number
            stack_trace: Optional stack trace
            user_id: Optional user ID
            session_id: Optional session ID
            context: Additional context

        Returns:
            Error ID
        """
        try:
            # Get caller info if not provided
            if not module or not function:
                frame = sys._getframe(1)
                module = module or os.path.basename(frame.f_code.co_filename)
                function = function or frame.f_code.co_name
                line_number = line_number or frame.f_lineno

            # Generate error hash
            error_hash = self._generate_error_hash(
                error_type, error_message, module, function, line_number or 0
            )

            # Check if error exists
            existing_error = self._get_error_by_hash(error_hash)

            if existing_error:
                error_id = self._update_error_occurrence(existing_error['error_id'])
            else:
                error_id = self._create_error_record(
                    error_hash=error_hash,
                    error_type=error_type,
                    error_message=error_message,
                    module=module or 'unknown',
                    function=function or 'unknown',
                    line_number=line_number or 0,
                    stack_trace=stack_trace or '',
                    severity=severity,
                    user_id=user_id,
                    session_id=session_id,
                    context=context
                )

            # Check alert threshold
            self._check_alert_threshold(error_hash, severity)

            return error_id

        except Exception as e:  # Intentional catch-all: error tracker must not crash
            logger.critical(f"Error capture failed: {e}")
            return -1

    def _generate_error_hash(
        self,
        error_type: str,
        error_message: str,
        module: str,
        function: str,
        line_number: int
    ) -> str:
        """Generate unique hash for error deduplication"""
        hash_input = f"{error_type}:{module}:{function}:{line_number}"
        return hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest()[:16]

    def _get_error_by_hash(self, error_hash: str) -> Optional[Dict]:
        """Get existing error by hash"""
        results = self.db.execute_query(
            """
            SELECT error_id, error_hash, occurrence_count
            FROM error_tracking
            WHERE error_hash = ? AND resolved = 0
            """,
            (error_hash,)
        )
        return results[0] if results else None

    def _create_error_record(
        self,
        error_hash: str,
        error_type: str,
        error_message: str,
        module: str,
        function: str,
        line_number: int,
        stack_trace: str,
        severity: str,
        user_id: Optional[str],
        session_id: Optional[str],
        context: Optional[Dict]
    ) -> int:
        """Create new error record"""
        now = datetime.now(timezone.utc).isoformat()

        self.db.execute_write(
            """
            INSERT INTO error_tracking (
                error_hash, error_type, error_message, module, function,
                line_number, stack_trace, first_seen, last_seen,
                occurrence_count, severity, resolved, user_id, session_id, context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error_hash, error_type, error_message, module, function,
                line_number, stack_trace, now, now,
                1, severity, False, user_id, session_id,
                str(context) if context else None
            )
        )

        # Get the error ID
        result = self.db.execute_query(
            "SELECT error_id FROM error_tracking WHERE error_hash = ? ORDER BY error_id DESC LIMIT 1",
            (error_hash,)
        )
        return result[0]['error_id'] if result else -1

    def _update_error_occurrence(self, error_id: int) -> int:
        """Update error occurrence count and last_seen"""
        now = datetime.now(timezone.utc).isoformat()

        self.db.execute_write(
            """
            UPDATE error_tracking
            SET occurrence_count = occurrence_count + 1,
                last_seen = ?
            WHERE error_id = ?
            """,
            (now, error_id)
        )

        return error_id

    def _check_alert_threshold(self, error_hash: str, severity: str):
        """Check if error has exceeded alert threshold"""
        with self._cache_lock:
            now = datetime.now(timezone.utc)

            # Add current occurrence
            self._error_cache[error_hash].append(now)

            # Remove old occurrences outside time window
            cutoff_time = now - timedelta(seconds=self.ERROR_TIME_WINDOW)
            self._error_cache[error_hash] = [
                t for t in self._error_cache[error_hash]
                if t > cutoff_time
            ]

            # Check threshold
            occurrence_count = len(self._error_cache[error_hash])

            if severity == 'critical' and occurrence_count >= 1:
                self._send_alert(error_hash, occurrence_count, severity)
            elif occurrence_count >= self.CRITICAL_ERROR_THRESHOLD:
                self._send_alert(error_hash, occurrence_count, severity)

    def _send_alert(self, error_hash: str, count: int, severity: str):
        """Send alert for frequent/critical error via email if SMTP is configured."""
        logger.warning(
            f"Error alert: {error_hash} occurred {count} times in last hour (severity: {severity})"
        )
        try:
            from utils.email_alerts import email_alert_system
            from config import system_config
            if system_config.SMTP_ENABLED and system_config.ADMIN_EMAIL:
                error_summary = (
                    f"[{severity.upper()}] Error {error_hash} — "
                    f"{count} occurrence(s) in the last hour"
                )
                email_alert_system.send_error_alert(
                    admin_email=system_config.ADMIN_EMAIL,
                    error_summary=error_summary,
                    error_count=count,
                )
        except Exception as e:
            logger.error(f"Failed to send error alert email: {e}")

    def get_error_summary(self, days: int = 7) -> Dict:
        """
        Get error summary for the past N days

        Args:
            days: Number of days to look back

        Returns:
            Summary dictionary
        """
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Total errors
        total_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM error_tracking
            WHERE first_seen >= ?
            """,
            (cutoff_date,)
        )
        total_errors = total_result[0]['count'] if total_result else 0

        # By severity
        severity_result = self.db.execute_query(
            """
            SELECT severity, COUNT(*) as count, SUM(occurrence_count) as total_occurrences
            FROM error_tracking
            WHERE first_seen >= ?
            GROUP BY severity
            """,
            (cutoff_date,)
        )

        # Unresolved errors
        unresolved_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM error_tracking
            WHERE resolved = 0
            """,
            ()
        )
        unresolved_errors = unresolved_result[0]['count'] if unresolved_result else 0

        # Most frequent errors
        frequent_errors = self.db.execute_query(
            """
            SELECT error_type, error_message, module, occurrence_count, severity
            FROM error_tracking
            WHERE first_seen >= ? AND resolved = 0
            ORDER BY occurrence_count DESC
            LIMIT 10
            """,
            (cutoff_date,)
        )

        return {
            'period_days': days,
            'total_unique_errors': total_errors,
            'unresolved_errors': unresolved_errors,
            'by_severity': [dict(row) for row in severity_result],
            'most_frequent': [dict(row) for row in frequent_errors]
        }

    def get_error_details(self, error_id: int) -> Optional[ErrorRecord]:
        """Get detailed information about a specific error"""
        results = self.db.execute_query(
            """
            SELECT * FROM error_tracking WHERE error_id = ?
            """,
            (error_id,)
        )

        if not results:
            return None

        row = results[0]
        return ErrorRecord(
            error_id=row['error_id'],
            error_hash=row['error_hash'],
            error_type=row['error_type'],
            error_message=row['error_message'],
            module=row['module'],
            function=row['function'],
            line_number=row['line_number'],
            stack_trace=row['stack_trace'],
            first_seen=datetime.fromisoformat(row['first_seen']),
            last_seen=datetime.fromisoformat(row['last_seen']),
            occurrence_count=row['occurrence_count'],
            severity=row['severity'],
            resolved=bool(row['resolved']),
            user_id=row.get('user_id'),
            session_id=row.get('session_id'),
            context=json.loads(row['context']) if row.get('context') else None
        )

    def mark_resolved(self, error_id: int, resolution_notes: Optional[str] = None) -> bool:
        """Mark an error as resolved"""
        try:
            self.db.execute_write(
                """
                UPDATE error_tracking
                SET resolved = 1, resolution_notes = ?, resolved_at = ?
                WHERE error_id = ?
                """,
                (resolution_notes, datetime.now(timezone.utc).isoformat(), error_id)
            )

            logger.info(f"Error {error_id} marked as resolved")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to mark error as resolved: {e}")
            return False

    def cleanup_old_errors(self, retention_days: int = 90) -> int:
        """
        Clean up old resolved errors

        Args:
            retention_days: Days to retain resolved errors

        Returns:
            Number of errors deleted
        """
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

        # Count before deletion
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM error_tracking
            WHERE resolved = 1 AND resolved_at < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            self.db.execute_write(
                """
                DELETE FROM error_tracking
                WHERE resolved = 1 AND resolved_at < ?
                """,
                (cutoff_date,)
            )

            logger.info(f"Cleaned up {count} old resolved errors")

        return count


# Global exception handler decorator
def track_exceptions(severity: str = 'error'):
    """
    Decorator to automatically track exceptions from functions

    Usage:
        @track_exceptions(severity='critical')
        def my_function():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:  # Intentional catch-all: error tracker must not crash
                error_tracker.capture_exception(
                    e,
                    severity=severity,
                    context={
                        'function': func.__name__,
                        'args': str(args)[:200],
                        'kwargs': str(kwargs)[:200]
                    }
                )
                raise  # Re-raise the exception
        return wrapper
    return decorator


# Singleton instance
error_tracker = ErrorTracker()


# Export public interface
__all__ = [
    'ErrorTracker',
    'ErrorRecord',
    'error_tracker',
    'track_exceptions'
]
