"""Read/query methods for ProfileManager (mixin). Extracted verbatim."""

from typing import List, Optional

from storage.db_adapters import DB_ERRORS
from utils.cache import cached
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

from core.profile_manager.models import ChildProfile


class _ProfileQueryMixin:
    """Read-side methods for ProfileManager (composed in __init__.py)."""

    @cached(ttl=120, key_prefix="child_profile")
    def get_profile(self, profile_id: str) -> Optional[ChildProfile]:
        try:
            rows = self.db.execute_query(
                "SELECT * FROM child_profiles WHERE profile_id = ?", (profile_id,)
            )
        except DB_ERRORS as e:
            logger.debug(
                f"Failed to query profile {sanitize_log_value(profile_id)!r} (non-critical): {e}"
            )
            return None
        if not rows:
            return None
        row = rows[0]

        def g(key, idx):
            try:
                return row[key]
            except (KeyError, IndexError, TypeError):
                try:
                    return row[idx]
                except (KeyError, IndexError, TypeError):
                    return None

        # Get subjects for this profile
        subjects = []
        try:
            subject_rows = self.db.execute_query(
                "SELECT subject FROM profile_subjects WHERE profile_id = ?",
                (profile_id,),
            )
            subjects = [
                row[0] if isinstance(row, tuple) else row["subject"]
                for row in subject_rows
            ]
        except DB_ERRORS as e:
            logger.debug(
                f"Failed to query subjects for profile {sanitize_log_value(profile_id)!r} (non-critical): {e}"
            )

        # Get session counts - prefer sessions table, fallback to profile table
        total_sessions = 0
        total_questions = 0
        try:
            session_stats = self.db.execute_query(
                "SELECT COUNT(*) as count, SUM(COALESCE(questions_asked, 0)) as questions FROM sessions WHERE profile_id = ?",
                (profile_id,),
            )
            if session_stats:
                stat_row = session_stats[0]
                sessions_count = (
                    stat_row["count"] if isinstance(stat_row, dict) else stat_row[0]
                )
                questions_count = (
                    stat_row["questions"] if isinstance(stat_row, dict) else stat_row[1]
                )

                # If there are actual sessions, use those counts
                if sessions_count and sessions_count > 0:
                    total_sessions = sessions_count
                    total_questions = questions_count if questions_count else 0
                else:
                    # No sessions yet, use values from profile table
                    total_sessions = g("total_sessions", 9) or 0
                    total_questions = g("total_questions", 10) or 0
        except DB_ERRORS as e:
            # Fallback to profile table values
            logger.debug(
                f"Failed to query session stats for profile {sanitize_log_value(profile_id)!r} (non-critical): {e}"
            )
            total_sessions = g("total_sessions", 9) or 0
            total_questions = g("total_questions", 10) or 0

        return ChildProfile(
            profile_id=g("profile_id", 0),
            parent_id=g("parent_id", 1),
            name=g("name", 2) or g("name", 1),
            age=g("age", 3) or 0,
            grade=g("grade", 4) or g("grade_level", 4) or "K",
            avatar=g("avatar", 5) or g("avatar_url", 5) or "default",
            learning_level=g("learning_level", 7) or "adaptive",
            daily_time_limit_minutes=g("daily_time_limit_minutes", 8) or 120,
            is_active=bool(g("is_active", 14)),
            total_sessions=total_sessions,
            total_questions=total_questions,
            last_active=g("last_active", 13),
            subjects_focus=subjects,
        )

    def get_profiles_by_parent(self, parent_id: str) -> List[ChildProfile]:
        try:
            rows = self.db.execute_query(
                "SELECT * FROM child_profiles WHERE parent_id = ?", (parent_id,)
            )
        except DB_ERRORS as e:
            logger.debug(
                f"Failed to query profiles for parent {sanitize_log_value(parent_id)!r} (non-critical): {e}"
            )
            return []

        if not rows:
            return []

        # Build profiles from row data
        profiles = []
        profile_ids = []

        for row in rows:
            try:
                # Build profile directly from row data to avoid N+1 query problem
                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, IndexError, TypeError):
                        try:
                            return row[idx]
                        except (KeyError, IndexError, TypeError):
                            return None

                profile_id = g("profile_id", 0)
                profile_ids.append(profile_id)

                profile = ChildProfile(
                    profile_id=profile_id,
                    parent_id=g("parent_id", 1),
                    name=g("name", 2) or g("name", 1),
                    age=g("age", 3) or 0,
                    grade=g("grade", 4) or g("grade_level", 4) or "K",
                    avatar=g("avatar", 5) or g("avatar_url", 5) or "default",
                    learning_level=g("learning_level", 7) or "adaptive",
                    daily_time_limit_minutes=g("daily_time_limit_minutes", 8) or 120,
                    is_active=bool(g("is_active", 14)),
                    total_sessions=0,  # Will be updated from sessions table
                    total_questions=0,  # Will be updated from sessions table
                    last_active=g("last_active", 13),
                    subjects_focus=[],  # Skip subject lookup for list view (use get_profile for details)
                )
                profiles.append(profile)
            except (KeyError, IndexError, TypeError) as e:
                # Log parsing failure but continue with other profiles
                logger.warning(
                    f"Failed to parse profile row: {e}",
                    extra={"row_data": str(row)[:100]},  # Truncate for safety
                )
                continue

        # Get real-time session counts for all profiles in one bulk query
        if profile_ids:
            try:
                placeholders = ",".join("?" * len(profile_ids))
                query = f"""
                    SELECT profile_id, COUNT(*) as count, SUM(COALESCE(questions_asked, 0)) as questions
                    FROM sessions
                    WHERE profile_id IN ({placeholders})
                    GROUP BY profile_id
                """
                session_stats = self.db.execute_query(query, tuple(profile_ids))

                # Build lookup dict for O(1) access
                stats_map = {}
                for stat in session_stats:
                    pid = stat["profile_id"] if isinstance(stat, dict) else stat[0]
                    count = stat["count"] if isinstance(stat, dict) else stat[1]
                    questions = stat["questions"] if isinstance(stat, dict) else stat[2]
                    stats_map[pid] = (count or 0, questions or 0)

                # Update profiles with real session counts from sessions table
                for profile in profiles:
                    if profile.profile_id in stats_map:
                        sessions_count, questions_count = stats_map[profile.profile_id]
                        profile.total_sessions = sessions_count
                        profile.total_questions = questions_count

                # For profiles without session records, fall back to counter columns
                # This supports increment_session_count() / increment_question_count() methods
                profiles_without_sessions = [
                    p for p in profiles if p.profile_id not in stats_map
                ]
                if profiles_without_sessions:
                    try:
                        # Bulk query for counter columns
                        counter_ids = [p.profile_id for p in profiles_without_sessions]
                        placeholders = ",".join("?" * len(counter_ids))
                        counter_query = f"SELECT profile_id, total_sessions, total_questions FROM child_profiles WHERE profile_id IN ({placeholders})"
                        counter_rows = self.db.execute_query(
                            counter_query, tuple(counter_ids)
                        )

                        # Build lookup dict
                        counter_map = {}
                        for row in counter_rows:
                            pid = row["profile_id"] if isinstance(row, dict) else row[0]
                            sessions = (
                                row["total_sessions"]
                                if isinstance(row, dict)
                                else row[1]
                            )
                            questions = (
                                row["total_questions"]
                                if isinstance(row, dict)
                                else row[2]
                            )
                            counter_map[pid] = (sessions or 0, questions or 0)

                        # Update profiles
                        for profile in profiles_without_sessions:
                            if profile.profile_id in counter_map:
                                sessions_count, questions_count = counter_map[
                                    profile.profile_id
                                ]
                                profile.total_sessions = sessions_count
                                profile.total_questions = questions_count
                    except DB_ERRORS as e:
                        # Log but don't fail - profiles will keep default counts (0, 0)
                        logger.warning(
                            f"Failed to fetch counter columns for profiles: {e}",
                            extra={"profile_count": len(profiles_without_sessions)},
                        )
            except DB_ERRORS as e:
                # Log but don't fail - profiles will have 0 counts
                logger.warning(
                    f"Failed to fetch session stats for profiles: {e}",
                    extra={"profile_count": len(profile_ids)},
                )

            # Get subjects for all profiles in one bulk query
            try:
                placeholders = ",".join("?" * len(profile_ids))
                query = f"SELECT profile_id, subject FROM profile_subjects WHERE profile_id IN ({placeholders})"
                subject_rows = self.db.execute_query(query, tuple(profile_ids))

                # Build lookup dict mapping profile_id to list of subjects
                subjects_map: dict = {}
                for row in subject_rows:
                    pid = row["profile_id"] if isinstance(row, dict) else row[0]
                    subject = row["subject"] if isinstance(row, dict) else row[1]
                    if pid not in subjects_map:
                        subjects_map[pid] = []
                    subjects_map[pid].append(subject)

                # Update profiles with subjects
                for profile in profiles:
                    if profile.profile_id in subjects_map:
                        profile.subjects_focus = subjects_map[profile.profile_id]
            except DB_ERRORS as e:
                # Log but don't fail - profiles will have empty subjects
                logger.warning(
                    f"Failed to fetch subjects for profiles: {e}",
                    extra={"profile_count": len(profile_ids)},
                )

        return profiles

    def get_active_profiles(self, parent_id: str) -> List[ChildProfile]:
        """
        Get all active profiles for a parent

        Args:
            parent_id: Parent ID

        Returns:
            List of active ChildProfile objects
        """
        try:
            rows = self.db.execute_query(
                "SELECT * FROM child_profiles WHERE parent_id = ? AND is_active = 1",
                (parent_id,),
            )
        except DB_ERRORS as e:
            logger.debug(f"Failed to query active profiles (non-critical): {e}")
            return []

        profiles = []
        for row in rows:
            p = self._row_to_profile(row)
            if p:
                profiles.append(p)
        return profiles

    def get_family_statistics(self, parent_id: str) -> dict:
        """
        Get family-level statistics for all profiles

        Args:
            parent_id: Parent ID

        Returns:
            Dictionary with family statistics
        """
        profiles = self.get_profiles_by_parent(parent_id)

        # Calculate stats - profile objects already have the right counts
        # (from sessions table if they exist, otherwise from profile table)
        total_sessions = sum(p.total_sessions for p in profiles)
        total_questions = sum(p.total_questions for p in profiles)

        # Get total minutes from sessions table
        total_minutes = 0
        if profiles:
            try:
                # Build IN clause placeholders safely
                placeholders = ",".join("?" * len(profiles))
                query = f"SELECT SUM(duration_minutes) FROM sessions WHERE profile_id IN ({placeholders})"
                minutes_rows = self.db.execute_query(
                    query, tuple(p.profile_id for p in profiles)
                )
                if minutes_rows:
                    row = minutes_rows[0]
                    # Safely extract value whether row is dict or tuple
                    if isinstance(row, dict):
                        total_minutes = row.get("SUM(duration_minutes)") or 0
                    elif row and len(row) > 0 and row[0]:
                        total_minutes = row[0]
            except DB_ERRORS as e:
                logger.debug(f"Failed to query total minutes (non-critical): {e}")

        active_profiles = len([p for p in profiles if p.is_active])

        return {
            "total_profiles": len(profiles),
            "active_profiles": active_profiles,
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "total_minutes": total_minutes,
            "profiles": profiles,
        }

    def get_most_active_profile(self, parent_id: str) -> Optional[ChildProfile]:
        """
        Get the most active profile for a parent based on total_sessions

        Args:
            parent_id: Parent ID

        Returns:
            ChildProfile with most sessions, or None
        """
        profiles = self.get_profiles_by_parent(parent_id)
        if not profiles:
            return None

        # Profile objects already have correct session counts
        # (from sessions table if available, otherwise from profile table)
        most_active = max(profiles, key=lambda p: p.total_sessions)
        return most_active

    def get_profiles_by_age_range(
        self, parent_id: str, min_age: int, max_age: int
    ) -> List[ChildProfile]:
        """
        Get profiles within an age range

        Args:
            parent_id: Parent ID
            min_age: Minimum age (inclusive)
            max_age: Maximum age (inclusive)

        Returns:
            List of ChildProfile objects
        """
        profiles = self.get_profiles_by_parent(parent_id)
        return [p for p in profiles if min_age <= p.age <= max_age]
