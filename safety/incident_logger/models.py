"""Incident data model + lazy-loaded collaborators (email, websocket).

Extracted verbatim from the former monolithic safety/incident_logger.py.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Lazy import to avoid circular dependency
_email_system = None
_websocket_manager = None


def get_email_system():
    """Lazy load email system"""
    global _email_system
    if _email_system is None:
        try:
            from utils.email_alerts import email_alert_system

            _email_system = email_alert_system
        except ImportError:
            logger.warning("Email alerts not available")
            _email_system = None
    return _email_system


def get_websocket_manager():
    """Lazy load WebSocket manager"""
    global _websocket_manager
    if _websocket_manager is None:
        try:
            from api.websocket_server import websocket_manager

            _websocket_manager = websocket_manager
        except ImportError:
            logger.warning("WebSocket manager not available")
            _websocket_manager = None
    return _websocket_manager


@dataclass
class SafetyIncident:
    """Detailed safety incident record"""

    incident_id: int
    profile_id: str
    session_id: Optional[str]
    incident_type: str
    severity: str
    content_snippet: str
    timestamp: datetime
    parent_notified: bool
    parent_notified_at: Optional[datetime]
    resolved: bool
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]
    metadata: Dict

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "incident_id": self.incident_id,
            "profile_id": self.profile_id,
            "session_id": self.session_id,
            "incident_type": self.incident_type,
            "severity": self.severity,
            "content_snippet": self.content_snippet,
            "timestamp": self.timestamp.isoformat(),
            "parent_notified": self.parent_notified,
            "parent_notified_at": (
                self.parent_notified_at.isoformat() if self.parent_notified_at else None
            ),
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_notes": self.resolution_notes,
            "metadata": self.metadata,
        }
