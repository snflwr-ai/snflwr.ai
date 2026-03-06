# safety/incident_logger.py
"""
Safety Incident Logging and Reporting System
Comprehensive tracking, analysis, and reporting of safety incidents with real-time WebSocket notifications
"""

from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json
import asyncio

from config import safety_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager
from core.email_crypto import get_email_crypto
from utils.logger import get_logger, sanitize_log_value

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    InvalidToken = Exception

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
            'incident_id': self.incident_id,
            'profile_id': self.profile_id,
            'session_id': self.session_id,
            'incident_type': self.incident_type,
            'severity': self.severity,
            'content_snippet': self.content_snippet,
            'timestamp': self.timestamp.isoformat(),
            'parent_notified': self.parent_notified,
            'parent_notified_at': self.parent_notified_at.isoformat() if self.parent_notified_at else None,
            'resolved': self.resolved,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'resolution_notes': self.resolution_notes,
            'metadata': self.metadata
        }


class IncidentLogger:
    """
    Comprehensive safety incident logging and reporting system
    Provides detailed tracking, analysis, and parent reporting capabilities
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
        send_alert: bool = True
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
            if severity not in ['minor', 'major', 'critical']:
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
                    encrypted_metadata
                )
            )

            # Get incident ID
            result = self.db.execute_query(
                "SELECT incident_id FROM safety_incidents WHERE profile_id = ? ORDER BY incident_id DESC LIMIT 1",
                (profile_id,)
            )

            if result:
                incident_id = result[0]['incident_id']
                logger.info(f"Incident logged: ID {sanitize_log_value(incident_id)!r}, severity: {severity!r}, profile: {sanitize_log_value(profile_id)!r}")

                # Broadcast incident via WebSocket for real-time monitoring
                self._broadcast_incident_websocket(profile_id, incident_id, severity, incident_type, content_snippet)

                # Send parent alert for major/critical incidents
                if send_alert and severity in ['major', 'critical']:
                    self._send_parent_alert(profile_id, incident_id, severity, incident_type)

                return True, incident_id

            return False, None

        except DB_ERRORS as e:
            logger.error(f"Failed to log incident: {e}")
            return False, None

    def get_incident(self, incident_id: int) -> Optional[SafetyIncident]:
        """
        Get detailed incident information
        
        Args:
            incident_id: Incident identifier
            
        Returns:
            SafetyIncident object or None
        """
        try:
            results = self.db.execute_query(
                """
                SELECT incident_id, profile_id, session_id, incident_type,
                       severity, content_snippet, timestamp, parent_notified,
                       parent_notified_at, resolved, resolved_at, resolution_notes,
                       metadata
                FROM safety_incidents
                WHERE incident_id = ?
                """,
                (incident_id,)
            )
            
            if not results:
                return None
            
            row = results[0]
            
            # Decrypt content
            content_snippet = self.encryption.decrypt_string(row['content_snippet'])
            
            # Decrypt metadata if present
            metadata = {}
            if row['metadata']:
                try:
                    metadata = self.encryption.decrypt_dict(row['metadata'])
                except (InvalidToken, ValueError, TypeError) as e:
                    logger.debug(f"Failed to decrypt incident metadata: {e}")
                    metadata = {}

            # Decrypt resolution notes if present
            resolution_notes = None
            if row['resolution_notes']:
                try:
                    resolution_notes = self.encryption.decrypt_string(row['resolution_notes'])
                except (InvalidToken, ValueError, TypeError) as e:
                    logger.debug(f"Failed to decrypt resolution notes: {e}")
                    resolution_notes = None

            incident = SafetyIncident(
                incident_id=row['incident_id'],
                profile_id=row['profile_id'],
                session_id=row['session_id'],
                incident_type=row['incident_type'],
                severity=row['severity'],
                content_snippet=content_snippet,
                timestamp=datetime.fromisoformat(row['timestamp']),
                parent_notified=bool(row['parent_notified']),
                parent_notified_at=datetime.fromisoformat(row['parent_notified_at']) if row['parent_notified_at'] else None,
                resolved=bool(row['resolved']),
                resolved_at=datetime.fromisoformat(row['resolved_at']) if row['resolved_at'] else None,
                resolution_notes=resolution_notes,
                metadata=metadata
            )

            return incident

        except DB_ERRORS as e:
            logger.error(f"Failed to get incident: {e}")
            return None
    
    def get_profile_incidents(
        self,
        profile_id: str,
        days: int = 30,
        severity: Optional[str] = None,
        unresolved_only: bool = False
    ) -> List[SafetyIncident]:
        """
        Get incidents for a specific profile
        
        Args:
            profile_id: Child profile ID
            days: Number of days to look back
            severity: Optional filter by severity
            unresolved_only: Only return unresolved incidents
            
        Returns:
            List of SafetyIncident objects
        """
        try:
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            
            # Build query
            query = """
                SELECT incident_id, profile_id, session_id, incident_type,
                       severity, content_snippet, timestamp, parent_notified,
                       parent_notified_at, resolved, resolved_at, resolution_notes,
                       metadata
                FROM safety_incidents
                WHERE profile_id = ? AND timestamp >= ?
            """
            params = [profile_id, cutoff_date]
            
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            
            if unresolved_only:
                query += " AND resolved = 0"
            
            query += " ORDER BY timestamp DESC"
            
            results = self.db.execute_query(query, tuple(params))
            
            incidents = []
            for row in results:
                # Decrypt content
                content_snippet = self.encryption.decrypt_string(row['content_snippet'])

                # Decrypt metadata
                metadata = {}
                if row['metadata']:
                    try:
                        metadata = self.encryption.decrypt_dict(row['metadata'])
                    except (InvalidToken, ValueError, TypeError) as e:
                        logger.debug(f"Failed to decrypt incident metadata: {e}")
                        metadata = {}

                # Decrypt resolution notes if present
                resolution_notes = None
                if row['resolution_notes']:
                    try:
                        resolution_notes = self.encryption.decrypt_string(row['resolution_notes'])
                    except (InvalidToken, ValueError, TypeError) as e:
                        logger.debug(f"Failed to decrypt resolution notes: {e}")
                        resolution_notes = None

                incident = SafetyIncident(
                    incident_id=row['incident_id'],
                    profile_id=row['profile_id'],
                    session_id=row['session_id'],
                    incident_type=row['incident_type'],
                    severity=row['severity'],
                    content_snippet=content_snippet,
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    parent_notified=bool(row['parent_notified']),
                    parent_notified_at=datetime.fromisoformat(row['parent_notified_at']) if row['parent_notified_at'] else None,
                    resolved=bool(row['resolved']),
                    resolved_at=datetime.fromisoformat(row['resolved_at']) if row['resolved_at'] else None,
                    resolution_notes=resolution_notes,
                    metadata=metadata
                )
                incidents.append(incident)

            return incidents

        except DB_ERRORS as e:
            logger.error(f"Failed to get profile incidents: {e}")
            return []

    def get_unresolved_incidents(self, profile_id: str) -> List[SafetyIncident]:
        """Return unresolved incidents for a profile."""
        return self.get_profile_incidents(profile_id, unresolved_only=True)

    def get_incidents_by_severity(self, profile_id: str, min_severity: str = None, severity: str = None) -> List[SafetyIncident]:
        """
        Get incidents filtered by severity level

        Args:
            profile_id: Profile identifier
            min_severity: Minimum severity level (returns incidents >= this level)
            severity: Exact severity level match (deprecated, use min_severity)

        Returns:
            List of SafetyIncident objects
        """
        try:
            # Define severity levels (higher number = more severe)
            severity_levels = {'minor': 1, 'major': 2, 'critical': 3}

            if min_severity:
                # Filter for incidents >= min_severity
                min_level = severity_levels.get(min_severity, 0)
                valid_severities = [s for s, l in severity_levels.items() if l >= min_level]

                placeholders = ','.join('?' * len(valid_severities))
                rows = self.db.execute_query(
                    f"""
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ? AND severity IN ({placeholders})
                    ORDER BY timestamp DESC
                    """,
                    (profile_id, *valid_severities)
                )
            elif severity:
                # Exact match (backward compatibility)
                rows = self.db.execute_query(
                    """
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ? AND severity = ?
                    ORDER BY timestamp DESC
                    """,
                    (profile_id, severity)
                )
            else:
                # No filter, return all
                rows = self.db.execute_query(
                    """
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ?
                    ORDER BY timestamp DESC
                    """,
                    (profile_id,)
                )

            incidents = []
            for row in rows:
                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, IndexError, TypeError):
                        return row[idx] if idx < len(row) else None

                timestamp_str = g('timestamp', 6)
                notified_str = g('parent_notified_at', 8)
                resolved_str = g('resolved_at', 10)

                try:
                    timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now(timezone.utc)
                except ValueError:
                    logger.debug(f"Failed to parse timestamp: {timestamp_str}")
                    timestamp = datetime.now(timezone.utc)

                try:
                    parent_notified_at = datetime.fromisoformat(notified_str) if notified_str else None
                except ValueError:
                    logger.debug(f"Failed to parse parent_notified_at: {notified_str}")
                    parent_notified_at = None

                try:
                    resolved_at = datetime.fromisoformat(resolved_str) if resolved_str else None
                except ValueError:
                    logger.debug(f"Failed to parse resolved_at: {resolved_str}")
                    resolved_at = None

                # Decrypt content snippet
                raw_snippet = g('content_snippet', 5) or ''
                try:
                    content_snippet = self.encryption.decrypt_string(raw_snippet)
                except Exception:
                    content_snippet = raw_snippet

                # Parse metadata (may be plain JSON or encrypted)
                metadata_str = g('metadata', 12)
                metadata = {}
                if metadata_str:
                    try:
                        metadata = json.loads(metadata_str)
                    except (json.JSONDecodeError, TypeError):
                        # Not valid JSON — try decryption
                        try:
                            metadata = self.encryption.decrypt_dict(metadata_str)
                        except Exception:
                            metadata = {}

                # Decrypt resolution notes
                raw_notes = g('resolution_notes', 11)
                resolution_notes = None
                if raw_notes:
                    try:
                        resolution_notes = self.encryption.decrypt_string(raw_notes)
                    except Exception:
                        resolution_notes = raw_notes

                incident = SafetyIncident(
                    incident_id=g('incident_id', 0),
                    profile_id=g('profile_id', 1),
                    session_id=g('session_id', 2),
                    incident_type=g('incident_type', 3),
                    severity=g('severity', 4),
                    content_snippet=content_snippet,
                    timestamp=timestamp,
                    parent_notified=bool(g('parent_notified', 7)),
                    parent_notified_at=parent_notified_at,
                    resolved=bool(g('resolved', 9)),
                    resolved_at=resolved_at,
                    resolution_notes=resolution_notes,
                    metadata=metadata
                )
                incidents.append(incident)

            return incidents

        except DB_ERRORS as e:
            logger.error(f"Failed to get incidents by severity: {e}")
            return []

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
                (datetime.now(timezone.utc).isoformat(), incident_id)
            )
            
            logger.info(f"Incident {incident_id} marked as parent-notified")
            return True
            
        except DB_ERRORS as e:
            logger.error(f"Failed to mark incident as notified: {e}")
            return False
    
    def resolve_incident(
        self,
        incident_id: int,
        resolution_notes: str
    ) -> bool:
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
            encrypted_notes = self.encryption.encrypt_string(resolution_notes) if resolution_notes else None

            self.db.execute_write(
                """
                UPDATE safety_incidents
                SET resolved = 1, resolved_at = ?, resolution_notes = ?
                WHERE incident_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), encrypted_notes, incident_id)
            )

            logger.info(f"Incident {sanitize_log_value(incident_id)!r} resolved")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to resolve incident: {e}")
            return False
    
    def get_incident_statistics(
        self,
        profile_id: Optional[str] = None,
        days: int = 30
    ) -> Dict:
        """
        Get incident statistics
        
        Args:
            profile_id: Optional filter by profile
            days: Number of days to analyze
            
        Returns:
            Dictionary of statistics
        """
        try:
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            
            # Base query
            if profile_id:
                results = self.db.execute_query(
                    """
                    SELECT severity, COUNT(*) as count,
                           SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved,
                           SUM(CASE WHEN parent_notified = 0 THEN 1 ELSE 0 END) as not_notified
                    FROM safety_incidents
                    WHERE profile_id = ? AND timestamp >= ?
                    GROUP BY severity
                    """,
                    (profile_id, cutoff_date)
                )
            else:
                results = self.db.execute_query(
                    """
                    SELECT severity, COUNT(*) as count,
                           SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved,
                           SUM(CASE WHEN parent_notified = 0 THEN 1 ELSE 0 END) as not_notified
                    FROM safety_incidents
                    WHERE timestamp >= ?
                    GROUP BY severity
                    """,
                    (cutoff_date,)
                )
            
            # Process results
            stats = {
                'total_incidents': 0,
                'by_severity': {},
                'unresolved': 0,
                'awaiting_parent_notification': 0,
                'time_period_days': days
            }
            
            for row in results:
                severity = row['severity']
                count = row['count']
                unresolved = row['unresolved']
                not_notified = row['not_notified']
                
                stats['by_severity'][severity] = {
                    'count': count,
                    'unresolved': unresolved,
                    'not_notified': not_notified
                }
                stats['total_incidents'] += count
                stats['unresolved'] += unresolved
                stats['awaiting_parent_notification'] += not_notified
            
            # Get incident types
            if profile_id:
                type_results = self.db.execute_query(
                    """
                    SELECT incident_type, COUNT(*) as count
                    FROM safety_incidents
                    WHERE profile_id = ? AND timestamp >= ?
                    GROUP BY incident_type
                    ORDER BY count DESC
                    LIMIT 5
                    """,
                    (profile_id, cutoff_date)
                )
            else:
                type_results = self.db.execute_query(
                    """
                    SELECT incident_type, COUNT(*) as count
                    FROM safety_incidents
                    WHERE timestamp >= ?
                    GROUP BY incident_type
                    ORDER BY count DESC
                    LIMIT 5
                    """,
                    (cutoff_date,)
                )
            
            stats['top_incident_types'] = [
                {'type': row['incident_type'], 'count': row['count']}
                for row in type_results
            ]
            
            return stats
            
        except DB_ERRORS as e:
            logger.error(f"Failed to get incident statistics: {e}")
            return {}
    
    def generate_parent_report(
        self,
        parent_id: str,
        profile_id: Optional[str] = None,
        days: int = 7
    ) -> Dict:
        """
        Generate comprehensive safety report for parents
        
        Args:
            parent_id: Parent ID
            profile_id: Optional specific profile
            days: Number of days to report on
            
        Returns:
            Comprehensive report dictionary
        """
        try:
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            # Get incidents for parent's profiles
            # Use parameterized queries to prevent SQL injection
            if profile_id:
                results = self.db.execute_query(
                    """
                    SELECT si.profile_id, cp.name as child_name,
                           COUNT(*) as incident_count,
                           SUM(CASE WHEN si.severity = 'critical' THEN 1 ELSE 0 END) as critical,
                           SUM(CASE WHEN si.severity = 'major' THEN 1 ELSE 0 END) as major,
                           SUM(CASE WHEN si.severity = 'minor' THEN 1 ELSE 0 END) as minor,
                           MAX(si.timestamp) as latest_incident
                    FROM safety_incidents si
                    JOIN child_profiles cp ON si.profile_id = cp.profile_id
                    WHERE cp.parent_id = ? AND si.timestamp >= ? AND si.profile_id = ?
                    GROUP BY si.profile_id, cp.name
                    """,
                    (parent_id, cutoff_date, profile_id)
                )
            else:
                results = self.db.execute_query(
                    """
                    SELECT si.profile_id, cp.name as child_name,
                           COUNT(*) as incident_count,
                           SUM(CASE WHEN si.severity = 'critical' THEN 1 ELSE 0 END) as critical,
                           SUM(CASE WHEN si.severity = 'major' THEN 1 ELSE 0 END) as major,
                           SUM(CASE WHEN si.severity = 'minor' THEN 1 ELSE 0 END) as minor,
                           MAX(si.timestamp) as latest_incident
                    FROM safety_incidents si
                    JOIN child_profiles cp ON si.profile_id = cp.profile_id
                    WHERE cp.parent_id = ? AND si.timestamp >= ?
                    GROUP BY si.profile_id, cp.name
                    """,
                    (parent_id, cutoff_date)
                )
            
            report = {
                'parent_id': parent_id,
                'report_period_days': days,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'profiles': []
            }
            
            for row in results:
                profile_report = {
                    'profile_id': row['profile_id'],
                    'child_name': row['child_name'],
                    'total_incidents': row['incident_count'],
                    'by_severity': {
                        'critical': row['critical'],
                        'major': row['major'],
                        'minor': row['minor']
                    },
                    'latest_incident': row['latest_incident']
                }
                
                # Get recent incidents for this profile
                recent_incidents = self.get_profile_incidents(
                    row['profile_id'],
                    days=days,
                    unresolved_only=True
                )
                
                profile_report['unresolved_incidents'] = [
                    {
                        'incident_id': inc.incident_id,
                        'type': inc.incident_type,
                        'severity': inc.severity,
                        'timestamp': inc.timestamp.isoformat(),
                        'content_preview': inc.content_snippet[:100] + '...' if len(inc.content_snippet) > 100 else inc.content_snippet
                    }
                    for inc in recent_incidents[:5]  # Top 5 unresolved
                ]
                
                report['profiles'].append(profile_report)
            
            # Calculate summary
            report['summary'] = {
                'total_profiles_with_incidents': len(report['profiles']),
                'total_incidents': sum(p['total_incidents'] for p in report['profiles']),
                'critical_incidents': sum(p['by_severity']['critical'] for p in report['profiles']),
                'major_incidents': sum(p['by_severity']['major'] for p in report['profiles']),
                'minor_incidents': sum(p['by_severity']['minor'] for p in report['profiles'])
            }
            
            return report
            
        except DB_ERRORS as e:
            logger.error(f"Failed to generate parent report: {e}")
            return {}
    
    def _send_parent_alert(
        self,
        profile_id: str,
        incident_id: int,
        severity: str,
        incident_type: str
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
                (profile_id,)
            )

            if not result:
                logger.error(f"Could not find profile {sanitize_log_value(profile_id)!r} for parent alert")
                return

            parent_id = result[0]['parent_id']
            child_name = result[0]['name']
            child_age = result[0]['age']

            # Create in-app alert
            alert_message = self._format_alert_message(
                child_name,
                child_age,
                severity,
                incident_type,
                incident_id
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
                    'safety_incident',
                    severity,
                    alert_message,
                    incident_id,
                    datetime.now(timezone.utc).isoformat(),
                    False
                )
            )

            # Send email alert if configured
            email_system = get_email_system()
            if email_system:
                # Get encrypted parent email from accounts table
                parent_result = self.db.execute_query(
                    "SELECT encrypted_email FROM accounts WHERE parent_id = ?",
                    (parent_id,)
                )

                if parent_result and parent_result[0]['encrypted_email']:
                    # Decrypt email for notification
                    email_crypto = get_email_crypto()
                    parent_email = email_crypto.decrypt_email(parent_result[0]['encrypted_email'])

                    email_system.send_safety_alert(
                        parent_email=parent_email,
                        child_name=child_name,
                        incident_type=incident_type,
                        severity=severity,
                        incident_id=incident_id,
                        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    )
                    logger.info(f"Email alert queued for parent (encrypted storage)")  # Don't log actual email

            # Mark incident as parent-notified
            self.mark_parent_notified(incident_id)

            logger.info(f"Parent alert sent for incident {incident_id}, parent: {parent_id}")

        except DB_ERRORS as e:
            logger.error(f"Failed to send parent alert: {e}")

    def _format_alert_message(
        self,
        child_name: str,
        child_age: int,
        severity: str,
        incident_type: str,
        incident_id: int
    ) -> str:
        """Format parent alert message"""

        severity_text = {
            'minor': 'Minor Safety Alert',
            'major': 'Important Safety Alert',
            'critical': 'URGENT Safety Alert'
        }.get(severity, 'Safety Alert')

        messages = {
            'violence': f"{severity_text}: {child_name} asked about violent content.",
            'self_harm': f"{severity_text}: {child_name} mentioned content related to self-harm. Please check in with them.",
            'sexual': f"{severity_text}: {child_name} asked about inappropriate content.",
            'drugs': f"{severity_text}: {child_name} asked about drug or alcohol related content.",
            'personal_info': f"{severity_text}: {child_name} was asked to share personal information.",
            'bullying': f"{severity_text}: Potential bullying-related content detected in {child_name}'s conversation.",
            'dangerous_activity': f"{severity_text}: {child_name} asked about a potentially dangerous activity."
        }

        base_message = messages.get(
            incident_type,
            f"{severity_text}: Safety concern detected in {child_name}'s conversation."
        )

        return f"{base_message} (Incident #{incident_id})"

    def _broadcast_incident_websocket(
        self,
        profile_id: str,
        incident_id: int,
        severity: str,
        incident_type: str,
        content_snippet: str
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
                (profile_id,)
            )

            if not result:
                logger.warning(f"Could not find profile {sanitize_log_value(profile_id)!r} for WebSocket broadcast")
                return

            parent_id = result[0]['parent_id']
            child_name = result[0]['name']

            # Get WebSocket manager
            ws_manager = get_websocket_manager()
            if not ws_manager:
                logger.debug("WebSocket manager not available, skipping real-time broadcast")
                return

            # Prepare WebSocket message
            ws_message = {
                'type': 'safety_incident',
                'data': {
                    'incident_id': incident_id,
                    'profile_id': profile_id,
                    'child_name': child_name,
                    'severity': severity,
                    'incident_type': incident_type,
                    'content_preview': content_snippet[:100] if content_snippet else '',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'requires_attention': severity in ['major', 'critical']
                }
            }

            # Create async task to broadcast
            # Use asyncio.create_task to avoid blocking the synchronous log_incident method
            try:
                # Try to get running loop (raises RuntimeError if no loop running)
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.create_task(ws_manager.broadcast_to_parent(parent_id, ws_message))
                    logger.info(f"WebSocket broadcast sent for incident {incident_id} to parent {parent_id}")
                except RuntimeError:
                    # No running loop - create new one and run (testing/sync scenario)
                    asyncio.run(ws_manager.broadcast_to_parent(parent_id, ws_message))
                    logger.info(f"WebSocket broadcast sent for incident {incident_id} to parent {parent_id}")

            except (ConnectionError, OSError, RuntimeError) as e:
                # Any other error - log but don't fail incident logging
                logger.debug(f"Could not broadcast via WebSocket: {e}")

        except DB_ERRORS as e:
            logger.error(f"Failed to broadcast incident via WebSocket: {e}")
            # Non-critical error, don't fail the incident logging

    def cleanup_old_incidents(self, retention_days: Optional[int] = None):
        """
        Clean up old resolved incidents

        Args:
            retention_days: Days to retain (uses config default if not specified)
        """
        try:
            retention_days = retention_days or safety_config.SAFETY_LOG_RETENTION_DAYS
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

            # Delete old resolved incidents
            self.db.execute_write(
                """
                DELETE FROM safety_incidents
                WHERE resolved = 1 AND resolved_at < ?
                """,
                (cutoff_date,)
            )

            logger.info(f"Cleaned up incidents older than {retention_days} days")

        except DB_ERRORS as e:
            logger.error(f"Failed to cleanup old incidents: {e}")


# Singleton instance
incident_logger = IncidentLogger()


# Export public interface
__all__ = [
    'IncidentLogger',
    'SafetyIncident',
    'incident_logger'
]
