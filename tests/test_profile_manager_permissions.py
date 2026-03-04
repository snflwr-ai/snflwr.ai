"""
Tests for core/profile_manager.py — FERPA Permission Checks

Compliance-critical paths tested:
    - create_profile: parent validation, duplicate name check, age validation
    - update_profile_with_permission_check: ownership enforcement
    - update_profile: field validation (age, grade, learning_level)
    - deactivate_profile / reactivate_profile: soft delete lifecycle
    - get_profile / get_profiles_by_parent: data retrieval
    - get_family_statistics: parent-scoped aggregation
    - increment_session_count / increment_question_count: counter updates
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from core.profile_manager import (
    ChildProfile,
    PermissionDeniedError,
    ProfileError,
    ProfileManager,
    ProfileNotFoundError,
    ProfileValidationError,
)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mgr(mock_db):
    return ProfileManager(mock_db)


# --------------------------------------------------------------------------
# create_profile
# --------------------------------------------------------------------------

class TestCreateProfile:

    def test_valid_profile(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent1'}],  # parent exists
            [],  # no duplicate name
        ]
        mock_db.execute_write.return_value = None

        profile = mgr.create_profile("parent1", "Tommy", 10, "5th")
        assert profile.name == "Tommy"
        assert profile.age == 10
        assert profile.is_active is True

    def test_name_too_short(self, mgr):
        with pytest.raises(ProfileValidationError, match="at least 2"):
            mgr.create_profile("parent1", "T", 10, "5th")

    def test_empty_name(self, mgr):
        with pytest.raises(ProfileValidationError):
            mgr.create_profile("parent1", "", 10, "5th")

    def test_age_too_young(self, mgr):
        with pytest.raises(ProfileValidationError, match="between 5 and 18"):
            mgr.create_profile("parent1", "Tommy", 3, "K")

    def test_age_too_old(self, mgr):
        with pytest.raises(ProfileValidationError, match="between 5 and 18"):
            mgr.create_profile("parent1", "Tommy", 25, "12th")

    def test_invalid_parent_id(self, mgr, mock_db):
        mock_db.execute_query.return_value = []  # parent not found
        with pytest.raises(ProfileError, match="Invalid parent_id"):
            mgr.create_profile("bad_parent", "Tommy", 10, "5th")

    def test_duplicate_name_rejected(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent1'}],  # parent exists
            [{'name': 'Tommy'}],  # duplicate
        ]
        with pytest.raises(ProfileValidationError, match="already exists"):
            mgr.create_profile("parent1", "Tommy", 10, "5th")

    def test_db_error_on_insert(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent1'}],
            [],
        ]
        mock_db.execute_write.side_effect = sqlite3.Error("db fail")
        with pytest.raises(ProfileError, match="database write failed"):
            mgr.create_profile("parent1", "Tommy", 10, "5th")

    def test_foreign_key_violation(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent1'}],
            [],
        ]
        mock_db.execute_write.side_effect = sqlite3.IntegrityError("FOREIGN KEY constraint failed: parent")
        with pytest.raises(ProfileError, match="Invalid parent_id"):
            mgr.create_profile("parent1", "Tommy", 10, "5th")


# --------------------------------------------------------------------------
# update_profile_with_permission_check — FERPA ownership
# --------------------------------------------------------------------------

class TestUpdateWithPermissionCheck:

    def test_owner_can_update(self, mgr, mock_db):
        mock_db.execute_query.return_value = [{'parent_id': 'parent1'}]
        mock_db.execute_write.return_value = None

        with patch("utils.cache.cache"):
            result = mgr.update_profile_with_permission_check(
                "parent1", "prof1", name="New Name"
            )
        assert result is True

    def test_non_owner_denied(self, mgr, mock_db):
        mock_db.execute_query.return_value = [{'parent_id': 'parent1'}]

        with pytest.raises(PermissionDeniedError):
            mgr.update_profile_with_permission_check(
                "other_parent", "prof1", name="Hacked"
            )

    def test_profile_not_found(self, mgr, mock_db):
        mock_db.execute_query.return_value = []

        with pytest.raises(ProfileNotFoundError):
            mgr.update_profile_with_permission_check(
                "parent1", "missing", name="X"
            )

    def test_db_error_on_permission_check(self, mgr, mock_db):
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")

        with pytest.raises(ProfileError, match="Permission check failed"):
            mgr.update_profile_with_permission_check(
                "parent1", "prof1", name="X"
            )

    def test_tuple_row_format(self, mgr, mock_db):
        """Test with tuple rows (some DB drivers return tuples, not dicts)."""
        mock_db.execute_query.return_value = [('parent1',)]
        mock_db.execute_write.return_value = None

        with patch("utils.cache.cache"):
            result = mgr.update_profile_with_permission_check(
                "parent1", "prof1", name="New"
            )
        assert result is True


# --------------------------------------------------------------------------
# update_profile — Field Validation
# --------------------------------------------------------------------------

class TestUpdateProfile:

    def test_valid_update(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            result = mgr.update_profile("prof1", name="New Name", age=12)
        assert result is True

    def test_age_out_of_range(self, mgr):
        with pytest.raises(ProfileValidationError, match="between 5 and 18"):
            mgr.update_profile("prof1", age=3)

    def test_invalid_learning_level(self, mgr):
        with pytest.raises(ProfileValidationError, match="Learning level"):
            mgr.update_profile("prof1", learning_level="expert")

    def test_invalid_grade(self, mgr):
        with pytest.raises(ProfileValidationError, match="Grade"):
            mgr.update_profile("prof1", grade="13th")

    def test_invalid_time_limit(self, mgr):
        with pytest.raises(ProfileValidationError, match="time limit"):
            mgr.update_profile("prof1", daily_time_limit_minutes=2000)

    def test_negative_time_limit(self, mgr):
        with pytest.raises(ProfileValidationError, match="time limit"):
            mgr.update_profile("prof1", daily_time_limit_minutes=-1)

    def test_is_active_must_be_bool(self, mgr):
        with pytest.raises(ProfileValidationError, match="boolean"):
            mgr.update_profile("prof1", is_active="yes")

    def test_no_updates_returns_true(self, mgr):
        result = mgr.update_profile("prof1")
        assert result is True

    def test_unknown_field_ignored(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            result = mgr.update_profile("prof1", unknown_field="value")
        assert result is True
        # execute_write should not be called since no valid fields
        mock_db.execute_write.assert_not_called()

    def test_valid_grades_with_suffix(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            result = mgr.update_profile("prof1", grade="5th")
        assert result is True


# --------------------------------------------------------------------------
# deactivate / reactivate — Soft Delete Lifecycle
# --------------------------------------------------------------------------

class TestSoftDeleteLifecycle:

    def test_deactivate(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.deactivate_profile("prof1") is True
        query = mock_db.execute_write.call_args[0][0]
        assert "is_active = 0" in query

    def test_reactivate(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.reactivate_profile("prof1") is True
        query = mock_db.execute_write.call_args[0][0]
        assert "is_active = 1" in query

    def test_deactivate_db_error(self, mgr, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        with pytest.raises(ProfileError):
            mgr.deactivate_profile("prof1")

    def test_delete_permanently_exists(self, mgr, mock_db):
        """Permanent delete should still exist but should be used cautiously."""
        mock_db.execute_write.return_value = None
        assert mgr.delete_profile_permanently("prof1") is True
        query = mock_db.execute_write.call_args[0][0]
        assert "DELETE FROM" in query


# --------------------------------------------------------------------------
# get_profile / get_profiles_by_parent
# --------------------------------------------------------------------------

class TestGetProfile:

    def test_profile_found(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{  # profile row
                'profile_id': 'prof1', 'parent_id': 'p1', 'name': 'Tommy',
                'age': 10, 'grade': '5th', 'avatar': 'default',
                'learning_level': 'adaptive', 'daily_time_limit_minutes': 120,
                'is_active': 1, 'total_sessions': 5, 'total_questions': 50,
                'last_active': '2024-01-01',
            }],
            [],  # subjects
            [{'count': 5, 'questions': 50}],  # session stats
        ]
        mock_cache = MagicMock()
        mock_cache.get.return_value = None  # Cache miss — force function execution
        with patch("utils.cache.cache", mock_cache):
            profile = mgr.get_profile("prof1")
        assert profile.name == "Tommy"
        assert profile.age == 10

    def test_profile_not_found(self, mgr, mock_db):
        mock_db.execute_query.return_value = []
        mock_cache = MagicMock()
        mock_cache.get.return_value = None
        with patch("utils.cache.cache", mock_cache):
            profile = mgr.get_profile("missing")
        assert profile is None

    def test_get_profiles_by_parent(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{  # profile rows
                'profile_id': 'prof1', 'parent_id': 'p1', 'name': 'Tommy',
                'age': 10, 'grade': '5th', 'avatar': 'default',
                'learning_level': 'adaptive', 'daily_time_limit_minutes': 120,
                'is_active': 1, 'total_sessions': 0, 'total_questions': 0,
                'last_active': None,
            }],
            [],  # session stats bulk query
            [{'profile_id': 'prof1', 'total_sessions': 0, 'total_questions': 0}],  # counter columns
            [],  # subjects bulk query
        ]
        profiles = mgr.get_profiles_by_parent("p1")
        assert len(profiles) == 1
        assert profiles[0].name == "Tommy"

    def test_get_profiles_empty(self, mgr, mock_db):
        mock_db.execute_query.return_value = []
        profiles = mgr.get_profiles_by_parent("p1")
        assert profiles == []


# --------------------------------------------------------------------------
# Family statistics
# --------------------------------------------------------------------------

class TestFamilyStatistics:

    def test_family_stats(self, mgr, mock_db):
        mock_db.execute_query.side_effect = [
            [{  # one profile
                'profile_id': 'prof1', 'parent_id': 'p1', 'name': 'Tommy',
                'age': 10, 'grade': '5th', 'avatar': 'default',
                'learning_level': 'adaptive', 'daily_time_limit_minutes': 120,
                'is_active': 1, 'total_sessions': 0, 'total_questions': 0,
                'last_active': None,
            }],
            [{'profile_id': 'prof1', 'count': 3, 'questions': 15}],  # session stats
            [],  # subjects
            [{'SUM(duration_minutes)': 45}],  # total minutes
        ]
        stats = mgr.get_family_statistics("p1")
        assert stats['total_profiles'] == 1
        assert stats['active_profiles'] == 1

    def test_family_stats_no_profiles(self, mgr, mock_db):
        mock_db.execute_query.return_value = []
        stats = mgr.get_family_statistics("p1")
        assert stats['total_profiles'] == 0


# --------------------------------------------------------------------------
# Counter increments
# --------------------------------------------------------------------------

class TestCounterIncrements:

    def test_increment_session(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.increment_session_count("prof1") is True
        assert "total_sessions = total_sessions + 1" in mock_db.execute_write.call_args[0][0]

    def test_increment_questions(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.increment_question_count("prof1", count=5) is True

    def test_increment_db_error(self, mgr, mock_db):
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        assert mgr.increment_session_count("prof1") is False

    def test_update_last_active(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        assert mgr.update_last_active("prof1") is True


# --------------------------------------------------------------------------
# Subject preferences
# --------------------------------------------------------------------------

class TestSubjectPreferences:

    def test_add_subject(self, mgr, mock_db):
        mock_db.execute_query.return_value = []  # not already there
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.add_subject_preference("prof1", "math") is True

    def test_add_duplicate_subject(self, mgr, mock_db):
        mock_db.execute_query.return_value = [{'id': 1}]  # already there
        assert mgr.add_subject_preference("prof1", "math") is True
        mock_db.execute_write.assert_not_called()

    def test_remove_subject(self, mgr, mock_db):
        mock_db.execute_write.return_value = None
        with patch("utils.cache.cache"):
            assert mgr.remove_subject_preference("prof1", "math") is True
