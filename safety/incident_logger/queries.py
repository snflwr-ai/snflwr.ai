"""Read-only incident queries + report generation (mixin).

Extracted verbatim from the former monolithic safety/incident_logger.py.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    InvalidToken = Exception  # type: ignore[misc,assignment]

logger = get_logger(__name__)

from safety.incident_logger.models import SafetyIncident


class _IncidentQueryMixin:
    """Read-side methods for IncidentLogger (composed in __init__.py)."""

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
                (incident_id,),
            )

            if not results:
                return None

            row = results[0]

            # Decrypt content
            content_snippet = self.encryption.decrypt_string(row["content_snippet"])

            # Decrypt metadata if present
            metadata = {}
            if row["metadata"]:
                try:
                    metadata = self.encryption.decrypt_dict(row["metadata"])
                except (InvalidToken, ValueError, TypeError) as e:
                    logger.debug(f"Failed to decrypt incident metadata: {e}")
                    metadata = {}

            # Decrypt resolution notes if present
            resolution_notes = None
            if row["resolution_notes"]:
                try:
                    resolution_notes = self.encryption.decrypt_string(
                        row["resolution_notes"]
                    )
                except (InvalidToken, ValueError, TypeError) as e:
                    logger.debug(f"Failed to decrypt resolution notes: {e}")
                    resolution_notes = None

            incident = SafetyIncident(
                incident_id=row["incident_id"],
                profile_id=row["profile_id"],
                session_id=row["session_id"],
                incident_type=row["incident_type"],
                severity=row["severity"],
                content_snippet=content_snippet,
                timestamp=datetime.fromisoformat(row["timestamp"]),
                parent_notified=bool(row["parent_notified"]),
                parent_notified_at=(
                    datetime.fromisoformat(row["parent_notified_at"])
                    if row["parent_notified_at"]
                    else None
                ),
                resolved=bool(row["resolved"]),
                resolved_at=(
                    datetime.fromisoformat(row["resolved_at"])
                    if row["resolved_at"]
                    else None
                ),
                resolution_notes=resolution_notes,
                metadata=metadata,
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
        unresolved_only: bool = False,
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
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

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
                content_snippet = self.encryption.decrypt_string(row["content_snippet"])

                # Decrypt metadata
                metadata = {}
                if row["metadata"]:
                    try:
                        metadata = self.encryption.decrypt_dict(row["metadata"])
                    except (InvalidToken, ValueError, TypeError) as e:
                        logger.debug(f"Failed to decrypt incident metadata: {e}")
                        metadata = {}

                # Decrypt resolution notes if present
                resolution_notes = None
                if row["resolution_notes"]:
                    try:
                        resolution_notes = self.encryption.decrypt_string(
                            row["resolution_notes"]
                        )
                    except (InvalidToken, ValueError, TypeError) as e:
                        logger.debug(f"Failed to decrypt resolution notes: {e}")
                        resolution_notes = None

                incident = SafetyIncident(
                    incident_id=row["incident_id"],
                    profile_id=row["profile_id"],
                    session_id=row["session_id"],
                    incident_type=row["incident_type"],
                    severity=row["severity"],
                    content_snippet=content_snippet,
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    parent_notified=bool(row["parent_notified"]),
                    parent_notified_at=(
                        datetime.fromisoformat(row["parent_notified_at"])
                        if row["parent_notified_at"]
                        else None
                    ),
                    resolved=bool(row["resolved"]),
                    resolved_at=(
                        datetime.fromisoformat(row["resolved_at"])
                        if row["resolved_at"]
                        else None
                    ),
                    resolution_notes=resolution_notes,
                    metadata=metadata,
                )
                incidents.append(incident)

            return incidents

        except DB_ERRORS as e:
            logger.error(f"Failed to get profile incidents: {e}")
            return []

    def get_unresolved_incidents(self, profile_id: str) -> List[SafetyIncident]:
        """Return unresolved incidents for a profile."""
        return self.get_profile_incidents(profile_id, unresolved_only=True)

    def get_incidents_by_severity(
        self, profile_id: str, min_severity: str = None, severity: str = None
    ) -> List[SafetyIncident]:
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
            severity_levels = {"minor": 1, "major": 2, "critical": 3}

            if min_severity:
                # Filter for incidents >= min_severity
                min_level = severity_levels.get(min_severity, 0)
                valid_severities = [
                    s for s, l in severity_levels.items() if l >= min_level
                ]

                placeholders = ",".join("?" * len(valid_severities))
                rows = self.db.execute_query(
                    f"""
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ? AND severity IN ({placeholders})
                    ORDER BY timestamp DESC
                    """,
                    (profile_id, *valid_severities),
                )
            elif severity:
                # Exact match (backward compatibility)
                rows = self.db.execute_query(
                    """
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ? AND severity = ?
                    ORDER BY timestamp DESC
                    """,
                    (profile_id, severity),
                )
            else:
                # No filter, return all
                rows = self.db.execute_query(
                    """
                    SELECT * FROM safety_incidents
                    WHERE profile_id = ?
                    ORDER BY timestamp DESC
                    """,
                    (profile_id,),
                )

            incidents = []
            for row in rows:

                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, IndexError, TypeError):
                        return row[idx] if idx < len(row) else None

                timestamp_str = g("timestamp", 6)
                notified_str = g("parent_notified_at", 8)
                resolved_str = g("resolved_at", 10)

                try:
                    timestamp = (
                        datetime.fromisoformat(timestamp_str)
                        if timestamp_str
                        else datetime.now(timezone.utc)
                    )
                except ValueError:
                    logger.debug(f"Failed to parse timestamp: {timestamp_str}")
                    timestamp = datetime.now(timezone.utc)

                try:
                    parent_notified_at = (
                        datetime.fromisoformat(notified_str) if notified_str else None
                    )
                except ValueError:
                    logger.debug(f"Failed to parse parent_notified_at: {notified_str}")
                    parent_notified_at = None

                try:
                    resolved_at = (
                        datetime.fromisoformat(resolved_str) if resolved_str else None
                    )
                except ValueError:
                    logger.debug(f"Failed to parse resolved_at: {resolved_str}")
                    resolved_at = None

                # Decrypt content snippet
                raw_snippet = g("content_snippet", 5) or ""
                try:
                    content_snippet = self.encryption.decrypt_string(raw_snippet)
                except Exception:
                    content_snippet = raw_snippet

                # Parse metadata (may be plain JSON or encrypted)
                metadata_str = g("metadata", 12)
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
                raw_notes = g("resolution_notes", 11)
                resolution_notes = None
                if raw_notes:
                    try:
                        resolution_notes = self.encryption.decrypt_string(raw_notes)
                    except Exception:
                        resolution_notes = raw_notes

                incident = SafetyIncident(
                    incident_id=g("incident_id", 0),
                    profile_id=g("profile_id", 1),
                    session_id=g("session_id", 2),
                    incident_type=g("incident_type", 3),
                    severity=g("severity", 4),
                    content_snippet=content_snippet,
                    timestamp=timestamp,
                    parent_notified=bool(g("parent_notified", 7)),
                    parent_notified_at=parent_notified_at,
                    resolved=bool(g("resolved", 9)),
                    resolved_at=resolved_at,
                    resolution_notes=resolution_notes,
                    metadata=metadata,
                )
                incidents.append(incident)

            return incidents

        except DB_ERRORS as e:
            logger.error(f"Failed to get incidents by severity: {e}")
            return []

    def get_incident_statistics(
        self, profile_id: Optional[str] = None, days: int = 30
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
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

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
                    (profile_id, cutoff_date),
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
                    (cutoff_date,),
                )

            # Process results
            stats: Dict[str, Any] = {
                "total_incidents": 0,
                "by_severity": {},
                "unresolved": 0,
                "awaiting_parent_notification": 0,
                "time_period_days": days,
            }

            for row in results:
                severity = row["severity"]
                count = row["count"]
                unresolved = row["unresolved"]
                not_notified = row["not_notified"]

                stats["by_severity"][severity] = {
                    "count": count,
                    "unresolved": unresolved,
                    "not_notified": not_notified,
                }
                stats["total_incidents"] += count
                stats["unresolved"] += unresolved
                stats["awaiting_parent_notification"] += not_notified

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
                    (profile_id, cutoff_date),
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
                    (cutoff_date,),
                )

            stats["top_incident_types"] = [
                {"type": row["incident_type"], "count": row["count"]}
                for row in type_results
            ]

            return stats

        except DB_ERRORS as e:
            logger.error(f"Failed to get incident statistics: {e}")
            return {}

    def generate_parent_report(
        self, parent_id: str, profile_id: Optional[str] = None, days: int = 7
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
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

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
                    (parent_id, cutoff_date, profile_id),
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
                    (parent_id, cutoff_date),
                )

            report: Dict[str, Any] = {
                "parent_id": parent_id,
                "report_period_days": days,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "profiles": [],
            }

            for row in results:
                profile_report = {
                    "profile_id": row["profile_id"],
                    "child_name": row["child_name"],
                    "total_incidents": row["incident_count"],
                    "by_severity": {
                        "critical": row["critical"],
                        "major": row["major"],
                        "minor": row["minor"],
                    },
                    "latest_incident": row["latest_incident"],
                }

                # Get recent incidents for this profile
                recent_incidents = self.get_profile_incidents(
                    row["profile_id"], days=days, unresolved_only=True
                )

                profile_report["unresolved_incidents"] = [
                    {
                        "incident_id": inc.incident_id,
                        "type": inc.incident_type,
                        "severity": inc.severity,
                        "timestamp": inc.timestamp.isoformat(),
                        "content_preview": (
                            inc.content_snippet[:100] + "..."
                            if len(inc.content_snippet) > 100
                            else inc.content_snippet
                        ),
                    }
                    for inc in recent_incidents[:5]  # Top 5 unresolved
                ]

                report["profiles"].append(profile_report)

            # Calculate summary
            report["summary"] = {
                "total_profiles_with_incidents": len(report["profiles"]),
                "total_incidents": sum(
                    p["total_incidents"] for p in report["profiles"]
                ),
                "critical_incidents": sum(
                    p["by_severity"]["critical"] for p in report["profiles"]
                ),
                "major_incidents": sum(
                    p["by_severity"]["major"] for p in report["profiles"]
                ),
                "minor_incidents": sum(
                    p["by_severity"]["minor"] for p in report["profiles"]
                ),
            }

            return report

        except DB_ERRORS as e:
            logger.error(f"Failed to generate parent report: {e}")
            return {}
