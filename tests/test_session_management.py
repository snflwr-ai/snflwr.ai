"""
Test Suite for Session Management System
Tests session creation, timeouts, concurrent limits, tracking, and session recovery
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import shutil
import time

from core.session_manager import (
    SessionManager,
    Session,
    SessionError,
    SessionLimitError,
    SessionTimeoutError,
    session_manager
)
from core.authentication import AuthenticationManager
from core.profile_manager import ProfileManager
from storage.database import DatabaseManager
from config import SESSION_CONFIG


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    db.close()
    shutil.rmtree(temp_dir)


@pytest.fixture
def session_manager_fixture(temp_db):
    """Create session manager with test database"""
    return SessionManager(temp_db)


@pytest.fixture
def auth_manager(temp_db):
    """Create authentication manager"""
    usb_path = Path(tempfile.mkdtemp())
    auth = AuthenticationManager(temp_db, usb_path)
    yield auth
    shutil.rmtree(usb_path)


@pytest.fixture
def profile_manager_fixture(temp_db):
    """Create profile manager"""
    return ProfileManager(temp_db)


@pytest.fixture
def test_profile(auth_manager, profile_manager_fixture):
    """Create test parent and child profile"""
    success, parent_id = auth_manager.create_parent_account("testparent", "SecurePass123!")
    assert success, f"Failed to create parent: {parent_id}"

    profile = profile_manager_fixture.create_profile(
        parent_id=parent_id, name="Emma", age=10, grade="5th"
    )
    return profile


class TestSessionCreation:
    """Test session creation functionality"""

    @pytest.mark.parametrize("session_type", ["student", "parent", "educator"])
    def test_create_session_types(self, session_manager_fixture, test_profile, session_type):
        """Test creating sessions of each type"""
        kwargs = {"session_type": session_type}
        if session_type == "student":
            kwargs["profile_id"] = test_profile.profile_id
        else:
            kwargs["parent_id"] = test_profile.parent_id
            if session_type == "parent":
                kwargs["profile_id"] = None

        session = session_manager_fixture.create_session(**kwargs)

        assert session is not None
        assert session.session_type == session_type
        assert session.session_id is not None
        assert len(session.session_id) == 32

    def test_session_persisted_to_database(self, session_manager_fixture, test_profile):
        """Test session is saved and retrievable"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        retrieved = session_manager_fixture.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_session(self, session_manager_fixture):
        """Test retrieving non-existent session returns None"""
        assert session_manager_fixture.get_session("nonexistent_id") is None


class TestSessionEnding:
    """Test session ending functionality"""

    def test_end_session(self, session_manager_fixture, test_profile):
        """Test ending an active session calculates duration"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        time.sleep(0.1)
        assert session_manager_fixture.end_session(session.session_id) is True

        ended = session_manager_fixture.get_session(session.session_id)
        assert ended.ended_at is not None
        assert ended.is_active is False
        assert ended.duration_minutes >= 0

    def test_end_nonexistent_session(self, session_manager_fixture):
        """Test ending non-existent session returns False"""
        assert session_manager_fixture.end_session("nonexistent_id") is False

    def test_end_already_ended_session_is_idempotent(self, session_manager_fixture, test_profile):
        """Test ending already-ended session still succeeds"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )
        session_manager_fixture.end_session(session.session_id)
        assert session_manager_fixture.end_session(session.session_id) is True


class TestSessionTimeouts:
    """Test session timeout functionality"""

    def test_idle_timeout_detection(self, session_manager_fixture, test_profile):
        """Test detecting idle timeout"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        idle_minutes = SESSION_CONFIG['idle_timeout_minutes']
        past_time = datetime.now() - timedelta(minutes=idle_minutes + 1)
        session_manager_fixture._update_last_activity(session.session_id, past_time.isoformat())

        assert session_manager_fixture.is_session_timed_out(session.session_id) is True

    def test_max_duration_timeout_detection(self, session_manager_fixture, test_profile):
        """Test detecting max duration timeout"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        max_hours = SESSION_CONFIG['max_session_hours']
        past_time = datetime.now() - timedelta(hours=max_hours + 1)
        session_manager_fixture._update_session_start(session.session_id, past_time.isoformat())

        assert session_manager_fixture.is_session_timed_out(session.session_id) is True

    def test_update_activity_prevents_timeout(self, session_manager_fixture, test_profile):
        """Test updating activity prevents timeout"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )
        session_manager_fixture.update_activity(session.session_id)
        assert session_manager_fixture.is_session_timed_out(session.session_id) is False

    def test_cleanup_timed_out_sessions(self, session_manager_fixture, test_profile):
        """Test automatic cleanup of timed out sessions"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        past_time = datetime.now() - timedelta(hours=5)
        session_manager_fixture._update_session_start(session.session_id, past_time.isoformat())

        cleaned = session_manager_fixture.cleanup_timed_out_sessions()
        assert cleaned > 0

        ended = session_manager_fixture.get_session(session.session_id)
        assert ended.is_active is False


class TestConcurrentSessionLimits:
    """Test concurrent session limits"""

    def test_daily_session_limit_enforced(self, session_manager_fixture, test_profile):
        """Test daily session limit is enforced"""
        daily_limit = SESSION_CONFIG['max_sessions_per_day']

        for i in range(daily_limit):
            session = session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)

        with pytest.raises(SessionLimitError):
            session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )

    def test_one_active_session_per_profile(self, session_manager_fixture, test_profile):
        """Test only one active session per profile"""
        session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        with pytest.raises(SessionLimitError):
            session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )

    def test_new_session_after_ending_previous(self, session_manager_fixture, test_profile):
        """Test new session can be created after ending previous"""
        session1 = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )
        session_manager_fixture.end_session(session1.session_id)

        session2 = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )
        assert session2.session_id != session1.session_id


class TestSessionTracking:
    """Test session activity tracking"""

    def test_track_question_count(self, session_manager_fixture, test_profile):
        """Test tracking questions asked in session"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        for _ in range(3):
            session_manager_fixture.increment_question_count(session.session_id)

        updated = session_manager_fixture.get_session(session.session_id)
        assert updated.questions_asked == 3

    def test_get_total_session_time_today(self, session_manager_fixture, test_profile):
        """Test getting total session time for today"""
        for minutes in [30, 45, 20]:
            session = session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, minutes)

        total = session_manager_fixture.get_total_session_time_today(test_profile.profile_id)
        assert total == 95


class TestDailyTimeLimit:
    """Test daily time limit enforcement"""

    def test_check_daily_time_limit(self, session_manager_fixture, test_profile):
        """Test daily time limit check with available time"""
        can_start, remaining = session_manager_fixture.check_daily_time_limit(
            test_profile.profile_id
        )
        assert can_start is True
        assert remaining == 120

    def test_daily_time_limit_exceeded(self, session_manager_fixture, test_profile):
        """Test daily time limit prevents new session"""
        for i in range(3):
            session = session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 50)

        can_start, remaining = session_manager_fixture.check_daily_time_limit(
            test_profile.profile_id
        )
        assert can_start is False
        assert remaining <= 0


class TestSessionStatistics:
    """Test session statistics and analytics"""

    def test_get_session_statistics(self, session_manager_fixture, test_profile):
        """Test retrieving session statistics"""
        for i in range(3):
            session = session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )
            for _ in range(5):
                session_manager_fixture.increment_question_count(session.session_id)
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 30)

        stats = session_manager_fixture.get_profile_statistics(test_profile.profile_id)

        assert stats['total_sessions'] == 3
        assert stats['total_questions'] == 15
        assert stats['total_minutes'] == 90
        assert stats['average_session_minutes'] == 30

    def test_get_session_history_ordered(self, session_manager_fixture, test_profile):
        """Test session history is reverse chronological"""
        for i in range(5):
            session = session_manager_fixture.create_session(
                profile_id=test_profile.profile_id, session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)

        history = session_manager_fixture.get_session_history(test_profile.profile_id, limit=3)
        assert len(history) == 3
        assert history[0].started_at > history[1].started_at


class TestSessionRecovery:
    """Test session recovery and crash handling"""

    def test_recover_orphaned_sessions(self, session_manager_fixture, test_profile):
        """Test recovering sessions not properly closed"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        past_time = datetime.now() - timedelta(hours=10)
        session_manager_fixture._update_session_start(session.session_id, past_time.isoformat())

        recovered = session_manager_fixture.recover_orphaned_sessions()
        assert recovered > 0

        recovered_session = session_manager_fixture.get_session(session.session_id)
        assert recovered_session.is_active is False

    def test_resume_session_after_restart(self, session_manager_fixture, test_profile):
        """Test resuming an active session after app restart"""
        session = session_manager_fixture.create_session(
            profile_id=test_profile.profile_id, session_type="student"
        )

        new_manager = SessionManager(session_manager_fixture.db)
        resumed = new_manager.get_session(session.session_id)

        assert resumed is not None
        assert resumed.is_active is True


class TestMultipleProfileSessions:
    """Test sessions across multiple profiles"""

    @pytest.fixture
    def multiple_profiles(self, auth_manager, profile_manager_fixture):
        """Create multiple profiles"""
        success, parent_id = auth_manager.create_parent_account("parent", "SecurePass123!")
        assert success

        profiles = []
        for name, age in [("Emma", 10), ("Alex", 8), ("Sophie", 12)]:
            profile = profile_manager_fixture.create_profile(
                parent_id=parent_id, name=name, age=age, grade=f"{age-5}th"
            )
            profiles.append(profile)
        return profiles

    def test_concurrent_sessions_different_profiles(self, session_manager_fixture, multiple_profiles):
        """Test concurrent sessions for different profiles"""
        sessions = []
        for profile in multiple_profiles:
            session = session_manager_fixture.create_session(
                profile_id=profile.profile_id, session_type="student"
            )
            sessions.append(session)

        assert len(sessions) == 3
        for session in sessions:
            assert session.is_active is True

    def test_get_all_active_sessions(self, session_manager_fixture, multiple_profiles):
        """Test getting all active sessions"""
        for profile in multiple_profiles:
            session_manager_fixture.create_session(
                profile_id=profile.profile_id, session_type="student"
            )

        active = session_manager_fixture.get_all_active_sessions()
        assert len(active) >= 3
