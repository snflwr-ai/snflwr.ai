# utils/data_retention.py
"""
COPPA-Compliant Data Retention Management
Automated cleanup of old data according to retention policies
"""

import threading
import schedule
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import safety_config, system_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)


class DataRetentionManager:
    """
    Manages automated data retention and cleanup
    COPPA/FERPA compliant data minimization
    """

    def __init__(self):
        """Initialize data retention manager"""
        self.db = db_manager
        self.running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        logger.info("Data Retention Manager initialized")

    def start_scheduler(self):
        """Start the automated cleanup scheduler"""
        if self.running:
            logger.warning("Data retention scheduler already running")
            return

        self.running = True
        self._stop_event.clear()

        # Schedule daily cleanup
        schedule.every().day.at(f"{safety_config.DATA_CLEANUP_HOUR:02d}:00").do(
            self.run_all_cleanup_tasks
        )

        # Start scheduler thread
        self.scheduler_thread = threading.Thread(
            target=self._run_scheduler,
            daemon=True,
            name="DataRetentionScheduler"
        )
        self.scheduler_thread.start()

        logger.info(f"Data retention scheduler started (daily at {safety_config.DATA_CLEANUP_HOUR:02d}:00)")

    def stop_scheduler(self):
        """Stop the automated cleanup scheduler"""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()

        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)

        schedule.clear()
        logger.info("Data retention scheduler stopped")

    def _run_scheduler(self):
        """Internal scheduler loop"""
        while self.running and not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(60)  # Check every minute

    def run_all_cleanup_tasks(self):
        """
        Run all data retention cleanup tasks
        Called automatically by scheduler or can be invoked manually
        """
        if not safety_config.DATA_CLEANUP_ENABLED:
            logger.info("Data cleanup is disabled in configuration")
            return

        logger.info("Starting automated data retention cleanup")

        results = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'tasks': {}
        }

        # 1. Cleanup safety incidents
        try:
            deleted_count = self.cleanup_safety_incidents()
            results['tasks']['safety_incidents'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Safety incidents cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['safety_incidents'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Safety incidents cleanup failed: {e}")

        # 2. Cleanup audit logs
        try:
            deleted_count = self.cleanup_audit_logs()
            results['tasks']['audit_logs'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Audit logs cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['audit_logs'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Audit logs cleanup failed: {e}")

        # 3. Cleanup old sessions
        try:
            deleted_count = self.cleanup_sessions()
            results['tasks']['sessions'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Sessions cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['sessions'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Sessions cleanup failed: {e}")

        # 4. Cleanup old conversations
        try:
            deleted_count = self.cleanup_conversations()
            results['tasks']['conversations'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Conversations cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['conversations'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Conversations cleanup failed: {e}")

        # 5. Cleanup old analytics
        try:
            deleted_count = self.cleanup_analytics()
            results['tasks']['analytics'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Analytics cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['analytics'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Analytics cleanup failed: {e}")

        # 6. Cleanup expired auth tokens
        try:
            deleted_count = self.cleanup_expired_tokens()
            results['tasks']['auth_tokens'] = {
                'status': 'success',
                'deleted_count': deleted_count
            }
            logger.info(f"Auth tokens cleanup: {deleted_count} records deleted")
        except DB_ERRORS as e:
            results['tasks']['auth_tokens'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Auth tokens cleanup failed: {e}")

        # 7. Vacuum database to reclaim space
        try:
            self.vacuum_database()
            results['tasks']['vacuum'] = {'status': 'success'}
            logger.info("Database vacuum completed")
        except DB_ERRORS as e:
            results['tasks']['vacuum'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"Database vacuum failed: {e}")

        # Log summary to audit trail
        self._log_cleanup_summary(results)

        logger.info("Automated data retention cleanup completed")
        return results

    def cleanup_safety_incidents(self) -> int:
        """
        Clean up old resolved safety incidents
        Retention period: SAFETY_LOG_RETENTION_DAYS (default 90 days)

        Returns:
            Number of records deleted
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=safety_config.SAFETY_LOG_RETENTION_DAYS)
        ).isoformat()

        # Count records to be deleted
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM safety_incidents
            WHERE resolved = 1 AND resolved_at < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete old resolved incidents
            self.db.execute_write(
                """
                DELETE FROM safety_incidents
                WHERE resolved = 1 AND resolved_at < ?
                """,
                (cutoff_date,)
            )

            # Log to audit trail
            self._audit_log(
                event_type='data_retention',
                action=f'Deleted {count} old safety incidents (older than {safety_config.SAFETY_LOG_RETENTION_DAYS} days)',
                success=True
            )

        return count

    def cleanup_audit_logs(self) -> int:
        """
        Clean up old audit logs
        Retention period: AUDIT_LOG_RETENTION_DAYS (default 365 days)

        Returns:
            Number of records deleted
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=safety_config.AUDIT_LOG_RETENTION_DAYS)
        ).isoformat()

        # Count records
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM audit_log
            WHERE timestamp < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete old audit logs
            self.db.execute_write(
                """
                DELETE FROM audit_log
                WHERE timestamp < ?
                """,
                (cutoff_date,)
            )

        return count

    def cleanup_sessions(self) -> int:
        """
        Clean up old ended sessions
        Retention period: SESSION_RETENTION_DAYS (default 180 days)

        Returns:
            Number of records deleted
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=safety_config.SESSION_RETENTION_DAYS)
        ).isoformat()

        # Count records
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM sessions
            WHERE ended_at IS NOT NULL AND ended_at < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete old sessions
            self.db.execute_write(
                """
                DELETE FROM sessions
                WHERE ended_at IS NOT NULL AND ended_at < ?
                """,
                (cutoff_date,)
            )

            # Log to audit trail
            self._audit_log(
                event_type='data_retention',
                action=f'Deleted {count} old sessions (older than {safety_config.SESSION_RETENTION_DAYS} days)',
                success=True
            )

        return count

    def cleanup_conversations(self) -> int:
        """
        Clean up old conversations and associated messages
        Retention period: CONVERSATION_RETENTION_DAYS (default 180 days)

        Note: Parents should export important conversations before they are deleted

        Returns:
            Number of records deleted
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=safety_config.CONVERSATION_RETENTION_DAYS)
        ).isoformat()

        # Count conversations to be deleted
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM conversations
            WHERE updated_at < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete associated messages first (cascade)
            self.db.execute_write(
                """
                DELETE FROM messages
                WHERE conversation_id IN (
                    SELECT conversation_id
                    FROM conversations
                    WHERE updated_at < ?
                )
                """,
                (cutoff_date,)
            )

            # Delete conversations
            self.db.execute_write(
                """
                DELETE FROM conversations
                WHERE updated_at < ?
                """,
                (cutoff_date,)
            )

            # Log to audit trail
            self._audit_log(
                event_type='data_retention',
                action=f'Deleted {count} old conversations (older than {safety_config.CONVERSATION_RETENTION_DAYS} days)',
                success=True
            )

        return count

    def cleanup_analytics(self) -> int:
        """
        Clean up old analytics data
        Retention period: ANALYTICS_RETENTION_DAYS (default 730 days / 2 years)

        Returns:
            Number of records deleted
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=safety_config.ANALYTICS_RETENTION_DAYS)
        ).isoformat()

        # Count records
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM learning_analytics
            WHERE date < ?
            """,
            (cutoff_date,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete old analytics
            self.db.execute_write(
                """
                DELETE FROM learning_analytics
                WHERE date < ?
                """,
                (cutoff_date,)
            )

            # Log to audit trail
            self._audit_log(
                event_type='data_retention',
                action=f'Deleted {count} old analytics records (older than {safety_config.ANALYTICS_RETENTION_DAYS} days)',
                success=True
            )

        return count

    def cleanup_expired_tokens(self) -> int:
        """
        Clean up expired authentication tokens

        Returns:
            Number of records deleted
        """
        now = datetime.now(timezone.utc).isoformat()

        # Count expired tokens
        count_result = self.db.execute_query(
            """
            SELECT COUNT(*) as count
            FROM auth_tokens
            WHERE expires_at < ? OR is_valid = 0
            """,
            (now,)
        )
        count = count_result[0]['count'] if count_result else 0

        if count > 0:
            # Delete expired and invalid tokens
            self.db.execute_write(
                """
                DELETE FROM auth_tokens
                WHERE expires_at < ? OR is_valid = 0
                """,
                (now,)
            )

        return count

    def vacuum_database(self):
        """
        Vacuum the database to reclaim disk space
        Should be run after bulk deletions
        """
        self.db.execute_write("VACUUM")
        logger.info("Database vacuumed to reclaim disk space")

    def get_retention_summary(self) -> Dict:
        """
        Get summary of data retention policies and current data volumes

        Returns:
            Dictionary with retention policy and data statistics
        """
        # Always return retention policy, even if data volumes can't be retrieved
        result = {
            'retention_policy': safety_config.get_retention_policy(),
            'cleanup_enabled': safety_config.DATA_CLEANUP_ENABLED,
            'cleanup_schedule': f"Daily at {safety_config.DATA_CLEANUP_HOUR:02d}:00"
        }

        try:
            # Get record counts by table
            tables_info = []

            # Safety incidents
            try:
                incidents = self.db.execute_query(
                    "SELECT COUNT(*) as total, SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved FROM safety_incidents"
                )
                tables_info.append({
                    'table': 'safety_incidents',
                    'retention_days': safety_config.SAFETY_LOG_RETENTION_DAYS,
                    'total_records': incidents[0]['total'] if incidents else 0,
                    'resolved_records': incidents[0]['resolved'] if incidents else 0
                })
            except DB_ERRORS as e:
                logger.debug(f"Failed to query safety_incidents count: {e}")

            # Audit logs
            try:
                audit = self.db.execute_query("SELECT COUNT(*) as total FROM audit_log")
                tables_info.append({
                    'table': 'audit_log',
                    'retention_days': safety_config.AUDIT_LOG_RETENTION_DAYS,
                    'total_records': audit[0]['total'] if audit else 0
                })
            except DB_ERRORS as e:
                logger.debug(f"Failed to query audit_log count: {e}")

            # Sessions
            try:
                sessions = self.db.execute_query(
                    "SELECT COUNT(*) as total, SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) as ended FROM sessions"
                )
                tables_info.append({
                    'table': 'sessions',
                    'retention_days': safety_config.SESSION_RETENTION_DAYS,
                    'total_records': sessions[0]['total'] if sessions else 0,
                    'ended_sessions': sessions[0]['ended'] if sessions else 0
                })
            except DB_ERRORS as e:
                logger.debug(f"Failed to query sessions count: {e}")

            # Conversations
            try:
                conversations = self.db.execute_query("SELECT COUNT(*) as total FROM conversations")
                tables_info.append({
                    'table': 'conversations',
                    'retention_days': safety_config.CONVERSATION_RETENTION_DAYS,
                    'total_records': conversations[0]['total'] if conversations else 0
                })
            except DB_ERRORS as e:
                logger.debug(f"Failed to query conversations count: {e}")

            # Analytics
            try:
                analytics = self.db.execute_query("SELECT COUNT(*) as total FROM learning_analytics")
                tables_info.append({
                    'table': 'learning_analytics',
                    'retention_days': safety_config.ANALYTICS_RETENTION_DAYS,
                    'total_records': analytics[0]['total'] if analytics else 0
                })
            except DB_ERRORS as e:
                logger.debug(f"Failed to query learning_analytics count: {e}")

            result['data_volumes'] = tables_info

        except DB_ERRORS as e:
            logger.error(f"Failed to get data volumes: {e}")
            result['data_volumes'] = []

        return result

    def _audit_log(self, event_type: str, action: str, success: bool):
        """Log cleanup action to audit trail"""
        try:
            self.db.execute_write(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, user_id, user_type, action,
                    ip_address, user_agent, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    event_type,
                    'system',
                    'system',
                    action,
                    'localhost',
                    'DataRetentionManager',
                    1 if success else 0
                )
            )
        except DB_ERRORS as e:
            logger.error(f"Failed to write audit log: {e}")

    def _log_cleanup_summary(self, results: Dict):
        """Log cleanup summary to audit trail"""
        total_deleted = sum(
            task.get('deleted_count', 0)
            for task in results['tasks'].values()
            if isinstance(task, dict)
        )

        summary = f"Data retention cleanup completed: {total_deleted} total records deleted"
        self._audit_log('data_retention', summary, True)


# Singleton instance
data_retention_manager = DataRetentionManager()


# Export public interface
__all__ = [
    'DataRetentionManager',
    'data_retention_manager'
]
