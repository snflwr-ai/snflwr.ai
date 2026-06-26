"""Incident escalation: parent alerts, crisis text, websocket broadcast (mixin).

Extracted verbatim from the former monolithic safety/incident_logger.py.
"""

import asyncio
from datetime import datetime, timezone

from core.email_crypto import get_email_crypto
from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger, sanitize_log_value

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    InvalidToken = Exception  # type: ignore[misc,assignment]

logger = get_logger(__name__)

from safety.incident_logger.models import (
    get_email_system,
    get_websocket_manager,
)


class _IncidentEscalationMixin:
    """Escalation methods for IncidentLogger (composed in __init__.py)."""

    def _send_parent_alert(
        self, profile_id: str, incident_id: int, severity: str, incident_type: str
    ):
        """
        Send parent alert for major/critical incidents

        Args:
            profile_id: Child profile ID
            incident_id: Incident ID
            severity: Incident severity
            incident_type: Type of incident
        """
        try:
            # Get parent_id from profile
            result = self.db.execute_query(
                "SELECT parent_id, name, age FROM child_profiles WHERE profile_id = ?",
                (profile_id,),
            )

            if not result:
                logger.error(
                    f"Could not find profile {sanitize_log_value(profile_id)!r} for parent alert"
                )
                return

            parent_id = result[0]["parent_id"]
            child_name = result[0]["name"]
            child_age = result[0]["age"]

            # Create in-app alert
            alert_message = self._format_alert_message(
                child_name, child_age, severity, incident_type, incident_id
            )

            # Store alert in database
            self.db.execute_write(
                """
                INSERT INTO parent_alerts (
                    parent_id, alert_type, severity, message,
                    related_incident_id, timestamp, acknowledged
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_id,
                    "safety_incident",
                    severity,
                    alert_message,
                    incident_id,
                    datetime.now(timezone.utc).isoformat(),
                    False,
                ),
            )

            # Send email alert if configured
            email_system = get_email_system()
            if email_system:
                # Get encrypted parent email from accounts table
                parent_result = self.db.execute_query(
                    "SELECT encrypted_email FROM accounts WHERE parent_id = ?",
                    (parent_id,),
                )

                if parent_result and parent_result[0]["encrypted_email"]:
                    # Decrypt email for notification
                    email_crypto = get_email_crypto()
                    parent_email = email_crypto.decrypt_email(
                        parent_result[0]["encrypted_email"]
                    )

                    email_system.send_safety_alert(
                        parent_email=parent_email,
                        child_name=child_name,
                        incident_type=incident_type,
                        severity=severity,
                        incident_id=incident_id,
                        timestamp=datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    )
                    logger.info(
                        "Email alert queued for parent (encrypted storage)"
                    )  # Don't log actual email

            # Mark incident as parent-notified
            self.mark_parent_notified(incident_id)

            logger.info(
                f"Parent alert sent for incident {incident_id}, parent: {parent_id}"
            )

        except DB_ERRORS as e:
            logger.error(f"Failed to send parent alert: {e}")

    def _format_alert_message(
        self,
        child_name: str,
        child_age: int,
        severity: str,
        incident_type: str,
        incident_id: int,
    ) -> str:
        """Format parent alert message"""

        severity_text = {
            "minor": "Minor Safety Alert",
            "major": "Important Safety Alert",
            "critical": "URGENT Safety Alert",
        }.get(severity, "Safety Alert")

        messages = {
            "violence": f"{severity_text}: {child_name} asked about violent content.",
            "self_harm": f"{severity_text}: {child_name} mentioned content related to self-harm. Please check in with them.",
            "sexual": f"{severity_text}: {child_name} asked about inappropriate content.",
            "drugs": f"{severity_text}: {child_name} asked about drug or alcohol related content.",
            "personal_info": f"{severity_text}: {child_name} was asked to share personal information.",
            "bullying": f"{severity_text}: Potential bullying-related content detected in {child_name}'s conversation.",
            "dangerous_activity": f"{severity_text}: {child_name} asked about a potentially dangerous activity.",
        }

        base_message = messages.get(
            incident_type,
            f"{severity_text}: Safety concern detected in {child_name}'s conversation.",
        )

        return f"{base_message} (Incident #{incident_id})"

    def _broadcast_incident_websocket(
        self,
        profile_id: str,
        incident_id: int,
        severity: str,
        incident_type: str,
        content_snippet: str,
    ):
        """
        Broadcast incident to parent via WebSocket for real-time monitoring

        Args:
            profile_id: Child profile ID
            incident_id: Incident ID
            severity: Incident severity
            incident_type: Type of incident
            content_snippet: Sample of concerning content
        """
        try:
            # Get parent_id from profile
            result = self.db.execute_query(
                "SELECT parent_id, name FROM child_profiles WHERE profile_id = ?",
                (profile_id,),
            )

            if not result:
                logger.warning(
                    f"Could not find profile {sanitize_log_value(profile_id)!r} for WebSocket broadcast"
                )
                return

            parent_id = result[0]["parent_id"]
            child_name = result[0]["name"]

            # Get WebSocket manager
            ws_manager = get_websocket_manager()
            if not ws_manager:
                logger.debug(
                    "WebSocket manager not available, skipping real-time broadcast"
                )
                return

            # Prepare WebSocket message
            ws_message = {
                "type": "safety_incident",
                "data": {
                    "incident_id": incident_id,
                    "profile_id": profile_id,
                    "child_name": child_name,
                    "severity": severity,
                    "incident_type": incident_type,
                    "content_preview": content_snippet[:100] if content_snippet else "",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "requires_attention": severity in ["major", "critical"],
                },
            }

            # Create async task to broadcast
            # Use asyncio.create_task to avoid blocking the synchronous log_incident method
            try:
                # Probe for a running loop (raises RuntimeError if none); the
                # return value is unused — the call is the guard.
                try:
                    asyncio.get_running_loop()
                    asyncio.create_task(
                        ws_manager.broadcast_to_parent(parent_id, ws_message)
                    )
                    logger.info(
                        f"WebSocket broadcast sent for incident {incident_id} to parent {parent_id}"
                    )
                except RuntimeError:
                    # No running loop - create new one and run (testing/sync scenario)
                    asyncio.run(ws_manager.broadcast_to_parent(parent_id, ws_message))
                    logger.info(
                        f"WebSocket broadcast sent for incident {incident_id} to parent {parent_id}"
                    )

            except (ConnectionError, OSError, RuntimeError) as e:
                # Any other error - log but don't fail incident logging
                logger.debug(f"Could not broadcast via WebSocket: {e}")

        except DB_ERRORS as e:
            logger.error(f"Failed to broadcast incident via WebSocket: {e}")
            # Non-critical error, don't fail the incident logging
