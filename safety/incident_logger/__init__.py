# safety/incident_logger/__init__.py  (was safety/incident_logger.py — decomposed)
"""
Safety Incident Logging and Reporting System
Comprehensive tracking, analysis, and reporting of safety incidents with real-time WebSocket notifications.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import safety_config
from core.email_crypto import get_email_crypto
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager
from utils.logger import get_logger, sanitize_log_value

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    InvalidToken = Exception  # type: ignore[misc,assignment]

logger = get_logger(__name__)

from safety.incident_logger.escalation import _IncidentEscalationMixin
from safety.incident_logger.models import (
    SafetyIncident,
    get_email_system,
    get_websocket_manager,
)
from safety.incident_logger.queries import _IncidentQueryMixin


class IncidentLogger(_IncidentQueryMixin, _IncidentEscalationMixin):
    """
    Comprehensive safety incident logging and reporting system.
    Read methods live in _IncidentQueryMixin; escalation in
    _IncidentEscalationMixin; write/lifecycle below.
    """

    def __init__(self, db=None):
        """Initialize incident logger

        Accept an optional `db` for test injection. If not provided, use module
        level `db_manager` singleton.
        """
        self.db = db or db_manager
        self.encryption = encryption_manager

        logger.info("Incident logger initialized")

    def log_incident(
        self,
        profile_id: str,
        incident_type: str,
        severity: str,
        content_snippet: str,
        metadata: Optional[Dict] = None,
        session_id: Optional[str] = None,
        send_alert: bool = True,
    ) -> Tuple[bool, Optional[int]]:
        """
        Log a safety incident and optionally send parent alert

        Args:
            profile_id: Child profile ID
            session_id: Session ID if applicable
            incident_type: Type/category of incident
            severity: Severity level ('minor', 'major', 'critical')
            content_snippet: Sample of concerning content
            metadata: Additional context information
            send_alert: Whether to send parent alert (default True)

        Returns:
            Tuple of (success, incident_id or None)
        """
        try:
            # Validate severity
            if severity not in ["minor", "major", "critical"]:
                logger.error(f"Invalid severity: {severity}")
                return False, None

            # Encrypt content snippet for privacy
            encrypted_snippet = self.encryption.encrypt_string(content_snippet[:500])

            # Encrypt metadata if present
            encrypted_metadata = None
            if metadata:
                encrypted_metadata = self.encryption.encrypt_dict(metadata)

            # Insert incident
            self.db.execute_write(
                """
                INSERT INTO safety_incidents (
                    profile_id, session_id, incident_type, severity,
                    content_snippet, timestamp, parent_notified,
                    resolved, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    session_id,
                    incident_type,
                    severity,
                    encrypted_snippet,
                    datetime.now(timezone.utc).isoformat(),
                    False,
                    False,
                    encrypted_metadata,
                ),
            )

            # Get incident ID
            result = self.db.execute_query(
                "SELECT incident_id FROM safety_incidents WHERE profile_id = ? ORDER BY incident_id DESC LIMIT 1",
                (profile_id,),
            )

            if result:
                incident_id = result[0]["incident_id"]
                logger.info(
                    f"Incident logged: ID {sanitize_log_value(incident_id)!r}, severity: {severity!r}, profile: {sanitize_log_value(profile_id)!r}"
                )

                # Broadcast incident via WebSocket for real-time monitoring
                self._broadcast_incident_websocket(
                    profile_id, incident_id, severity, incident_type, content_snippet
                )

                # Send parent alert for major/critical incidents
                if send_alert and severity in ["major", "critical"]:
                    self._send_parent_alert(
                        profile_id, incident_id, severity, incident_type
                    )

                return True, incident_id

            return False, None

        except DB_ERRORS as e:
            logger.error(f"Failed to log incident: {e}")
            # Fail-safe: a crisis incident must never be silently dropped. The DB
            # insert can fail for a profile-less session whose synthetic
            # profile_id can't satisfy the FK we keep for COPPA cascade-delete.
            # For major/critical severities, escalate to a human via an operator
            # alert (the incident is still captured in the safety log file).
            if severity in ("major", "critical"):
                try:
                    from core.email_service import email_service

                    email_service.send_operator_alert(
                        subject=f"Crisis incident could not be recorded ({incident_type})",
                        description=(
                            f"A {severity} safety incident (type={incident_type}) "
                            f"for profile {sanitize_log_value(profile_id)!r} failed "
                            f"to persist to the database ({e}). It is captured in "
                            "the safety incident log file. Review immediately."
                        ),
                    )
                except Exception:
                    pass  # operator alert is best-effort; never raise from logger
            return False, None

    def mark_parent_notified(self, incident_id: int) -> bool:
        """
        Mark incident as parent-notified

        Args:
            incident_id: Incident identifier

        Returns:
            True if successful
        """
        try:
            self.db.execute_write(
                """
                UPDATE safety_incidents
                SET parent_notified = 1, parent_notified_at = ?
                WHERE incident_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), incident_id),
            )

            logger.info(f"Incident {incident_id} marked as parent-notified")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to mark incident as notified: {e}")
            return False

    def resolve_incident(self, incident_id: int, resolution_notes: str) -> bool:
        """
        Mark incident as resolved

        Args:
            incident_id: Incident identifier
            resolution_notes: Notes on resolution

        Returns:
            True if successful
        """
        try:
            # Encrypt resolution notes for privacy
            encrypted_notes = (
                self.encryption.encrypt_string(resolution_notes)
                if resolution_notes
                else None
            )

            self.db.execute_write(
                """
                UPDATE safety_incidents
                SET resolved = 1, resolved_at = ?, resolution_notes = ?
                WHERE incident_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), encrypted_notes, incident_id),
            )

            logger.info(f"Incident {sanitize_log_value(incident_id)!r} resolved")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to resolve incident: {e}")
            return False

    def cleanup_old_incidents(self, retention_days: Optional[int] = None):
        """
        Clean up old resolved incidents

        Args:
            retention_days: Days to retain (uses config default if not specified)
        """
        try:
            retention_days = retention_days or safety_config.SAFETY_LOG_RETENTION_DAYS
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=retention_days)
            ).isoformat()

            # Delete old resolved incidents
            self.db.execute_write(
                """
                DELETE FROM safety_incidents
                WHERE resolved = 1 AND resolved_at < ?
                """,
                (cutoff_date,),
            )

            logger.info(f"Cleaned up incidents older than {retention_days} days")

        except DB_ERRORS as e:
            logger.error(f"Failed to cleanup old incidents: {e}")


# Singleton instance


incident_logger = IncidentLogger()


# Export public interface
__all__ = ["IncidentLogger", "SafetyIncident", "incident_logger"]
