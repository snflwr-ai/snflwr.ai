"""
Test Suite for Profile Management System
Tests child profile CRUD operations, age validation, learning preferences, and family management
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import shutil

from core.profile_manager import (
    ProfileManager,
    ChildProfile,
    ProfileError,
    ProfileNotFoundError,
    ProfileValidationError,
    PermissionDeniedError
)
from core.authentication import AuthenticationManager
from storage.database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    shutil.rmtree(temp_dir)


@pytest.fixture
def auth_manager(temp_db):
    """Create authentication manager"""
    usb_path = Path(tempfile.mkdtemp())
    auth = AuthenticationManager(temp_db, usb_path)
    yield auth
    shutil.rmtree(usb_path)


@pytest.fixture
def profile_manager(temp_db):
    """Create profile manager with test database"""
    return ProfileManager(temp_db)


@pytest.fixture
def test_parent(auth_manager):
    """Create test parent account"""
    success, parent_id = auth_manager.create_parent_account(
        "testparent", "SecurePass123!"
    )
    assert success, f"Failed to create test parent: {parent_id}"
    return parent_id


class TestProfileCreation:
    """Test child profile creation functionality"""

    def test_create_profile_with_defaults(self, profile_manager, test_parent):
        """Test successful profile creation and defaults"""
        profile = profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )

        assert profile is not None
        assert profile.name == "Emma"
        assert profile.age == 10
        assert profile.grade == "5th"
        assert profile.parent_id == test_parent
        assert len(profile.profile_id) == 32
        assert profile.avatar == "default"
        assert profile.learning_level == "adaptive"
        assert profile.daily_time_limit_minutes == 120
        assert profile.is_active is True
        assert profile.total_sessions == 0

    def test_create_profile_with_options(self, profile_manager, test_parent):
        """Test profile creation with custom avatar and learning level"""
        profile = profile_manager.create_profile(
            parent_id=test_parent, name="Sophie", age=12, grade="7th",
            avatar="robot", learning_level="advanced"
        )

        assert profile.avatar == "robot"
        assert profile.learning_level == "advanced"

    def test_create_profile_invalid_parent_id(self, profile_manager):
        """Test profile creation fails with invalid parent ID"""
        with pytest.raises(ProfileError):
            profile_manager.create_profile(
                parent_id="invalid_parent_id", name="Test", age=10, grade="5th"
            )


class TestAgeValidation:
    """Test age validation rules"""

    @pytest.mark.parametrize("age,should_pass", [
        (4, False), (5, True), (10, True), (18, True), (19, False),
    ])
    def test_age_boundaries(self, profile_manager, test_parent, age, should_pass):
        """Test age validation at boundaries"""
        if should_pass:
            profile = profile_manager.create_profile(
                parent_id=test_parent, name=f"Child{age}", age=age, grade="K"
            )
            assert profile.age == age
        else:
            with pytest.raises(ProfileValidationError) as exc_info:
                profile_manager.create_profile(
                    parent_id=test_parent, name=f"Child{age}", age=age, grade="K"
                )
            assert "age" in str(exc_info.value).lower()


class TestProfileRetrieval:
    """Test profile retrieval operations"""

    @pytest.fixture
    def test_profiles(self, profile_manager, test_parent):
        """Create multiple test profiles"""
        profiles = []
        for name, age, grade in [("Emma", 10, "5th"), ("Alex", 8, "3rd"), ("Sophie", 12, "7th")]:
            profile = profile_manager.create_profile(
                parent_id=test_parent, name=name, age=age, grade=grade
            )
            profiles.append(profile)
        return profiles

    def test_get_profile_by_id(self, profile_manager, test_profiles):
        """Test retrieving profile by ID"""
        original = test_profiles[0]
        retrieved = profile_manager.get_profile(original.profile_id)

        assert retrieved is not None
        assert retrieved.profile_id == original.profile_id
        assert retrieved.name == original.name

    def test_get_profile_nonexistent_id(self, profile_manager):
        """Test retrieving non-existent profile returns None"""
        assert profile_manager.get_profile("nonexistent_id") is None

    def test_get_all_profiles_for_parent(self, profile_manager, test_parent, test_profiles):
        """Test retrieving all profiles for a parent"""
        profiles = profile_manager.get_profiles_by_parent(test_parent)
        assert len(profiles) == 3
        assert {p.name for p in profiles} == {"Emma", "Alex", "Sophie"}

    def test_get_active_profiles_only(self, profile_manager, test_parent, test_profiles):
        """Test retrieving only active profiles excludes deactivated"""
        profile_manager.update_profile(test_profiles[1].profile_id, is_active=False)
        active = profile_manager.get_active_profiles(test_parent)
        assert len(active) == 2
        assert "Alex" not in {p.name for p in active}


class TestProfileUpdate:
    """Test profile update operations"""

    @pytest.fixture
    def test_profile(self, profile_manager, test_parent):
        """Create single test profile"""
        return profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )

    def test_update_multiple_fields(self, profile_manager, test_profile):
        """Test updating multiple fields at once"""
        success = profile_manager.update_profile(
            test_profile.profile_id,
            name="Emily", age=11, grade="6th", learning_level="advanced"
        )
        assert success is True

        updated = profile_manager.get_profile(test_profile.profile_id)
        assert updated.name == "Emily"
        assert updated.age == 11
        assert updated.grade == "6th"
        assert updated.learning_level == "advanced"

    def test_update_time_limit(self, profile_manager, test_profile):
        """Test updating daily time limit"""
        profile_manager.update_profile(test_profile.profile_id, daily_time_limit_minutes=180)
        updated = profile_manager.get_profile(test_profile.profile_id)
        assert updated.daily_time_limit_minutes == 180

    @pytest.mark.parametrize("field,value", [
        ("age", 25),
        ("learning_level", "invalid_level"),
    ])
    def test_update_invalid_values(self, profile_manager, test_profile, field, value):
        """Test update fails with invalid values"""
        with pytest.raises(ProfileValidationError):
            profile_manager.update_profile(test_profile.profile_id, **{field: value})


class TestProfileDeletion:
    """Test profile deletion and deactivation"""

    @pytest.fixture
    def test_profile(self, profile_manager, test_parent):
        """Create test profile"""
        return profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )

    def test_deactivate_and_reactivate(self, profile_manager, test_profile):
        """Test deactivating and reactivating profile"""
        assert profile_manager.deactivate_profile(test_profile.profile_id) is True
        assert profile_manager.get_profile(test_profile.profile_id).is_active is False

        assert profile_manager.reactivate_profile(test_profile.profile_id) is True
        assert profile_manager.get_profile(test_profile.profile_id).is_active is True

    def test_delete_profile_permanently(self, profile_manager, test_profile):
        """Test permanent profile deletion"""
        profile_id = test_profile.profile_id
        assert profile_manager.delete_profile(profile_id) is True
        assert profile_manager.get_profile(profile_id) is None


class TestSubjectPreferences:
    """Test subject preference management"""

    @pytest.fixture
    def test_profile(self, profile_manager, test_parent):
        """Create test profile"""
        return profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )

    def test_add_and_remove_subjects(self, profile_manager, test_profile):
        """Test adding and removing subject preferences"""
        for subject in ["mathematics", "biology", "chemistry"]:
            profile_manager.add_subject_preference(test_profile.profile_id, subject)

        updated = profile_manager.get_profile(test_profile.profile_id)
        assert {"mathematics", "biology", "chemistry"}.issubset(set(updated.subjects_focus))

        profile_manager.remove_subject_preference(test_profile.profile_id, "chemistry")
        updated = profile_manager.get_profile(test_profile.profile_id)
        assert "chemistry" not in updated.subjects_focus

    def test_duplicate_subject_ignored(self, profile_manager, test_profile):
        """Test adding duplicate subject is idempotent"""
        profile_manager.add_subject_preference(test_profile.profile_id, "mathematics")
        profile_manager.add_subject_preference(test_profile.profile_id, "mathematics")

        updated = profile_manager.get_profile(test_profile.profile_id)
        assert updated.subjects_focus.count("mathematics") == 1


class TestProfileStatistics:
    """Test profile statistics tracking"""

    @pytest.fixture
    def test_profile(self, profile_manager, test_parent):
        """Create test profile"""
        return profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )

    def test_increment_counts(self, profile_manager, test_profile):
        """Test incrementing session and question counts"""
        profile_manager.increment_session_count(test_profile.profile_id)
        profile_manager.increment_session_count(test_profile.profile_id)
        profile_manager.increment_question_count(test_profile.profile_id, 5)
        profile_manager.increment_question_count(test_profile.profile_id, 3)

        updated = profile_manager.get_profile(test_profile.profile_id)
        assert updated.total_sessions == 2
        assert updated.total_questions == 8

    def test_update_last_active(self, profile_manager, test_profile):
        """Test updating last active timestamp"""
        profile_manager.update_last_active(test_profile.profile_id)

        updated = profile_manager.get_profile(test_profile.profile_id)
        last_active = datetime.fromisoformat(updated.last_active)
        if last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=timezone.utc)
        assert (datetime.now(timezone.utc) - last_active).total_seconds() < 60


class TestFamilyManagement:
    """Test family-level profile management"""

    @pytest.fixture
    def family_profiles(self, profile_manager, test_parent):
        """Create family with multiple children"""
        profiles = []
        for name, age, grade in [("Emma", 10, "5th"), ("Alex", 8, "3rd"), ("Sophie", 12, "7th")]:
            profile = profile_manager.create_profile(
                parent_id=test_parent, name=name, age=age, grade=grade
            )
            profiles.append(profile)
        return profiles

    def test_family_statistics(self, profile_manager, test_parent, family_profiles):
        """Test getting statistics for entire family"""
        for profile in family_profiles:
            profile_manager.increment_session_count(profile.profile_id)
            profile_manager.increment_question_count(profile.profile_id, 10)

        stats = profile_manager.get_family_statistics(test_parent)
        assert stats['total_profiles'] == 3
        assert stats['total_sessions'] == 3
        assert stats['total_questions'] == 30

    def test_most_active_profile(self, profile_manager, test_parent, family_profiles):
        """Test identifying most active profile"""
        for _ in range(5):
            profile_manager.increment_session_count(family_profiles[0].profile_id)
        profile_manager.increment_session_count(family_profiles[1].profile_id)

        most_active = profile_manager.get_most_active_profile(test_parent)
        assert most_active.name == "Emma"

    def test_filter_by_age_range(self, profile_manager, test_parent, family_profiles):
        """Test filtering profiles by age range"""
        elementary = profile_manager.get_profiles_by_age_range(test_parent, min_age=5, max_age=10)
        assert len(elementary) == 2
        assert {p.name for p in elementary} == {"Emma", "Alex"}

    def test_unique_names_per_family(self, profile_manager, test_parent):
        """Test profile names must be unique within family"""
        profile_manager.create_profile(
            parent_id=test_parent, name="Emma", age=10, grade="5th"
        )
        with pytest.raises(ProfileValidationError) as exc_info:
            profile_manager.create_profile(
                parent_id=test_parent, name="Emma", age=8, grade="3rd"
            )
        assert "already exists" in str(exc_info.value).lower()


class TestPermissions:
    """Test profile permission and access control"""

    @pytest.fixture
    def two_parents(self, auth_manager):
        """Create two parent accounts"""
        _, parent1 = auth_manager.create_parent_account("parent1", "SecurePass123!")
        _, parent2 = auth_manager.create_parent_account("parent2", "SecurePass123!")
        return parent1, parent2

    def test_parent_isolation(self, profile_manager, two_parents):
        """Test parents can only see their own children"""
        parent1, parent2 = two_parents

        profile_manager.create_profile(parent_id=parent1, name="Emma", age=10, grade="5th")
        profile_manager.create_profile(parent_id=parent2, name="Alex", age=8, grade="3rd")

        assert len(profile_manager.get_profiles_by_parent(parent1)) == 1
        assert profile_manager.get_profiles_by_parent(parent1)[0].name == "Emma"
        assert len(profile_manager.get_profiles_by_parent(parent2)) == 1
        assert profile_manager.get_profiles_by_parent(parent2)[0].name == "Alex"

    def test_cross_parent_modification_denied(self, profile_manager, two_parents):
        """Test parent cannot modify another parent's child"""
        parent1, parent2 = two_parents

        profile = profile_manager.create_profile(
            parent_id=parent1, name="Emma", age=10, grade="5th"
        )

        with pytest.raises(PermissionDeniedError):
            profile_manager.update_profile_with_permission_check(
                parent_id=parent2, profile_id=profile.profile_id, name="Modified"
            )
