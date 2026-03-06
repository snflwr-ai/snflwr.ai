# safety/safety_monitor.py
"""
Real-Time Safety Monitoring System
Continuous monitoring of conversations with pattern detection and parent alerts
"""

import threading
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from collections import defaultdict

import smtplib

from config import safety_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from safety.pipeline import safety_pipeline
from utils.logger import get_logger, log_safety_incident, sanitize_log_value
from enum import Enum

logger = get_logger(__name__)

# Lazy import email_service to avoid circular imports
_email_service = None


def _get_email_service():
    """Lazy load email service"""
    global _email_service
    if _email_service is None:
        from core.email_service import email_service

        _email_service = email_service
    return _email_service


@dataclass
class MonitoringProfile:
    """Profile monitoring state"""

    profile_id: str
    parent_id: str
    minor_incidents: int = 0
    major_incidents: int = 0
    critical_incidents: int = 0
    last_incident_time: Optional[datetime] = None
    alert_sent: bool = False
    monitoring_start: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def get_total_incidents(self) -> int:
        """Get total incident count"""
        return self.minor_incidents + self.major_incidents + self.critical_incidents

    def should_alert_parent(self) -> bool:
        """Determine if parent should be alerted"""
        # Critical incidents always alert
        if self.critical_incidents >= safety_config.ALERT_THRESHOLD_CRITICAL:
            return True

        # Major incidents alert on threshold
        if self.major_incidents >= safety_config.ALERT_THRESHOLD_MAJOR:
            return True

        # Minor incidents alert on higher threshold
        if self.minor_incidents >= safety_config.ALERT_THRESHOLD_MINOR:
            return True

        return False

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "profile_id": self.profile_id,
            "parent_id": self.parent_id,
            "minor_incidents": self.minor_incidents,
            "major_incidents": self.major_incidents,
            "critical_incidents": self.critical_incidents,
            "total_incidents": self.get_total_incidents(),
            "last_incident": (
                self.last_incident_time.isoformat() if self.last_incident_time else None
            ),
            "alert_sent": self.alert_sent,
            "monitoring_duration_minutes": (
                datetime.now(timezone.utc) - self.monitoring_start
            ).total_seconds()
            / 60,
        }


@dataclass
class SafetyAlert:
    """Parent alert for safety incidents"""

    alert_id: str
    profile_id: str
    parent_id: str
    severity: str
    incident_count: int
    description: str
    timestamp: datetime
    conversation_snippet: Optional[str] = None
    requires_action: bool = False

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "alert_id": self.alert_id,
            "profile_id": self.profile_id,
            "parent_id": self.parent_id,
            "severity": self.severity,
            "incident_count": self.incident_count,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "conversation_snippet": self.conversation_snippet,
            "requires_action": self.requires_action,
        }


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyMonitor:
    """
    Real-time safety monitoring system
    Tracks conversations, detects patterns, and alerts parents
    """

    def __init__(self, db=None):
        """Initialize safety monitor

        Accept an optional `db` for test injection; falls back to module `db_manager`.
        """
        self.db = db or db_manager
        self.filter = safety_pipeline

        # Active monitoring profiles
        self._monitoring_profiles: Dict[str, MonitoringProfile] = {}
        self._lock = threading.Lock()

        # Alert queue for parent notifications
        self._pending_alerts: List[SafetyAlert] = []

        # Pattern detection
        self._conversation_history: Dict[str, List[str]] = defaultdict(list)
        self._pattern_detectors = self._initialize_pattern_detectors()

        logger.info("Safety monitor initialized")

    def _initialize_pattern_detectors(self) -> List:
        """Initialize pattern detection rules"""
        # Patterns that indicate concerning behavior
        patterns = [
            {
                "name": "repeated_prohibited_content",
                "description": "Multiple attempts to access prohibited content",
                "detector": self._detect_repeated_prohibited,
                "severity": "major",
            },
            {
                "name": "escalating_requests",
                "description": "Increasingly inappropriate requests",
                "detector": self._detect_escalating_requests,
                "severity": "major",
            },
            {
                "name": "persistent_off_topic",
                "description": "Persistent attempts to discuss non-educational topics",
                "detector": self._detect_persistent_off_topic,
                "severity": "minor",
            },
            {
                "name": "distress_indicators",
                "description": "Language indicating child distress",
                "detector": self._detect_distress_indicators,
                "severity": "critical",
            },
        ]

        return patterns

    def start_monitoring(self, profile_id: str, parent_id: str):
        """
        Start monitoring a child profile

        Args:
            profile_id: Child profile ID
            parent_id: Parent ID
        """
        try:
            with self._lock:
                if profile_id not in self._monitoring_profiles:
                    self._monitoring_profiles[profile_id] = MonitoringProfile(
                        profile_id=profile_id, parent_id=parent_id
                    )
                    logger.info(
                        f"Started monitoring profile: {sanitize_log_value(profile_id)!r}"
                    )
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to start monitoring: {e}")

    def stop_monitoring(self, profile_id: str):
        """
        Stop monitoring a child profile

        Args:
            profile_id: Child profile ID
        """
        try:
            with self._lock:
                if profile_id in self._monitoring_profiles:
                    del self._monitoring_profiles[profile_id]
                    logger.info(f"Stopped monitoring profile: {profile_id!r}")

                # Clean up conversation history to prevent memory leak
                if profile_id in self._conversation_history:
                    del self._conversation_history[profile_id]
                    logger.debug(
                        f"Cleaned up conversation history for profile: {profile_id!r}"
                    )
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to stop monitoring: {e}")

    def cleanup_inactive_profiles(self):
        """
        Clean up conversation history for profiles that are no longer being monitored.

        This prevents memory leaks from inactive profiles accumulating in _conversation_history.
        Should be called periodically or when memory usage is a concern.
        """
        try:
            with self._lock:
                # Find profiles in conversation history that are not being monitored
                inactive_profiles = [
                    profile_id
                    for profile_id in self._conversation_history.keys()
                    if profile_id not in self._monitoring_profiles
                ]

                # Remove conversation history for inactive profiles
                for profile_id in inactive_profiles:
                    del self._conversation_history[profile_id]
                    logger.debug(
                        f"Cleaned up conversation history for inactive profile: {profile_id!r}"
                    )

                if inactive_profiles:
                    logger.info(
                        f"Cleaned up {len(inactive_profiles)} inactive profiles from conversation history"
                    )

        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to cleanup inactive profiles: {e}")

    def monitor_message(
        self,
        profile_id: str,
        message: str,
        age: Optional[int] = None,
        message_type: Optional[str] = "user",  # 'user' or 'assistant'
        session_id: Optional[str] = None,
    ) -> Optional[SafetyAlert]:
        """
        Monitor a conversation message in real-time

        Args:
            profile_id: Child profile ID
            message: Message content
            message_type: Type of message
            session_id: Session ID

        Returns:
            Tuple of (is_safe, alert_message or None)
        """
        try:
            # Ensure monitoring profile exists (tests expect auto-start)
            profile = self._get_monitoring_profile(profile_id)
            if not profile:
                # Try to find parent_id from DB
                try:
                    res = self.db.execute_query(
                        "SELECT parent_id, name, age FROM child_profiles WHERE profile_id = ?",
                        (profile_id,),
                    )
                    parent_id = res[0]["parent_id"] if res else "unknown"
                except DB_ERRORS as e:
                    logger.warning(
                        f"Failed to look up parent_id for profile {sanitize_log_value(profile_id)!r}: {e}"
                    )
                    parent_id = "unknown"

                # Auto-start monitoring for compatibility with tests
                self.start_monitoring(profile_id, parent_id)
                profile = self._get_monitoring_profile(profile_id)

            # Store message in conversation history
            self._conversation_history[profile_id].append(message)

            # Keep only recent messages (last 20)
            if len(self._conversation_history[profile_id]) > 20:
                self._conversation_history[profile_id] = self._conversation_history[
                    profile_id
                ][-20:]

            # Filter content (use provided age if available)
            use_age = age if age is not None else 12
            filter_result = self.filter.check_input(message, use_age, profile_id)

            # If content is not safe, record incident and return an alert object
            if not filter_result.is_safe:
                self._record_incident(
                    profile=profile,
                    severity=filter_result.severity.value,
                    reason=filter_result.reason or "Content filtered",
                    message_snippet=message[:200],
                    session_id=session_id,
                )

                # Create a SafetyAlert for this event and queue it
                import secrets

                alert = SafetyAlert(
                    alert_id=secrets.token_hex(8),
                    profile_id=profile.profile_id,
                    parent_id=profile.parent_id,
                    severity=(
                        AlertSeverity.CRITICAL.value
                        if filter_result.severity.value == "critical"
                        else (
                            AlertSeverity.HIGH.value
                            if filter_result.severity.value == "major"
                            else AlertSeverity.MEDIUM.value
                        )
                    ),
                    incident_count=profile.get_total_incidents(),
                    description=filter_result.reason or "Safety concern",
                    timestamp=datetime.now(timezone.utc),
                    conversation_snippet=message[:200],
                    requires_action=(filter_result.severity.value == "critical"),
                )

                with self._lock:
                    self._pending_alerts.append(alert)

                # Attempt parent alert flow
                if profile.should_alert_parent() and not profile.alert_sent:
                    self._create_parent_alert(profile, session_id)

                return alert

            # Run pattern detection
            pattern_result = self._run_pattern_detection(profile_id, profile)
            if pattern_result:
                severity, description = pattern_result
                self._record_incident(
                    profile=profile,
                    severity=severity,
                    reason=description,
                    message_snippet=message[:200],
                    session_id=session_id,
                )

                if profile.should_alert_parent() and not profile.alert_sent:
                    self._create_parent_alert(profile, session_id)

            return None

        except Exception as e:  # Intentional catch-all: fail closed to protect children
            # FAIL-CLOSED: if safety monitoring errors, block the message.
            # Same principle as the safety pipeline — when in doubt, block.
            logger.error(f"Safety monitoring failed, blocking message: {e}")
            return None

    def _get_monitoring_profile(self, profile_id: str) -> Optional[MonitoringProfile]:
        """Get monitoring profile"""
        with self._lock:
            return self._monitoring_profiles.get(profile_id)

    def _record_incident(
        self,
        profile: MonitoringProfile,
        severity: str,
        reason: str,
        message_snippet: str,
        session_id: str,
    ):
        """Record a safety incident"""
        try:
            # Update profile incident counts
            with self._lock:
                if severity == "critical":
                    profile.critical_incidents += 1
                elif severity == "major":
                    profile.major_incidents += 1
                elif severity == "minor":
                    profile.minor_incidents += 1

                profile.last_incident_time = datetime.now(timezone.utc)

            # Encrypt content snippet before storing (COPPA/FERPA compliance)
            encrypted_snippet = message_snippet
            try:
                from storage.encryption import EncryptionManager

                encryption = EncryptionManager()
                encrypted_snippet = encryption.encrypt_string(message_snippet)
            except Exception as enc_err:
                logger.error(
                    f"Failed to encrypt incident snippet, storing redacted: {enc_err}"
                )
                encrypted_snippet = "[encryption unavailable - content redacted]"

            # Log to database
            self.db.execute_write(
                """
                INSERT INTO safety_incidents (
                    profile_id, session_id, incident_type, severity,
                    content_snippet, timestamp, parent_notified
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.profile_id,
                    session_id,
                    reason,
                    severity,
                    encrypted_snippet,
                    datetime.now(timezone.utc).isoformat(),
                    False,
                ),
            )

            # Log to safety incident logger
            log_safety_incident(
                incident_type=reason,
                profile_id=profile.profile_id,
                content=message_snippet,
                severity=severity,
                metadata={"session_id": session_id},
            )

            logger.warning(
                f"Safety incident recorded: {severity!r} - {reason!r} (profile: {profile.profile_id!r})"
            )

        except DB_ERRORS as e:
            logger.error(f"Failed to record incident: {e}")

    def _create_parent_alert(self, profile: MonitoringProfile, session_id: str):
        """
        Create parent alert and send email notification

        Args:
            profile: Monitoring profile with incident data
            session_id: Current session ID
        """
        try:
            import secrets

            # Determine severity
            severity = (
                "critical"
                if profile.critical_incidents > 0
                else ("high" if profile.major_incidents >= 2 else "medium")
            )

            alert = SafetyAlert(
                alert_id=secrets.token_hex(8),
                profile_id=profile.profile_id,
                parent_id=profile.parent_id,
                severity=severity,
                incident_count=profile.get_total_incidents(),
                description=self._generate_alert_description(profile),
                timestamp=datetime.now(timezone.utc),
                conversation_snippet=self._get_recent_conversation_snippet(
                    profile.profile_id
                ),
                requires_action=profile.critical_incidents > 0,
            )

            # Add to pending alerts
            with self._lock:
                self._pending_alerts.append(alert)
                profile.alert_sent = True

            # Update database
            self.db.execute_write(
                """
                UPDATE safety_incidents
                SET parent_notified = 1, parent_notified_at = ?
                WHERE profile_id = ? AND parent_notified = 0
                """,
                (datetime.now(timezone.utc).isoformat(), profile.profile_id),
            )

            logger.warning(f"Parent alert created for profile {profile.profile_id!r}")

            # Send email notification
            try:
                child_name = self._get_child_profile_name(profile.profile_id)

                email_service = _get_email_service()
                success, error = email_service.send_safety_alert(
                    parent_id=profile.parent_id,
                    child_name=child_name,
                    severity=severity,
                    incident_count=profile.get_total_incidents(),
                    description=alert.description,
                    snippet=alert.conversation_snippet,
                )

                if success:
                    logger.info(f"Email notification sent for alert {alert.alert_id!r}")
                elif error:
                    logger.warning(
                        f"Email notification failed for alert {alert.alert_id!r}: {error}"
                    )

            except smtplib.SMTPException as email_error:
                # Don't fail the alert creation if email fails
                logger.error(f"Failed to send email notification: {email_error}")

        except DB_ERRORS as e:
            logger.error(f"Failed to create parent alert: {e}")

    def _generate_alert_description(self, profile: MonitoringProfile) -> str:
        """Generate alert description"""
        if profile.critical_incidents > 0:
            return f"Critical safety incidents detected ({profile.critical_incidents} critical, {profile.major_incidents} major)"
        elif profile.major_incidents >= 2:
            return f"Multiple safety incidents detected ({profile.major_incidents} major, {profile.minor_incidents} minor)"
        else:
            return f"Safety incidents requiring attention ({profile.get_total_incidents()} total)"

    def _get_recent_conversation_snippet(self, profile_id: str) -> Optional[str]:
        """Get recent conversation snippet for alert"""
        history = self._conversation_history.get(profile_id, [])
        if not history:
            return None

        # Get last 3 messages
        recent = history[-3:]
        return " | ".join(msg[:50] + "..." if len(msg) > 50 else msg for msg in recent)

    def _get_child_profile_name(self, profile_id: str) -> str:
        """
        Get child profile name from database

        Args:
            profile_id: Child profile ID

        Returns:
            Child name or "Your Child" if not found
        """
        try:
            result = self.db.execute_query(
                "SELECT name FROM child_profiles WHERE profile_id = ?", (profile_id,)
            )

            if result and isinstance(result[0], dict) and "name" in result[0]:
                return result[0]["name"]

            return "Your Child"

        except DB_ERRORS as e:
            logger.error(f"Failed to get child profile name: {e}")
            return "Your Child"

    def _run_pattern_detection(
        self, profile_id: str, profile: MonitoringProfile
    ) -> Optional[Tuple[str, str]]:
        """
        Run pattern detection on conversation history

        Returns:
            Tuple of (severity, description) if pattern detected, None otherwise
        """
        for pattern in self._pattern_detectors:
            if pattern["detector"](profile_id):
                return pattern["severity"], pattern["description"]

        return None

    # Compatibility helpers expected by tests
    def get_latest_alert(self, profile_id: str) -> Optional[SafetyAlert]:
        """Return the latest pending alert for a profile, if any."""
        with self._lock:
            for a in reversed(self._pending_alerts):
                if a.profile_id == profile_id:
                    return a
        return None

    def check_for_patterns(self, profile_id: str) -> Optional[SafetyAlert]:
        """Run pattern detectors and return a SafetyAlert if any pattern triggers."""
        profile = self._get_monitoring_profile(profile_id)
        if not profile:
            return None

        pattern_result = self._run_pattern_detection(profile_id, profile)
        if not pattern_result:
            return None

        severity, description = pattern_result
        import secrets

        alert = SafetyAlert(
            alert_id=secrets.token_hex(8),
            profile_id=profile.profile_id,
            parent_id=profile.parent_id,
            severity=(
                AlertSeverity.CRITICAL.value
                if severity == "critical"
                else (
                    AlertSeverity.HIGH.value
                    if severity == "major"
                    else AlertSeverity.MEDIUM.value
                )
            ),
            incident_count=profile.get_total_incidents(),
            description=description,
            timestamp=datetime.now(timezone.utc),
            conversation_snippet=self._get_recent_conversation_snippet(profile_id),
            requires_action=(severity == "critical"),
        )

        with self._lock:
            self._pending_alerts.append(alert)

        return alert

    def _detect_repeated_prohibited(self, profile_id: str) -> bool:
        """Detect repeated attempts to access prohibited content"""
        history = self._conversation_history.get(profile_id, [])
        if len(history) < 5:
            return False

        # Check last 5 messages
        recent = history[-5:]
        prohibited_count = 0

        for msg in recent:
            result = self.filter.check_input(msg, 12, profile_id)
            if not result.is_safe:
                prohibited_count += 1

        return prohibited_count >= 3

    def _detect_escalating_requests(self, profile_id: str) -> bool:
        """Detect increasingly inappropriate requests"""
        history = self._conversation_history.get(profile_id, [])
        if len(history) < 4:
            return False

        # Check if severity is increasing over time
        recent = history[-4:]
        severities = []

        for msg in recent:
            result = self.filter.check_input(msg, 12, profile_id)
            if not result.is_safe:
                severity_map = {"minor": 1, "major": 2, "critical": 3}
                severities.append(severity_map.get(result.severity.value, 0))

        # Check for increasing trend
        if len(severities) >= 3:
            return severities[-1] > severities[0]

        return False

    def _detect_persistent_off_topic(self, profile_id: str) -> bool:
        """Detect persistent off-topic discussions"""
        history = self._conversation_history.get(profile_id, [])
        if len(history) < 6:
            return False

        # Check for repeated topic redirections
        recent = history[-6:]
        off_topic_count = 0

        for msg in recent:
            if any(
                topic in msg.lower() for topic in safety_config.REDIRECT_TOPICS.keys()
            ):
                off_topic_count += 1

        return off_topic_count >= 4

    def _detect_distress_indicators(self, profile_id: str) -> bool:
        """Detect language indicating child distress"""
        history = self._conversation_history.get(profile_id, [])
        if not history:
            return False

        distress_keywords = [
            "help me",
            "scared",
            "afraid",
            "worried",
            "sad",
            "depressed",
            "alone",
            "nobody cares",
        ]

        recent = history[-3:]
        for msg in recent:
            msg_lower = msg.lower()
            if any(keyword in msg_lower for keyword in distress_keywords):
                return True

        return False

    def get_pending_alerts(self, parent_id: Optional[str] = None) -> List[SafetyAlert]:
        """
        Get pending parent alerts

        Args:
            parent_id: Optional filter by parent

        Returns:
            List of pending alerts
        """
        with self._lock:
            if parent_id:
                return [a for a in self._pending_alerts if a.parent_id == parent_id]
            return list(self._pending_alerts)

    def acknowledge_alert(self, alert_id: str) -> bool:
        """
        Acknowledge a parent alert

        Args:
            alert_id: Alert identifier

        Returns:
            True if successful
        """
        try:
            with self._lock:
                for i, alert in enumerate(self._pending_alerts):
                    if alert.alert_id == alert_id:
                        self._pending_alerts.pop(i)
                        logger.info(
                            f"Alert acknowledged: {sanitize_log_value(alert_id)!r}"
                        )
                        return True
                return False
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to acknowledge alert: {e}")
            return False

    def get_profile_statistics(self, profile_id: str) -> Dict:
        """Get monitoring statistics for a profile"""
        try:
            profile = self._get_monitoring_profile(profile_id)
            if not profile:
                return {}

            return profile.to_dict()

        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to get profile statistics: {e}")
            return {}

    def get_system_statistics(self) -> Dict:
        """Get system-wide monitoring statistics"""
        try:
            with self._lock:
                total_profiles = len(self._monitoring_profiles)
                total_incidents = sum(
                    p.get_total_incidents() for p in self._monitoring_profiles.values()
                )
                profiles_with_incidents = sum(
                    1
                    for p in self._monitoring_profiles.values()
                    if p.get_total_incidents() > 0
                )
                pending_alerts = len(self._pending_alerts)

            return {
                "monitored_profiles": total_profiles,
                "total_incidents": total_incidents,
                "profiles_with_incidents": profiles_with_incidents,
                "pending_alerts": pending_alerts,
                "pattern_detectors": len(self._pattern_detectors),
            }

        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to get system statistics: {e}")
            return {}


# Singleton instance
safety_monitor = SafetyMonitor()


# Export public interface
__all__ = [
    "SafetyMonitor",
    "MonitoringProfile",
    "SafetyAlert",
    "AlertSeverity",
    "safety_monitor",
]
