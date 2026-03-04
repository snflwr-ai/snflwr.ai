"""
Profile manager for tests.

Provides a small, DB-backed implementation used by unit tests. Stores child
profiles in the `child_profiles` table (schema is created by
`storage.database.DatabaseManager.initialize_database`).
"""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Tuple

from utils.logger import get_logger
from utils.cache import cached
from storage.db_adapters import DB_ERRORS

logger = get_logger(__name__)


@dataclass
class ChildProfile:
    profile_id: str
    parent_id: str
    name: str
    age: int
    grade: str
    avatar: str = 'default'
    learning_level: str = 'adaptive'
    daily_time_limit_minutes: int = 120
    is_active: bool = True
    total_sessions: int = 0
    total_questions: int = 0
    last_active: Optional[str] = None
    subjects_focus: Optional[List[str]] = None

    def to_dict(self) -> dict:
        """Convert profile to dictionary for JSON serialization"""
        return {
            'profile_id': self.profile_id,
            'parent_id': self.parent_id,
            'name': self.name,
            'age': self.age,
            'grade': self.grade,
            'avatar': self.avatar,
            'learning_level': self.learning_level,
            'daily_time_limit_minutes': self.daily_time_limit_minutes,
            'is_active': self.is_active,
            'total_sessions': self.total_sessions,
            'total_questions': self.total_questions,
            'last_active': self.last_active,
            'subjects_focus': self.subjects_focus or []
        }


class ProfileError(Exception):
    pass


class ProfileValidationError(ProfileError):
    pass


class ProfileNotFoundError(ProfileError):
    pass


class PermissionDeniedError(ProfileError):
    pass


class ProfileManager:
    """Simple DB-backed profile manager for tests."""

    def __init__(self, db_manager):
        self.db = db_manager

    def _invalidate_profile_cache(self, profile_id: str):
        """Invalidate the cached get_profile result for a given profile."""
        from utils.cache import cache
        cache.delete(f"child_profile:{profile_id}", namespace="snflwr")

    def create_profile(self, parent_id: str, name: str, age: int, grade: str, avatar: str = 'default', learning_level: str = 'adaptive', daily_time_limit_minutes: int = 120) -> ChildProfile:
        # Basic validation
        if not name or len(name) < 2:
            raise ProfileValidationError("Name must be at least 2 characters")
        if age < 5 or age > 18:
            raise ProfileValidationError("Age must be between 5 and 18 for K-12")

        # Verify parent exists (if table present)
        try:
            rows = self.db.execute_query("SELECT parent_id FROM accounts WHERE parent_id = ?", (parent_id,))
            if not rows:
                raise ProfileError("Invalid parent_id")
        except ProfileError:
            raise
        except DB_ERRORS as e:
            # Table may not exist in test environments — log it so we know
            logger.warning(f"Could not verify parent_id {parent_id} (table may not exist): {e}")

        # Check for duplicate name within family
        try:
            existing = self.db.execute_query(
                "SELECT name FROM child_profiles WHERE parent_id = ? AND name = ? AND is_active = 1",
                (parent_id, name)
            )
            if existing:
                raise ProfileValidationError(f"Profile with name '{name}' already exists for this parent")
        except ProfileValidationError:
            raise
        except DB_ERRORS as e:
            logger.debug(f"Duplicate name check skipped (non-critical): {e}")

        profile_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()

        # Insert into DB if possible
        try:
            self.db.execute_write(
                "INSERT INTO child_profiles (profile_id, parent_id, name, age, grade, created_at, avatar, learning_level, daily_time_limit_minutes, is_active, total_sessions, total_questions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, parent_id, name, age, grade, created_at, avatar, learning_level, daily_time_limit_minutes, 1, 0, 0)
            )
        except DB_ERRORS as e:
            error_msg = str(e).lower()
            if 'foreign key' in error_msg or ('constraint' in error_msg and 'parent' in error_msg):
                raise ProfileError(f"Invalid parent_id: {parent_id}")
            logger.error(f"Failed to write profile {profile_id} to database: {e}")
            raise ProfileError(f"Could not create profile: database write failed") from e

        return ChildProfile(
            profile_id=profile_id,
            parent_id=parent_id,
            name=name,
            age=age,
            grade=grade,
            avatar=avatar,
            learning_level=learning_level,
            daily_time_limit_minutes=daily_time_limit_minutes,
            is_active=True,
            total_sessions=0,
            total_questions=0,
            last_active=None,
            subjects_focus=[]
        )

    @cached(ttl=120, key_prefix="child_profile")
    def get_profile(self, profile_id: str) -> Optional[ChildProfile]:
        try:
            rows = self.db.execute_query("SELECT * FROM child_profiles WHERE profile_id = ?", (profile_id,))
        except DB_ERRORS as e:
            logger.debug(f"Failed to query profile {profile_id} (non-critical): {e}")
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
                (profile_id,)
            )
            subjects = [row[0] if isinstance(row, tuple) else row['subject'] for row in subject_rows]
        except DB_ERRORS as e:
            logger.debug(f"Failed to query subjects for profile {profile_id} (non-critical): {e}")

        # Get session counts - prefer sessions table, fallback to profile table
        total_sessions = 0
        total_questions = 0
        try:
            session_stats = self.db.execute_query(
                "SELECT COUNT(*) as count, SUM(COALESCE(questions_asked, 0)) as questions FROM sessions WHERE profile_id = ?",
                (profile_id,)
            )
            if session_stats:
                stat_row = session_stats[0]
                sessions_count = stat_row['count'] if isinstance(stat_row, dict) else stat_row[0]
                questions_count = stat_row['questions'] if isinstance(stat_row, dict) else stat_row[1]

                # If there are actual sessions, use those counts
                if sessions_count and sessions_count > 0:
                    total_sessions = sessions_count
                    total_questions = questions_count if questions_count else 0
                else:
                    # No sessions yet, use values from profile table
                    total_sessions = g('total_sessions', 9) or 0
                    total_questions = g('total_questions', 10) or 0
        except DB_ERRORS as e:
            # Fallback to profile table values
            logger.debug(f"Failed to query session stats for profile {profile_id} (non-critical): {e}")
            total_sessions = g('total_sessions', 9) or 0
            total_questions = g('total_questions', 10) or 0

        return ChildProfile(
            profile_id=g('profile_id', 0),
            parent_id=g('parent_id', 1),
            name=g('name', 2) or g('name', 1),
            age=g('age', 3) or 0,
            grade=g('grade', 4) or g('grade_level', 4) or 'K',
            avatar=g('avatar', 5) or g('avatar_url', 5) or 'default',
            learning_level=g('learning_level', 7) or 'adaptive',
            daily_time_limit_minutes=g('daily_time_limit_minutes', 8) or 120,
            is_active=bool(g('is_active', 14)),
            total_sessions=total_sessions,
            total_questions=total_questions,
            last_active=g('last_active', 13),
            subjects_focus=subjects
        )

    def get_profiles_by_parent(self, parent_id: str) -> List[ChildProfile]:
        try:
            rows = self.db.execute_query("SELECT * FROM child_profiles WHERE parent_id = ?", (parent_id,))
        except DB_ERRORS as e:
            logger.debug(f"Failed to query profiles for parent {parent_id} (non-critical): {e}")
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

                profile_id = g('profile_id', 0)
                profile_ids.append(profile_id)

                profile = ChildProfile(
                    profile_id=profile_id,
                    parent_id=g('parent_id', 1),
                    name=g('name', 2) or g('name', 1),
                    age=g('age', 3) or 0,
                    grade=g('grade', 4) or g('grade_level', 4) or 'K',
                    avatar=g('avatar', 5) or g('avatar_url', 5) or 'default',
                    learning_level=g('learning_level', 7) or 'adaptive',
                    daily_time_limit_minutes=g('daily_time_limit_minutes', 8) or 120,
                    is_active=bool(g('is_active', 14)),
                    total_sessions=0,  # Will be updated from sessions table
                    total_questions=0,  # Will be updated from sessions table
                    last_active=g('last_active', 13),
                    subjects_focus=[]  # Skip subject lookup for list view (use get_profile for details)
                )
                profiles.append(profile)
            except (KeyError, IndexError, TypeError) as e:
                # Log parsing failure but continue with other profiles
                logger.warning(
                    f"Failed to parse profile row: {e}",
                    extra={'row_data': str(row)[:100]}  # Truncate for safety
                )
                continue

        # Get real-time session counts for all profiles in one bulk query
        if profile_ids:
            try:
                placeholders = ','.join('?' * len(profile_ids))
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
                    pid = stat['profile_id'] if isinstance(stat, dict) else stat[0]
                    count = stat['count'] if isinstance(stat, dict) else stat[1]
                    questions = stat['questions'] if isinstance(stat, dict) else stat[2]
                    stats_map[pid] = (count or 0, questions or 0)

                # Update profiles with real session counts from sessions table
                for profile in profiles:
                    if profile.profile_id in stats_map:
                        sessions_count, questions_count = stats_map[profile.profile_id]
                        profile.total_sessions = sessions_count
                        profile.total_questions = questions_count

                # For profiles without session records, fall back to counter columns
                # This supports increment_session_count() / increment_question_count() methods
                profiles_without_sessions = [p for p in profiles if p.profile_id not in stats_map]
                if profiles_without_sessions:
                    try:
                        # Bulk query for counter columns
                        counter_ids = [p.profile_id for p in profiles_without_sessions]
                        placeholders = ','.join('?' * len(counter_ids))
                        counter_query = f"SELECT profile_id, total_sessions, total_questions FROM child_profiles WHERE profile_id IN ({placeholders})"
                        counter_rows = self.db.execute_query(counter_query, tuple(counter_ids))

                        # Build lookup dict
                        counter_map = {}
                        for row in counter_rows:
                            pid = row['profile_id'] if isinstance(row, dict) else row[0]
                            sessions = row['total_sessions'] if isinstance(row, dict) else row[1]
                            questions = row['total_questions'] if isinstance(row, dict) else row[2]
                            counter_map[pid] = (sessions or 0, questions or 0)

                        # Update profiles
                        for profile in profiles_without_sessions:
                            if profile.profile_id in counter_map:
                                sessions_count, questions_count = counter_map[profile.profile_id]
                                profile.total_sessions = sessions_count
                                profile.total_questions = questions_count
                    except DB_ERRORS as e:
                        # Log but don't fail - profiles will keep default counts (0, 0)
                        logger.warning(
                            f"Failed to fetch counter columns for profiles: {e}",
                            extra={'profile_count': len(profiles_without_sessions)}
                        )
            except DB_ERRORS as e:
                # Log but don't fail - profiles will have 0 counts
                logger.warning(
                    f"Failed to fetch session stats for profiles: {e}",
                    extra={'profile_count': len(profile_ids)}
                )

            # Get subjects for all profiles in one bulk query
            try:
                placeholders = ','.join('?' * len(profile_ids))
                query = f"SELECT profile_id, subject FROM profile_subjects WHERE profile_id IN ({placeholders})"
                subject_rows = self.db.execute_query(query, tuple(profile_ids))

                # Build lookup dict mapping profile_id to list of subjects
                subjects_map = {}
                for row in subject_rows:
                    pid = row['profile_id'] if isinstance(row, dict) else row[0]
                    subject = row['subject'] if isinstance(row, dict) else row[1]
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
                    extra={'profile_count': len(profile_ids)}
                )

        return profiles

    def update_profile(self, profile_id: str, **kwargs) -> bool:
        """
        Update profile fields

        Args:
            profile_id: Profile to update
            **kwargs: Fields to update (name, age, grade, learning_level, daily_time_limit_minutes, is_active)

        Returns:
            True if successful

        Raises:
            ProfileValidationError: If validation fails
            ProfileError: If update fails
        """
        # Validate inputs before updating
        if 'age' in kwargs:
            age = kwargs['age']
            if age < 5 or age > 18:
                raise ProfileValidationError("Age must be between 5 and 18 for K-12")

        if 'learning_level' in kwargs:
            level = kwargs['learning_level']
            valid_levels = ['beginner', 'advanced', 'adaptive']
            if level not in valid_levels:
                raise ProfileValidationError(f"Learning level must be one of: {', '.join(valid_levels)}")

        if 'name' in kwargs:
            name = kwargs['name']
            if not name or len(name) < 2:
                raise ProfileValidationError("Name must be at least 2 characters")

        if 'grade' in kwargs:
            grade = kwargs['grade']
            valid_grades = ['K', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12']
            # Also accept grades with suffixes like "1st", "2nd", "6th", etc.
            valid_grades_with_suffix = valid_grades + ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th', '11th', '12th']
            if grade not in valid_grades_with_suffix:
                raise ProfileValidationError(f"Grade must be one of: {', '.join(valid_grades)}")

        if 'daily_time_limit_minutes' in kwargs:
            limit = kwargs['daily_time_limit_minutes']
            if not isinstance(limit, int) or limit < 0 or limit > 1440:
                raise ProfileValidationError("Daily time limit must be between 0 and 1440 minutes (24 hours)")

        if 'is_active' in kwargs:
            is_active = kwargs['is_active']
            if not isinstance(is_active, bool):
                raise ProfileValidationError("is_active must be a boolean")

        updates = []
        params = []
        allowed = ['name', 'age', 'grade', 'grade_level', 'learning_level', 'daily_time_limit_minutes', 'is_active']
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            updates.append(f"{k} = ?")
            params.append(v)
        if not updates:
            return True
        params.append(profile_id)
        query = f"UPDATE child_profiles SET {', '.join(updates)} WHERE profile_id = ?"
        try:
            self.db.execute_write(query, tuple(params))
            # Invalidate cache for this profile
            from utils.cache import cache
            cache.delete(f"child_profile:{profile_id}", namespace="snflwr")
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to update profile: {e}")
            raise ProfileError(f"Failed to update profile: {e}")

    def update_profile_with_permission_check(self, parent_id: str, profile_id: str, **kwargs) -> bool:
        """
        Update profile fields with permission check

        Args:
            parent_id: Parent ID requesting the update
            profile_id: Profile to update
            **kwargs: Fields to update

        Returns:
            True if successful

        Raises:
            PermissionDeniedError: If parent doesn't own the profile
            ProfileValidationError: If validation fails
            ProfileError: If update fails
        """
        # Check if parent owns this profile
        try:
            rows = self.db.execute_query(
                "SELECT parent_id FROM child_profiles WHERE profile_id = ?",
                (profile_id,)
            )
            if not rows:
                raise ProfileNotFoundError(f"Profile not found: {profile_id}")

            row = rows[0]
            owner_parent_id = row[0] if isinstance(row, tuple) else row['parent_id']

            if owner_parent_id != parent_id:
                raise PermissionDeniedError(f"Parent {parent_id} does not have permission to modify profile {profile_id}")

        except PermissionDeniedError:
            raise
        except ProfileNotFoundError:
            raise
        except DB_ERRORS as e:
            logger.error(f"Permission check failed: {e}")
            raise ProfileError(f"Permission check failed: {e}")

        # Proceed with update
        return self.update_profile(profile_id, **kwargs)

    def deactivate_profile(self, profile_id: str) -> bool:
        """
        Deactivate a profile

        Args:
            profile_id: Profile to deactivate

        Returns:
            True if successful

        Raises:
            ProfileError: If deactivation fails
        """
        try:
            self.db.execute_write("UPDATE child_profiles SET is_active = 0 WHERE profile_id = ?", (profile_id,))
            self._invalidate_profile_cache(profile_id)
            return True
        except DB_ERRORS as e:
            raise ProfileError(f"Failed to deactivate profile: {e}")

    def reactivate_profile(self, profile_id: str) -> bool:
        """
        Reactivate a deactivated profile

        Args:
            profile_id: Profile to reactivate

        Returns:
            True if successful

        Raises:
            ProfileError: If reactivation fails
        """
        try:
            self.db.execute_write("UPDATE child_profiles SET is_active = 1 WHERE profile_id = ?", (profile_id,))
            self._invalidate_profile_cache(profile_id)
            return True
        except DB_ERRORS as e:
            raise ProfileError(f"Failed to reactivate profile: {e}")

    def delete_profile_permanently(self, profile_id: str) -> bool:
        """
        Permanently delete a profile (use with caution)

        Args:
            profile_id: Profile to delete

        Returns:
            True if successful

        Raises:
            ProfileError: If deletion fails
        """
        try:
            self.db.execute_write("DELETE FROM child_profiles WHERE profile_id = ?", (profile_id,))
            return True
        except DB_ERRORS as e:
            raise ProfileError(f"Failed to delete profile: {e}")

    # Alias for backwards compatibility
    def delete_profile(self, profile_id: str) -> bool:
        """Alias for delete_profile_permanently"""
        return self.delete_profile_permanently(profile_id)

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
                (parent_id,)
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

    def add_subject_preference(self, profile_id: str, subject: str) -> bool:
        """
        Add a subject preference for a profile

        Args:
            profile_id: Profile ID
            subject: Subject name

        Returns:
            True if successful

        Note:
            Uses profile_subjects table
        """
        try:
            # Check if already exists
            rows = self.db.execute_query(
                "SELECT id FROM profile_subjects WHERE profile_id = ? AND subject = ?",
                (profile_id, subject)
            )
            if rows:
                return True  # Already exists

            # Insert new preference with timestamp
            added_at = datetime.now(timezone.utc).isoformat()
            self.db.execute_write(
                "INSERT INTO profile_subjects (profile_id, subject, added_at) VALUES (?, ?, ?)",
                (profile_id, subject, added_at)
            )
            self._invalidate_profile_cache(profile_id)
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to add subject preference: {e}")
            return False

    def remove_subject_preference(self, profile_id: str, subject: str) -> bool:
        """
        Remove a subject preference for a profile

        Args:
            profile_id: Profile ID
            subject: Subject name to remove

        Returns:
            True if successful
        """
        try:
            self.db.execute_write(
                "DELETE FROM profile_subjects WHERE profile_id = ? AND subject = ?",
                (profile_id, subject)
            )
            self._invalidate_profile_cache(profile_id)
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to remove subject preference: {e}")
            return False

    def increment_session_count(self, profile_id: str) -> bool:
        """
        Increment the total session count for a profile

        Args:
            profile_id: Profile ID

        Returns:
            True if successful
        """
        try:
            self.db.execute_write(
                "UPDATE child_profiles SET total_sessions = total_sessions + 1 WHERE profile_id = ?",
                (profile_id,)
            )
            # Invalidate cache for this profile
            from utils.cache import cache
            cache.delete(f"child_profile:{profile_id}", namespace="snflwr")
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to increment session count: {e}")
            return False

    def increment_question_count(self, profile_id: str, count: int = 1) -> bool:
        """
        Increment the total question count for a profile

        Args:
            profile_id: Profile ID
            count: Number of questions to add (default 1)

        Returns:
            True if successful
        """
        try:
            self.db.execute_write(
                "UPDATE child_profiles SET total_questions = total_questions + ? WHERE profile_id = ?",
                (count, profile_id)
            )
            # Invalidate cache for this profile
            from utils.cache import cache
            cache.delete(f"child_profile:{profile_id}", namespace="snflwr")
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to increment question count: {e}")
            return False

    def update_last_active(self, profile_id: str) -> bool:
        """
        Update the last_active timestamp for a profile

        Args:
            profile_id: Profile ID

        Returns:
            True if successful
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute_write(
                "UPDATE child_profiles SET last_active = ? WHERE profile_id = ?",
                (now, profile_id)
            )
            # Invalidate cache so get_profile() returns the updated timestamp
            from utils.cache import cache
            cache.delete(f"child_profile:{profile_id}", namespace="snflwr")
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to update last_active: {e}")
            return False

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
                placeholders = ','.join('?' * len(profiles))
                query = f"SELECT SUM(duration_minutes) FROM sessions WHERE profile_id IN ({placeholders})"
                minutes_rows = self.db.execute_query(
                    query,
                    tuple(p.profile_id for p in profiles)
                )
                if minutes_rows:
                    row = minutes_rows[0]
                    # Safely extract value whether row is dict or tuple
                    if isinstance(row, dict):
                        total_minutes = row.get('SUM(duration_minutes)') or 0
                    elif row and len(row) > 0 and row[0]:
                        total_minutes = row[0]
            except DB_ERRORS as e:
                logger.debug(f"Failed to query total minutes (non-critical): {e}")

        active_profiles = len([p for p in profiles if p.is_active])

        return {
            'total_profiles': len(profiles),
            'active_profiles': active_profiles,
            'total_sessions': total_sessions,
            'total_questions': total_questions,
            'total_minutes': total_minutes,
            'profiles': profiles
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

    def get_profiles_by_age_range(self, parent_id: str, min_age: int, max_age: int) -> List[ChildProfile]:
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

    def _row_to_profile(self, row) -> Optional[ChildProfile]:
        """Helper to convert database row to ChildProfile"""
        def g(key, idx):
            try:
                return row[key]
            except (KeyError, IndexError, TypeError):
                try:
                    return row[idx]
                except (KeyError, IndexError, TypeError):
                    return None

        profile_id = g('profile_id', 0)

        # Get subjects for this profile
        subjects = []
        if profile_id:
            try:
                subject_rows = self.db.execute_query(
                    "SELECT subject FROM profile_subjects WHERE profile_id = ?",
                    (profile_id,)
                )
                subjects = [row[0] if isinstance(row, tuple) else row['subject'] for row in subject_rows]
            except DB_ERRORS as e:
                logger.debug(f"Failed to query subjects in _row_to_profile (non-critical): {e}")

        return ChildProfile(
            profile_id=profile_id,
            parent_id=g('parent_id', 1),
            name=g('name', 2) or g('name', 1),
            age=g('age', 3) or 0,
            grade=g('grade', 4) or g('grade_level', 4) or 'K',
            avatar=g('avatar', 5) or g('avatar_url', 5) or 'default',
            learning_level=g('learning_level', 7) or 'adaptive',
            daily_time_limit_minutes=g('daily_time_limit_minutes', 8) or 120,
            is_active=bool(g('is_active', 14)),
            total_sessions=g('total_sessions', 9) or 0,
            total_questions=g('total_questions', 10) or 0,
            last_active=g('last_active', 13),
            subjects_focus=subjects
        )

