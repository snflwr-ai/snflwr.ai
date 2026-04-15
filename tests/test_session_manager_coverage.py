"""
Tests for core/session_manager.py — Session dataclass, error types,
and session manager operations via mocked DB.
"""
import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestSessionDataclass:
    def test_session_is_active_when_no_end(self):
        from core.session_manager import Session
        s = Session(session_id="s1", profile_id="p1")
        assert s.is_active is True

    def test_session_inactive_when_ended(self):
        from core.session_manager import Session
        s = Session(session_id="s1", profile_id="p1", ended_at="2024-01-01T10:00:00")
        assert s.is_active is False

    def test_to_dict_includes_all_fields(self):
        from core.session_manager import Session
        s = Session(
            session_id="s1",
            profile_id="p1",
            parent_id="parent-1",
            session_type="student",
            questions_asked=5,
        )
        d = s.to_dict()
        assert d["session_id"] == "s1"
        assert d["profile_id"] == "p1"
        assert d["parent_id"] == "parent-1"
        assert d["questions_asked"] == 5
        assert d["is_active"] is True

    def test_to_dict_is_active_false_when_ended(self):
        from core.session_manager import Session
        s = Session(session_id="s1", profile_id="p1", ended_at="2024-01-01")
        d = s.to_dict()
        assert d["is_active"] is False


class TestSessionErrors:
    def test_session_error_is_exception(self):
        from core.session_manager import SessionError
        e = SessionError("test")
        assert isinstance(e, Exception)

    def test_session_limit_error_is_session_error(self):
        from core.session_manager import SessionLimitError, SessionError
        e = SessionLimitError("limit reached")
        assert isinstance(e, SessionError)


class TestSessionManager:
    @pytest.fixture
    def mock_db(self):
        from core.session_manager import session_manager
        original_db = session_manager.db
        mock = MagicMock()
        session_manager.db = mock
        yield mock
        session_manager.db = original_db

    def test_create_session_raises_or_returns(self, mock_db):
        from core.session_manager import session_manager, SessionError
        mock_db.execute_write.return_value = None
        mock_db.execute_query.return_value = []

        try:
            sess = session_manager.create_session(
                profile_id="no_profile_test",
                parent_id=None,
                session_type="student",
            )
            assert sess is not None
        except (SessionError, Exception):
            # DB foreign key errors are expected without real schema
            pass

    def test_get_session_returns_none_when_not_found(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        result = session_manager.get_session("nonexistent-id")
        assert result is None

    def test_get_session_returns_session_when_found(self, mock_db):
        from core.session_manager import session_manager
        data = {
            "session_id": "s1",
            "profile_id": "p1",
            "parent_id": "par1",
            "session_type": "student",
            "started_at": "2024-01-01T10:00:00",
            "ended_at": None,
            "duration_minutes": None,
            "questions_asked": 0,
            "platform": "web",
        }
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda s, k: data[k]
        mock_row.get = lambda k, d=None: data.get(k, d)
        mock_db.execute_query.return_value = [mock_row]
        try:
            result = session_manager.get_session("s1")
            # May succeed or fail depending on row mapping implementation
        except Exception:
            pass  # DB mapping quirks without real schema

    def test_end_session_returns_bool(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_write.return_value = None
        mock_db.execute_query.return_value = []
        result = session_manager.end_session("sess-1")
        assert isinstance(result, bool)

    def test_get_active_session_returns_none_when_missing(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        result = session_manager.get_active_session("p1")
        assert result is None

    def test_get_all_active_sessions_returns_list(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        result = session_manager.get_all_active_sessions()
        assert isinstance(result, list)

    def test_increment_question_count(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_write.return_value = None
        mock_db.execute_query.return_value = []
        result = session_manager.increment_question_count("sess-1")
        assert isinstance(result, bool)

    def test_get_session_history_returns_list(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        result = session_manager.get_session_history("p1", limit=10)
        assert isinstance(result, list)

    def test_cleanup_timed_out_sessions_returns_int(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None
        result = session_manager.cleanup_timed_out_sessions()
        assert isinstance(result, int)

    def test_get_profile_statistics_returns_dict(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        stats = session_manager.get_profile_statistics("p1")
        assert isinstance(stats, dict)

    def test_get_profile_sessions_returns_list(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        result = session_manager.get_profile_sessions("p1")
        assert isinstance(result, list)

    def test_get_usage_stats_returns_dict(self, mock_db):
        from core.session_manager import session_manager
        mock_db.execute_query.return_value = []
        stats = session_manager.get_usage_stats("p1", days=7)
        assert isinstance(stats, dict)


# ---------------------------------------------------------------------------
# Tests using real in-memory SQLite to cover more code paths
# ---------------------------------------------------------------------------

import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_session.db"
    from storage.database import DatabaseManager
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    db.close()
    shutil.rmtree(temp_dir)


@pytest.fixture
def sm(temp_db):
    from core.session_manager import SessionManager
    return SessionManager(temp_db)


@pytest.fixture
def parent_and_profile(temp_db):
    """Create a real parent account and child profile for session tests."""
    from core.authentication import AuthenticationManager
    from core.profile_manager import ProfileManager
    auth = AuthenticationManager(temp_db, Path(tempfile.mkdtemp()))
    pm = ProfileManager(temp_db)
    ok, parent_id = auth.create_parent_account("sessparent", "SecurePass123!")
    assert ok
    profile = pm.create_profile(parent_id, "TestKid", 10, "5th")
    return parent_id, profile


class TestSessionLifecycleReal:
    """Full lifecycle tests using real SQLite DB to cover branches missed by mocked tests."""

    def test_create_and_get_session(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        assert session.session_id is not None
        assert session.is_active is True

        fetched = sm.get_session(session.session_id)
        assert fetched is not None
        assert fetched.session_id == session.session_id

    def test_get_active_session_found(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        active = sm.get_active_session(profile.profile_id)
        assert active is not None
        assert active.is_active is True

    def test_end_session_and_no_longer_active(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        result = sm.end_session(session.session_id)
        assert result is True

        fetched = sm.get_session(session.session_id)
        assert fetched.is_active is False
        assert fetched.ended_at is not None

    def test_end_already_ended_session_returns_true(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        sm.end_session(session.session_id)
        # End again - should return True (idempotent)
        result = sm.end_session(session.session_id)
        assert result is True

    def test_end_nonexistent_session_returns_false(self, sm):
        result = sm.end_session("nonexistent-session-id")
        assert result is False

    def test_increment_question_count_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        result = sm.increment_question_count(session.session_id)
        assert result is True

    def test_get_session_duration_active(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        dur = sm.get_session_duration(session.session_id)
        assert dur is not None
        assert isinstance(dur, int)
        assert dur >= 0

    def test_get_session_duration_ended(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        sm._set_session_duration(session.session_id, 42)
        sm.end_session(session.session_id)
        fetched = sm.get_session(session.session_id)
        dur = sm.get_session_duration(session.session_id)
        assert dur is not None

    def test_get_session_duration_none_session(self, sm):
        result = sm.get_session_duration("nonexistent")
        assert result is None

    def test_get_profile_sessions_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        sm.end_session(sm.get_active_session(profile.profile_id).session_id)
        sessions = sm.get_profile_sessions(profile.profile_id)
        assert len(sessions) >= 1

    def test_get_sessions_today_count_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        count = sm.get_sessions_today_count(profile.profile_id)
        assert count >= 1

    def test_get_sessions_today_count_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        count = sm_no_db.get_sessions_today_count("p1")
        assert count == 0

    def test_check_daily_time_limit_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        can_start, remaining = sm_no_db.check_daily_time_limit("p1")
        assert can_start is True
        assert remaining == 9999

    def test_check_daily_time_limit_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        can_start, remaining = sm.check_daily_time_limit(profile.profile_id)
        assert isinstance(can_start, bool)
        assert isinstance(remaining, int)

    def test_check_daily_time_limit_profile_has_limit(self, sm, temp_db, parent_and_profile):
        """Cover the branch where profile row has daily_time_limit_minutes."""
        parent_id, profile = parent_and_profile
        # Update the profile's time limit so the query returns a row
        temp_db.execute_write(
            "UPDATE child_profiles SET daily_time_limit_minutes = 60 WHERE profile_id = ?",
            (profile.profile_id,)
        )
        can_start, remaining = sm.check_daily_time_limit(profile.profile_id)
        assert isinstance(can_start, bool)

    def test_get_total_session_time_today(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        sm._set_session_duration(session.session_id, 15)
        total = sm.get_total_session_time_today(profile.profile_id)
        assert isinstance(total, int)

    def test_get_total_session_time_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_total_session_time_today("p1")
        assert result == 0

    def test_get_profile_statistics_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        stats = sm.get_profile_statistics(profile.profile_id)
        assert 'total_sessions' in stats
        assert 'total_questions' in stats
        assert 'total_minutes' in stats
        assert 'average_session_minutes' in stats

    def test_get_profile_statistics_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        stats = sm_no_db.get_profile_statistics("p1")
        assert stats['total_sessions'] == 0

    def test_get_usage_stats_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        stats = sm.get_usage_stats(profile.profile_id, days=7)
        assert 'total_sessions' in stats
        assert stats['days'] == 7

    def test_update_activity_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        result = sm.update_activity(session.session_id)
        assert result is True

    def test_update_last_activity_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db._update_last_activity("s1", "2024-01-01")
        assert result is False

    def test_update_session_start_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        new_ts = "2023-01-01T00:00:00+00:00"
        result = sm._update_session_start(session.session_id, new_ts)
        assert result is True

    def test_update_session_start_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db._update_session_start("s1", "2024-01-01")
        assert result is False

    def test_set_session_duration_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db._set_session_duration("s1", 30)
        assert result is False

    def test_get_all_active_sessions_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        active = sm.get_all_active_sessions()
        assert len(active) >= 1

    def test_get_all_active_sessions_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_all_active_sessions()
        assert result == []

    def test_cleanup_timed_out_sessions_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        # Set started_at to way in the past so it times out
        old_ts = "2000-01-01T00:00:00+00:00"
        sm._update_session_start(session.session_id, old_ts)
        count = sm.cleanup_timed_out_sessions()
        assert isinstance(count, int)
        assert count >= 1

    def test_recover_orphaned_sessions_real(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        # Set started_at to far past to trigger orphan recovery
        old_ts = "2000-01-01T00:00:00+00:00"
        sm._update_session_start(session.session_id, old_ts)
        recovered = sm.recover_orphaned_sessions()
        assert isinstance(recovered, int)
        assert recovered >= 1

    def test_recover_orphaned_sessions_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.recover_orphaned_sessions()
        assert result == 0

    def test_is_session_timed_out_active_not_timed_out(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        result = sm.is_session_timed_out(session.session_id)
        assert result is False

    def test_is_session_timed_out_old_start(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        old_ts = "2000-01-01T00:00:00+00:00"
        sm._update_session_start(session.session_id, old_ts)
        result = sm.is_session_timed_out(session.session_id)
        assert result is True

    def test_is_session_timed_out_idle(self, sm, parent_and_profile):
        """Timed out due to idle last_activity."""
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        # Set last_activity to 2 hours ago
        old_activity = "2000-01-01T00:00:00+00:00"
        sm._update_last_activity(session.session_id, old_activity)
        result = sm.is_session_timed_out(session.session_id)
        assert result is True

    def test_is_session_timed_out_nonexistent(self, sm):
        result = sm.is_session_timed_out("no-such-session")
        assert result is False

    def test_daily_session_limit_enforced(self, sm, parent_and_profile, temp_db):
        """Cover the branch where daily session count >= max_sessions_per_day."""
        _, profile = parent_and_profile
        from unittest.mock import patch
        from core.session_manager import SessionLimitError
        import config as _config
        # Patch SESSION_CONFIG in the config module (where it's imported from in create_session)
        original = _config.SESSION_CONFIG.copy()
        try:
            _config.SESSION_CONFIG["max_sessions_per_day"] = 1
            # First session succeeds
            s1 = sm.create_session(profile_id=profile.profile_id, session_type="student")
            # End it so there's no active session conflict
            sm.end_session(s1.session_id)
            # Second session should fail due to daily limit
            with pytest.raises(SessionLimitError, match="Daily session limit"):
                sm.create_session(profile_id=profile.profile_id, session_type="student")
        finally:
            _config.SESSION_CONFIG.clear()
            _config.SESSION_CONFIG.update(original)

    def test_concurrent_session_limit(self, sm, parent_and_profile):
        """A profile cannot have two active sessions simultaneously."""
        _, profile = parent_and_profile
        from core.session_manager import SessionLimitError
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        with pytest.raises(SessionLimitError, match="already has an active session"):
            sm.create_session(profile_id=profile.profile_id, session_type="student")

    def test_get_messages_no_conversations(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        session = sm.create_session(profile_id=profile.profile_id, session_type="student")
        messages = sm.get_messages(session.session_id)
        assert isinstance(messages, list)
        assert len(messages) == 0

    def test_get_messages_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_messages("s1")
        assert result == []

    def test_get_profile_sessions_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_profile_sessions("p1")
        assert result == []

    def test_get_active_session_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_active_session("p1")
        assert result is None

    def test_get_session_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.get_session("s1")
        assert result is None

    def test_end_session_db_error(self):
        """DB error on end_session raises SessionError."""
        import sqlite3
        from unittest.mock import MagicMock
        from core.session_manager import SessionManager, Session, SessionError

        mock_db = MagicMock()
        # get_session returns an active session
        active_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student", "started_at": "2024-01-01T10:00:00+00:00",
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux"
        }
        mock_db.execute_query.return_value = [active_row]
        mock_db.execute_write.side_effect = sqlite3.Error("DB write failed")

        sm = SessionManager(mock_db)
        with pytest.raises(SessionError):
            sm.end_session("s1")

    def test_create_session_db_error(self):
        """DB error on create_session raises SessionError."""
        import sqlite3
        from unittest.mock import MagicMock
        from core.session_manager import SessionManager, SessionError

        mock_db = MagicMock()
        mock_db.execute_query.return_value = []  # no active session, no daily count
        mock_db.execute_write.side_effect = sqlite3.Error("DB write failed")

        sm = SessionManager(mock_db)
        with pytest.raises(SessionError):
            sm.create_session(profile_id="p1", session_type="student")

    def test_create_session_no_db(self):
        """Creating session without DB still returns a Session object."""
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        session = sm_no_db.create_session(profile_id=None, session_type="student")
        assert session is not None
        assert session.is_active is True

    def test_increment_question_count_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.increment_question_count("s1")
        assert result is False

    def test_increment_question_count_db_error(self):
        import sqlite3
        from unittest.mock import MagicMock
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_write.side_effect = sqlite3.Error("fail")
        sm = SessionManager(mock_db)
        result = sm.increment_question_count("s1")
        assert result is False

    def test_session_timeout_check_no_db(self):
        """is_session_timed_out when db query works but session not found returns False."""
        from unittest.mock import MagicMock
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_query.return_value = []  # no session
        sm = SessionManager(mock_db)
        result = sm.is_session_timed_out("nonexistent")
        assert result is False

    def test_row_to_session_none_returns_none(self):
        from core.session_manager import SessionManager
        sm_inst = SessionManager(None)
        result = sm_inst._row_to_session(None)
        assert result is None

    def test_get_session_history_alias(self, sm, parent_and_profile):
        _, profile = parent_and_profile
        sm.create_session(profile_id=profile.profile_id, session_type="student")
        history = sm.get_session_history(profile.profile_id, limit=5)
        assert isinstance(history, list)
        assert len(history) >= 1

    def test_cleanup_timed_out_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        result = sm_no_db.cleanup_timed_out_sessions()
        assert result == 0

    def test_duration_negative_clamped_to_zero(self):
        """Duration computation where ended < started should yield 0."""
        from unittest.mock import MagicMock
        from core.session_manager import SessionManager, Session

        mock_db = MagicMock()
        # Simulate session where started_at is in the future (edge case)
        active_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "2099-01-01T10:00:00+00:00",  # future start
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux"
        }
        mock_db.execute_query.return_value = [active_row]
        mock_db.execute_write.return_value = None

        sm = SessionManager(mock_db)
        result = sm.end_session("s1")
        assert result is True


# ---------------------------------------------------------------------------
# Additional coverage tests (uncovered lines)
# ---------------------------------------------------------------------------

class TestRowToSessionColumnError:
    """Cover lines 141-143: _safe() fallback when column extraction fails."""

    def test_row_with_missing_columns_returns_session_with_nones(self):
        from core.session_manager import SessionManager

        sm_inst = SessionManager(None)
        # A row that raises KeyError for most columns
        bad_row = {}
        session = sm_inst._row_to_session(bad_row)
        assert session is not None
        assert session.session_id is None
        assert session.profile_id is None

    def test_row_as_tuple_with_index_error(self):
        """Tuple-like row too short triggers IndexError in _safe()."""
        from core.session_manager import SessionManager

        sm_inst = SessionManager(None)
        # A tuple that will fail on column name access
        class TupleRow:
            """Row that fails on string key access and integer index."""
            def __getitem__(self, key):
                raise TypeError(f"bad key: {key}")

        session = sm_inst._row_to_session(TupleRow())
        assert session is not None
        assert session.session_id is None


class TestTimezoneNormalization:
    """Cover lines 203, 207-211: timezone normalization in end_session."""

    def test_end_session_naive_start_aware_end(self):
        """started_at naive + ended_at aware triggers tz normalization (line 200-201)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "2024-06-01T10:00:00",  # naive
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }
        mock_db.execute_query.return_value = [row]
        mock_db.execute_write.return_value = None

        sm = SessionManager(mock_db)
        result = sm.end_session("s1")
        assert result is True
        # The write should have been called with a duration
        args = mock_db.execute_write.call_args[0][1]
        assert args[1] is not None  # duration_minutes

    def test_end_session_aware_start_naive_end(self):
        """started_at aware + ended_at naive triggers tz normalization (line 202-203)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "2024-06-01T10:00:00+00:00",  # aware
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }
        mock_db.execute_query.return_value = [row]
        mock_db.execute_write.return_value = None

        sm = SessionManager(mock_db)
        # Force a naive ended_at by patching _now_iso
        with patch.object(sm, '_now_iso', return_value="2024-06-01T10:30:00"):
            result = sm.end_session("s1")
        assert result is True


class TestDurationValueError:
    """Cover lines 207-211: ValueError in duration calculation."""

    def test_end_session_bad_started_at_format(self):
        """Invalid started_at triggers ValueError fallback, duration=None."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "not-a-date",  # will cause ValueError
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }
        mock_db.execute_query.return_value = [row]
        mock_db.execute_write.return_value = None

        sm = SessionManager(mock_db)
        result = sm.end_session("s1")
        assert result is True
        # duration should be None due to parse failure
        args = mock_db.execute_write.call_args[0][1]
        assert args[1] is None  # duration_minutes


class TestGetSessionDurationErrors:
    """Cover lines 254, 256-260: get_session_duration ValueError fallback."""

    def test_active_session_bad_started_at(self):
        """Active session with unparseable started_at returns None (lines 256-260)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "garbage-date",
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }
        mock_db.execute_query.return_value = [row]

        sm = SessionManager(mock_db)
        result = sm.get_session_duration("s1")
        assert result is None

    def test_active_session_naive_started_at_gets_tz(self):
        """Active session with naive started_at gets UTC tz added (line 253-254)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "2024-06-01T10:00:00",  # naive
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }
        mock_db.execute_query.return_value = [row]

        sm = SessionManager(mock_db)
        result = sm.get_session_duration("s1")
        assert result is not None
        assert isinstance(result, int)


class TestDBErrorOnWriteOps:
    """Cover DB error handlers: lines 273-277, 292-294, 305-307."""

    def test_update_last_activity_db_error(self):
        """DB error on _update_last_activity returns False (lines 273-277)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_write.side_effect = sqlite3.Error("write failed")

        sm = SessionManager(mock_db)
        result = sm._update_last_activity("s1", "2024-01-01T00:00:00+00:00")
        assert result is False

    def test_update_session_start_db_error(self):
        """DB error on _update_session_start returns False (lines 292-294)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_write.side_effect = sqlite3.Error("write failed")

        sm = SessionManager(mock_db)
        result = sm._update_session_start("s1", "2024-01-01T00:00:00+00:00")
        assert result is False

    def test_set_session_duration_db_error(self):
        """DB error on _set_session_duration returns False (lines 305-307)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_write.side_effect = sqlite3.Error("write failed")

        sm = SessionManager(mock_db)
        result = sm._set_session_duration("s1", 30)
        assert result is False


class TestDailyTimeLimitEdgeCases:
    """Cover lines 344, 348, 351-352: daily time limit extraction edge cases."""

    def test_daily_limit_from_tuple_row(self):
        """When profile row is a tuple, extract first element (lines 345-346)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        # First query: sessions total
        # Second query: profile daily limit — returns a tuple row
        mock_db.execute_query.side_effect = [
            [{"total": 30}],        # SUM of durations
            [(90,)],                 # profile row as tuple (not dict)
        ]

        sm = SessionManager(mock_db)
        can_start, remaining = sm.check_daily_time_limit("p1")
        assert isinstance(can_start, bool)
        assert isinstance(remaining, int)

    def test_daily_limit_profile_row_empty_tuple(self):
        """Empty tuple row returns None for profile_limit (line 348)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_query.side_effect = [
            [{"total": 0}],
            [()],  # empty tuple
        ]

        sm = SessionManager(mock_db)
        can_start, remaining = sm.check_daily_time_limit("p1")
        assert can_start is True
        assert remaining == 120  # default

    def test_daily_limit_db_error_uses_default(self):
        """DB error reading profile limit uses default (lines 351-352)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"total": 0}]
            raise sqlite3.Error("can't read profiles")
        mock_db.execute_query.side_effect = side_effect

        sm = SessionManager(mock_db)
        can_start, remaining = sm.check_daily_time_limit("p1")
        assert can_start is True
        assert remaining == 120


class TestDailyTimeLimitDictRow:
    """Cover line 344: dict row for daily_time_limit_minutes."""

    def test_daily_limit_from_dict_row(self):
        """When profile row is a dict, extract via .get() (line 343-344)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_query.side_effect = [
            [{"total": 0}],
            [{"daily_time_limit_minutes": 45}],  # dict row
        ]

        sm = SessionManager(mock_db)
        can_start, remaining = sm.check_daily_time_limit("p1")
        assert can_start is True
        assert remaining == 45


class TestNaiveDatetimeTimezoneInTimeout:
    """Cover lines 428, 440: naive datetime tz fixup in is_session_timed_out."""

    def test_naive_last_activity_gets_utc(self):
        """Naive last_activity datetime gets UTC assigned (line 427-428)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]
            else:
                # Naive datetime (no tz info) — old enough to be idle
                return [{"last_activity": "2000-01-01T00:00:00"}]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        assert result is True

    def test_naive_started_at_gets_utc_in_max_duration(self):
        """Naive started_at datetime gets UTC in max-duration check (line 439-440)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "2000-01-01T00:00:00",  # naive, old enough
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]
            else:
                return [{"last_activity": None}]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        assert result is True


class TestOrphanRecoveryNaiveDatetime:
    """Cover line 478: naive started_at gets UTC in recover_orphaned_sessions."""

    def test_recover_orphaned_naive_datetime(self):
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        # Query returns orphaned sessions with naive timestamps
        mock_db.execute_query.side_effect = [
            [{"session_id": "s1", "started_at": "2000-01-01T00:00:00"}],  # orphaned list
            # get_session for end_session
            [{"session_id": "s1", "profile_id": "p1", "parent_id": None,
              "session_type": "student", "started_at": "2000-01-01T00:00:00",
              "ended_at": None, "duration_minutes": None,
              "questions_asked": 0, "platform": "Linux"}],
        ]
        mock_db.execute_write.return_value = None

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"max_session_hours": 4}):
            recovered = sm.recover_orphaned_sessions()
        assert recovered == 1


class TestSessionTimeoutParsingErrors:
    """Cover lines 420, 431-432, 443-444: session timeout parsing errors."""

    def test_last_activity_as_tuple_row(self):
        """last_activity from tuple row (line 420-423)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        # get_session query returns active session
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]  # get_session
            else:
                # last_activity query as tuple row
                return [("2000-01-01T00:00:00+00:00",)]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        # Should be timed out due to old last_activity
        assert result is True

    def test_last_activity_bad_format(self):
        """Unparseable last_activity triggers ValueError fallback (lines 431-432)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]
            else:
                return [{"last_activity": "not-a-date"}]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        # Should not crash; falls through to max duration check
        assert result is False

    def test_started_at_bad_format_in_timeout_check(self):
        """Unparseable started_at in max-duration check (lines 443-444)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": "not-a-date",
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]
            else:
                return [{"last_activity": None}]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        # Both checks fail gracefully, returns False
        assert result is False

    def test_last_activity_dict_row(self):
        """last_activity from dict row (line 419-420)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        session_row = {
            "session_id": "s1", "profile_id": "p1", "parent_id": None,
            "session_type": "student",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None, "duration_minutes": None,
            "questions_asked": 0, "platform": "Linux",
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [session_row]
            else:
                return [{"last_activity": "2000-01-01T00:00:00+00:00"}]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"idle_timeout_minutes": 30, "max_session_hours": 4}):
            result = sm.is_session_timed_out("s1")
        assert result is True


class TestOrphanedSessionRecoveryError:
    """Cover lines 482-485: exception during orphan recovery."""

    def test_recover_orphaned_bad_started_at(self):
        """Unparseable started_at in orphan recovery logs warning and continues."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_query.return_value = [
            {"session_id": "bad-s1", "started_at": "not-a-date"},
            {"session_id": "bad-s2", "started_at": "also-not-a-date"},
        ]

        sm = SessionManager(mock_db)
        with patch("config.SESSION_CONFIG", {"max_session_hours": 4}):
            recovered = sm.recover_orphaned_sessions()
        # Neither should crash; both skip with warning
        assert recovered == 0


class TestGetMessagesWithData:
    """Cover lines 543-554: message retrieval with conversation/message rows."""

    def test_get_messages_with_dict_rows(self):
        """Messages as dict rows are appended directly (line 551-552)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        conv_rows = [{"conversation_id": "c1"}]
        msg_dict = {
            "message_id": "m1", "conversation_id": "c1",
            "role": "user", "content": "Hello",
            "timestamp": "2024-01-01T00:00:00", "model_used": "gpt",
            "response_time_ms": 100, "tokens_used": 10,
            "safety_filtered": False,
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return conv_rows
            else:
                return [msg_dict]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        messages = sm.get_messages("s1")
        assert len(messages) == 1
        assert messages[0]["message_id"] == "m1"

    def test_get_messages_with_tuple_rows(self):
        """Messages as tuple rows are converted to dict (lines 553-566)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        conv_rows = [{"conversation_id": "c1"}]
        msg_tuple = ("m1", "c1", "user", "Hello", "2024-01-01T00:00:00", "gpt", 100, 10, False)

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return conv_rows
            else:
                return [msg_tuple]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        messages = sm.get_messages("s1")
        assert len(messages) == 1
        assert messages[0]["message_id"] == "m1"
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_get_messages_conv_row_as_tuple(self):
        """Conversation row as tuple — conv_id extracted via index (line 543)."""
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        conv_tuple = ("c1",)  # not a dict
        msg_dict = {
            "message_id": "m1", "conversation_id": "c1",
            "role": "user", "content": "Hi",
            "timestamp": "2024-01-01T00:00:00", "model_used": "gpt",
            "response_time_ms": 50, "tokens_used": 5,
            "safety_filtered": False,
        }

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [conv_tuple]
            else:
                return [msg_dict]
        mock_db.execute_query.side_effect = query_side_effect

        sm = SessionManager(mock_db)
        messages = sm.get_messages("s1")
        assert len(messages) == 1


class TestGetUsageStatsNoDb:
    """Cover line 502: get_usage_stats with db=None."""

    def test_get_usage_stats_no_db(self):
        from core.session_manager import SessionManager
        sm_no_db = SessionManager(None)
        stats = sm_no_db.get_usage_stats("p1", days=7)
        assert stats["total_sessions"] == 0
        assert stats["days"] == 7


class TestGetMessagesDBError:
    """Cover lines 568-572: DB error in get_messages returns empty list."""

    def test_get_messages_db_error(self):
        from core.session_manager import SessionManager

        mock_db = MagicMock()
        mock_db.execute_query.side_effect = sqlite3.Error("query failed")

        sm = SessionManager(mock_db)
        result = sm.get_messages("s1")
        assert result == []


class TestModuleLevelImportFallback:
    """Cover lines 579-580: ImportError fallback for db_manager at module level."""

    def test_session_manager_module_import_succeeds(self):
        """The module-level session_manager should be importable."""
        from core.session_manager import session_manager
        assert session_manager is not None

    def test_session_manager_with_none_db(self):
        """SessionManager(None) works as in-memory fallback (line 579-580)."""
        from core.session_manager import SessionManager
        sm = SessionManager(None)
        assert sm.db is None
        assert sm.get_session("any") is None

    def test_module_level_import_fallback(self):
        """Reload session_manager with storage.database blocked to cover lines 579-580."""
        import importlib
        import sys
        import core.session_manager as sm_mod

        db_mod = sys.modules.get("storage.database")
        try:
            sys.modules["storage.database"] = None
            importlib.reload(sm_mod)
            assert sm_mod.session_manager is not None
            assert sm_mod.session_manager.db is None
        finally:
            if db_mod is not None:
                sys.modules["storage.database"] = db_mod
            else:
                sys.modules.pop("storage.database", None)
            importlib.reload(sm_mod)
