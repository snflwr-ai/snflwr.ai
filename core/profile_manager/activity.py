"""Activity/counter methods for ProfileManager (mixin). Extracted verbatim."""

from datetime import datetime, timezone

from storage.db_adapters import DB_ERRORS
from utils.logger import get_logger

logger = get_logger(__name__)


class _ProfileActivityMixin:
    """Subject prefs + session/question counters (composed in __init__.py)."""

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
                (profile_id, subject),
            )
            if rows:
                return True  # Already exists

            # Insert new preference with timestamp
            added_at = datetime.now(timezone.utc).isoformat()
            self.db.execute_write(
                "INSERT INTO profile_subjects (profile_id, subject, added_at) VALUES (?, ?, ?)",
                (profile_id, subject, added_at),
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
                (profile_id, subject),
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
                (profile_id,),
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
                (count, profile_id),
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
                (now, profile_id),
            )
            # Invalidate cache so get_profile() returns the updated timestamp
            from utils.cache import cache

            cache.delete(f"child_profile:{profile_id}", namespace="snflwr")
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to update last_active: {e}")
            return False
