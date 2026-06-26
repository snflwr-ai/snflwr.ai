# core/profile_manager/__init__.py  (was profile_manager.py — decomposed)
"""
Profile manager for tests.

Provides a small, DB-backed implementation used by unit tests. Stores child
profiles in the `child_profiles` table (schema is created by
`storage.database.DatabaseManager.initialize_database`).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from storage.db_adapters import DB_ERRORS
from utils.cache import cached
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

from core.profile_manager.activity import _ProfileActivityMixin
from core.profile_manager.models import (
    ChildProfile,
    PermissionDeniedError,
    ProfileError,
    ProfileNotFoundError,
    ProfileValidationError,
)
from core.profile_manager.queries import _ProfileQueryMixin


class ProfileManager(_ProfileQueryMixin, _ProfileActivityMixin):
    """DB-backed profile manager. Read methods live in _ProfileQueryMixin,
    activity/counters in _ProfileActivityMixin; CRUD + _row_to_profile here.
    """

    def __init__(self, db_manager):
        self.db = db_manager

    def _invalidate_profile_cache(self, profile_id: str):
        """Invalidate the cached get_profile result for a given profile."""
        from utils.cache import cache

        cache.delete(f"child_profile:{profile_id}", namespace="snflwr")

    def create_profile(
        self,
        parent_id: str,
        name: str,
        age: int,
        grade: str,
        avatar: str = "default",
        learning_level: str = "adaptive",
        daily_time_limit_minutes: int = 120,
    ) -> ChildProfile:
        # Basic validation
        if not name or len(name) < 2:
            raise ProfileValidationError("Name must be at least 2 characters")
        if age < 5 or age > 18:
            raise ProfileValidationError("Age must be between 5 and 18 for K-12")

        # Verify parent exists (if table present)
        try:
            rows = self.db.execute_query(
                "SELECT parent_id FROM accounts WHERE parent_id = ?", (parent_id,)
            )
            if not rows:
                raise ProfileError("Invalid parent_id")
        except ProfileError:
            raise
        except DB_ERRORS as e:
            # Table may not exist in test environments — log it so we know
            logger.warning(
                f"Could not verify parent_id {sanitize_log_value(parent_id)!r} (table may not exist): {e}"
            )

        # Check for duplicate name within family
        try:
            existing = self.db.execute_query(
                "SELECT name FROM child_profiles WHERE parent_id = ? AND name = ? AND is_active = 1",
                (parent_id, name),
            )
            if existing:
                raise ProfileValidationError(
                    f"Profile with name '{name}' already exists for this parent"
                )
        except ProfileValidationError:
            raise
        except DB_ERRORS as e:
            logger.debug(f"Duplicate name check skipped (non-critical): {e}")

        profile_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()

        # Insert into DB if possible
        try:
            self.db.execute_write(
                "INSERT INTO child_profiles (profile_id, parent_id, name, age, grade, grade_level, tier, model_role, created_at, avatar, learning_level, daily_time_limit_minutes, is_active, total_sessions, total_questions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    profile_id,
                    parent_id,
                    name,
                    age,
                    grade,
                    grade,
                    "standard",
                    "student",
                    created_at,
                    avatar,
                    learning_level,
                    daily_time_limit_minutes,
                    1,
                    0,
                    0,
                ),
            )
        except DB_ERRORS as e:
            error_msg = str(e).lower()
            if "foreign key" in error_msg or (
                "constraint" in error_msg and "parent" in error_msg
            ):
                raise ProfileError(f"Invalid parent_id: {parent_id}")
            logger.error(f"Failed to write profile {profile_id} to database: {e}")
            raise ProfileError("Could not create profile: database write failed") from e

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
            subjects_focus=[],
        )

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
        if "age" in kwargs:
            age = kwargs["age"]
            if age < 5 or age > 18:
                raise ProfileValidationError("Age must be between 5 and 18 for K-12")

        if "learning_level" in kwargs:
            level = kwargs["learning_level"]
            valid_levels = ["beginner", "advanced", "adaptive"]
            if level not in valid_levels:
                raise ProfileValidationError(
                    f"Learning level must be one of: {', '.join(valid_levels)}"
                )

        if "name" in kwargs:
            name = kwargs["name"]
            if not name or len(name) < 2:
                raise ProfileValidationError("Name must be at least 2 characters")

        if "grade" in kwargs:
            grade = kwargs["grade"]
            valid_grades = [
                "K",
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
                "11",
                "12",
            ]
            # Also accept grades with suffixes like "1st", "2nd", "6th", etc.
            valid_grades_with_suffix = valid_grades + [
                "1st",
                "2nd",
                "3rd",
                "4th",
                "5th",
                "6th",
                "7th",
                "8th",
                "9th",
                "10th",
                "11th",
                "12th",
            ]
            if grade not in valid_grades_with_suffix:
                raise ProfileValidationError(
                    f"Grade must be one of: {', '.join(valid_grades)}"
                )

        if "daily_time_limit_minutes" in kwargs:
            limit = kwargs["daily_time_limit_minutes"]
            if not isinstance(limit, int) or limit < 0 or limit > 1440:
                raise ProfileValidationError(
                    "Daily time limit must be between 0 and 1440 minutes (24 hours)"
                )

        if "is_active" in kwargs:
            is_active = kwargs["is_active"]
            if not isinstance(is_active, bool):
                raise ProfileValidationError("is_active must be a boolean")

        updates = []
        params = []
        allowed = [
            "name",
            "age",
            "grade",
            "grade_level",
            "learning_level",
            "daily_time_limit_minutes",
            "is_active",
        ]
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

    def update_profile_with_permission_check(
        self, parent_id: str, profile_id: str, **kwargs
    ) -> bool:
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
                (profile_id,),
            )
            if not rows:
                raise ProfileNotFoundError(f"Profile not found: {profile_id}")

            row = rows[0]
            owner_parent_id = row[0] if isinstance(row, tuple) else row["parent_id"]

            if owner_parent_id != parent_id:
                raise PermissionDeniedError(
                    f"Parent {parent_id} does not have permission to modify profile {profile_id}"
                )

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
            self.db.execute_write(
                "UPDATE child_profiles SET is_active = 0 WHERE profile_id = ?",
                (profile_id,),
            )
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
            self.db.execute_write(
                "UPDATE child_profiles SET is_active = 1 WHERE profile_id = ?",
                (profile_id,),
            )
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
            self.db.execute_write(
                "DELETE FROM child_profiles WHERE profile_id = ?", (profile_id,)
            )
            return True
        except DB_ERRORS as e:
            raise ProfileError(f"Failed to delete profile: {e}")

    # Alias for backwards compatibility
    def delete_profile(self, profile_id: str) -> bool:
        """Alias for delete_profile_permanently"""
        return self.delete_profile_permanently(profile_id)

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

        profile_id = g("profile_id", 0)

        # Get subjects for this profile
        subjects = []
        if profile_id:
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
                    f"Failed to query subjects in _row_to_profile (non-critical): {e}"
                )

        return ChildProfile(
            profile_id=profile_id,
            parent_id=g("parent_id", 1),
            name=g("name", 2) or g("name", 1),
            age=g("age", 3) or 0,
            grade=g("grade", 4) or g("grade_level", 4) or "K",
            avatar=g("avatar", 5) or g("avatar_url", 5) or "default",
            learning_level=g("learning_level", 7) or "adaptive",
            daily_time_limit_minutes=g("daily_time_limit_minutes", 8) or 120,
            is_active=bool(g("is_active", 14)),
            total_sessions=g("total_sessions", 9) or 0,
            total_questions=g("total_questions", 10) or 0,
            last_active=g("last_active", 13),
            subjects_focus=subjects,
        )
