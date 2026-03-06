from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid
import platform
import threading
from typing import Optional, List

from utils.logger import get_logger, sanitize_log_value
from utils.cache import cached
from storage.db_adapters import DB_ERRORS

logger = get_logger(__name__)


@dataclass
class Session:
    session_id: str
    profile_id: Optional[str]
    parent_id: Optional[str] = None
    session_type: str = 'student'
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    questions_asked: int = 0
    platform: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    def to_dict(self) -> dict:
        return {
            'session_id': self.session_id,
            'profile_id': self.profile_id,
            'parent_id': self.parent_id,
            'session_type': self.session_type,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_minutes': self.duration_minutes,
            'questions_asked': self.questions_asked,
            'platform': self.platform,
            'is_active': self.is_active,
        }


class SessionError(Exception):
    pass


class SessionLimitError(SessionError):
    pass


class SessionTimeoutError(SessionError):
    pass


class SessionManager:
    """Session manager that persists sessions to the SQLite database."""

    def __init__(self, db_manager=None):
        self.db = db_manager
        self._session_lock = threading.Lock()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_session(self, profile_id: Optional[str] = None, parent_id: Optional[str] = None,
                       session_type: str = 'student') -> Session:
        # Acquire lock for entire check-and-create operation to prevent race conditions
        with self._session_lock:
            # Enforce concurrent (one active per profile) and daily limits
            from config import SESSION_CONFIG
            if profile_id is not None and self.get_active_session(profile_id) is not None:
                raise SessionLimitError('Profile already has an active session')

            if profile_id is not None and self.db is not None:
                today_count = self.get_sessions_today_count(profile_id)
                if today_count >= SESSION_CONFIG.get('max_sessions_per_day', 5):
                    raise SessionLimitError('Daily session limit reached')

            session_id = uuid.uuid4().hex
            started_at = self._now_iso()
            plat = platform.system()

            # Persist to DB if available — fail loudly if write fails
            # so we never return a session that wasn't actually saved
            if self.db is not None:
                insert_sql = (
                    "INSERT INTO sessions (session_id, profile_id, parent_id, session_type, started_at, ended_at, duration_minutes, questions_asked, platform)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                )
                params = (session_id, profile_id, parent_id, session_type, started_at, None, None, 0, plat)
                try:
                    self.db.execute_write(insert_sql, params)
                except DB_ERRORS as e:
                    logger.error(f"Failed to persist session {session_id!r} to database: {e}")
                    raise SessionError(f"Could not create session: database write failed") from e

            return Session(
                session_id=session_id,
                profile_id=profile_id,
                parent_id=parent_id,
                session_type=session_type,
                started_at=started_at,
                ended_at=None,
                duration_minutes=None,
                questions_asked=0,
                platform=plat
            )

    def _row_to_session(self, row) -> Optional[Session]:
        if row is None:
            return None
        def _safe(col):
            try:
                return row[col]
            except (KeyError, IndexError, TypeError) as e:
                logger.debug(f"Column '{col}' not found in row: {e}")
                return None

        return Session(
            session_id=_safe('session_id'),
            profile_id=_safe('profile_id'),
            parent_id=_safe('parent_id'),
            session_type=_safe('session_type'),
            started_at=_safe('started_at'),
            ended_at=_safe('ended_at'),
            duration_minutes=_safe('duration_minutes'),
            questions_asked=_safe('questions_asked') or 0,
            platform=_safe('platform')
        )

    def get_session(self, session_id: str) -> Optional[Session]:
        if self.db is None:
            return None
        rows = self.db.execute_query("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def get_active_session(self, profile_id: str) -> Optional[Session]:
        if self.db is None:
            return None
        rows = self.db.execute_query(
            "SELECT * FROM sessions WHERE profile_id = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (profile_id,)
        )
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def get_profile_sessions(self, profile_id: str, limit: int = 100) -> List[Session]:
        if self.db is None:
            return []
        rows = self.db.execute_query(
            "SELECT * FROM sessions WHERE profile_id = ? ORDER BY started_at DESC LIMIT ?",
            (profile_id, limit)
        )
        return [self._row_to_session(r) for r in rows]

    def end_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False
        if not session.is_active:
            return True

        ended_at = self._now_iso()
        # compute duration in minutes
        try:
            started = datetime.fromisoformat(session.started_at)
            ended = datetime.fromisoformat(ended_at)
            # Normalize timezone awareness for subtraction
            if started.tzinfo is None and ended.tzinfo is not None:
                started = started.replace(tzinfo=timezone.utc)
            elif started.tzinfo is not None and ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            duration = int((ended - started).total_seconds() // 60)
            if duration < 0:
                duration = 0
        except ValueError as e:
            logger.warning(f"Failed to calculate session duration for {sanitize_log_value(session_id)!r}: {e}")
            duration = None

        if self.db is not None:
            try:
                self.db.execute_write(
                    "UPDATE sessions SET ended_at = ?, duration_minutes = ? WHERE session_id = ?",
                    (ended_at, duration, session_id)
                )
            except DB_ERRORS as e:
                logger.error(f"Failed to persist session end for {sanitize_log_value(session_id)!r}: {e}")
                raise SessionError(f"Could not end session: database write failed") from e

        return True

    def increment_question_count(self, session_id: str) -> bool:
        if self.db is None:
            return False
        try:
            self.db.execute_write(
                "UPDATE sessions SET questions_asked = COALESCE(questions_asked,0) + 1 WHERE session_id = ?",
                (session_id,)
            )
        except DB_ERRORS as e:
            logger.error(f"Failed to increment question count for session {session_id!r}: {e}")
            return False
        return True

    def get_session_duration(self, session_id: str) -> Optional[int]:
        session = self.get_session(session_id)
        if session is None:
            return None
        if session.duration_minutes is not None:
            return session.duration_minutes
        # active session: compute from now (UTC)
        try:
            started = datetime.fromisoformat(session.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - started).total_seconds() // 60)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to compute duration for active session {session_id}: {e}")
            return None

    # Helpers used by tests to manipulate timestamps
    def _update_last_activity(self, session_id: str, iso_ts: str):
        if self.db is None:
            return False
        # Add last_activity column if missing is handled elsewhere; store into sessions.last_activity
        try:
            self.db.execute_write("UPDATE sessions SET last_activity = ? WHERE session_id = ?", (iso_ts, session_id))
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to update last_activity for session {session_id}: {e}")
            return False

    def update_activity(self, session_id: str):
        """Public method used by tests to mark activity now."""
        return self._update_last_activity(session_id, self._now_iso())

    def _update_session_start(self, session_id: str, iso_ts: str):
        if self.db is None:
            return False
        try:
            self.db.execute_write("UPDATE sessions SET started_at = ? WHERE session_id = ?", (iso_ts, session_id))
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to update session start time for {session_id}: {e}")
            return False

    def _set_session_duration(self, session_id: str, minutes: int) -> bool:
        if self.db is None:
            return False
        try:
            self.db.execute_write("UPDATE sessions SET duration_minutes = ? WHERE session_id = ?", (minutes, session_id))
            return True
        except DB_ERRORS as e:
            logger.error(f"Failed to set session duration for {session_id}: {e}")
            return False

    def get_sessions_today_count(self, profile_id: str) -> int:
        if self.db is None:
            return 0
        today = datetime.now(timezone.utc).date().isoformat()
        # Use LIKE for database portability (works in SQLite and PostgreSQL)
        rows = self.db.execute_query(
            "SELECT COUNT(*) as count FROM sessions WHERE profile_id = ? AND started_at LIKE ?",
            (profile_id, f"{today}%")
        )
        return rows[0]['count'] if rows else 0

    def check_daily_time_limit(self, profile_id: str):
        # returns (can_start: bool, remaining_minutes: int)
        if self.db is None:
            return True, 9999

        # Get total time used today
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.db.execute_query("SELECT SUM(COALESCE(duration_minutes,0)) as total FROM sessions WHERE profile_id = ? AND started_at LIKE ?", (profile_id, f"{today}%"))
        total = rows[0]['total'] or 0

        # Get profile-specific daily time limit
        daily_limit = 120  # Default
        try:
            profile_rows = self.db.execute_query(
                "SELECT daily_time_limit_minutes FROM child_profiles WHERE profile_id = ?",
                (profile_id,)
            )
            if profile_rows and len(profile_rows) > 0:
                row = profile_rows[0]
                # Safely extract value whether row is dict or tuple
                if isinstance(row, dict):
                    profile_limit = row.get('daily_time_limit_minutes')
                elif row and len(row) > 0:
                    profile_limit = row[0]
                else:
                    profile_limit = None
                if profile_limit:
                    daily_limit = profile_limit
        except DB_ERRORS as e:
            logger.debug(f"Failed to get daily time limit for profile {profile_id}, using default: {e}")

        remaining = daily_limit - total
        return (remaining > 0, remaining)

    def get_total_session_time_today(self, profile_id: str) -> int:
        if self.db is None:
            return 0
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.db.execute_query("SELECT SUM(COALESCE(duration_minutes,0)) as total FROM sessions WHERE profile_id = ? AND started_at LIKE ?", (profile_id, f"{today}%"))
        return int(rows[0]['total'] or 0)

    @cached(ttl=60, key_prefix="profile_stats")
    def get_profile_statistics(self, profile_id: str) -> dict:
        # total_sessions, total_questions, total_minutes, average_session_minutes
        if self.db is None:
            return {'total_sessions': 0, 'total_questions': 0, 'total_minutes': 0, 'average_session_minutes': 0}
        rows = self.db.execute_query("SELECT COUNT(*) as total_sessions, SUM(COALESCE(questions_asked,0)) as total_questions, SUM(COALESCE(duration_minutes,0)) as total_minutes FROM sessions WHERE profile_id = ?", (profile_id,))
        r = rows[0]
        total_sessions = int(r['total_sessions'] or 0)
        total_questions = int(r['total_questions'] or 0)
        total_minutes = int(r['total_minutes'] or 0)
        avg = int(total_minutes // total_sessions) if total_sessions > 0 else 0
        return {
            'total_sessions': total_sessions,
            'total_questions': total_questions,
            'total_minutes': total_minutes,
            'average_session_minutes': avg
        }

    def is_session_timed_out(self, session_id: str) -> bool:
        # Check last_activity then max duration
        session = self.get_session(session_id)
        if session is None:
            return False
        from config import SESSION_CONFIG
        idle_minutes = SESSION_CONFIG.get('idle_timeout_minutes', 30)
        max_hours = SESSION_CONFIG.get('max_session_hours', 4)

        # Check last_activity if present
        rows = self.db.execute_query("SELECT last_activity FROM sessions WHERE session_id = ?", (session_id,))
        last_activity = None
        if rows:
            row = rows[0]
            if isinstance(row, dict):
                last_activity = row.get('last_activity')
            else:
                # Tuple/Row object - just get first column
                last_activity = row[0] if row and row[0] else None
        if last_activity:
            try:
                la = datetime.fromisoformat(last_activity)
                if la.tzinfo is None:
                    la = la.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - la > timedelta(minutes=idle_minutes):
                    return True
            except ValueError as e:
                logger.debug(f"Failed to parse last_activity for session {session_id}: {e}")

        # Check max duration
        try:
            started = datetime.fromisoformat(session.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - started > timedelta(hours=max_hours):
                return True
        except ValueError as e:
            logger.debug(f"Failed to check max duration for session {session_id}: {e}")

        return False

    def cleanup_timed_out_sessions(self) -> int:
        if self.db is None:
            return 0
        # Find sessions that are active and timed out
        rows = self.db.execute_query("SELECT session_id, started_at FROM sessions WHERE ended_at IS NULL")
        cleaned = 0
        for r in rows:
            sid = r['session_id']
            if self.is_session_timed_out(sid):
                if self.end_session(sid):
                    cleaned += 1
        return cleaned

    def recover_orphaned_sessions(self) -> int:
        # End sessions that have been active longer than max_session_hours
        if self.db is None:
            return 0
        from config import SESSION_CONFIG
        max_hours = SESSION_CONFIG.get('max_session_hours', 4)
        rows = self.db.execute_query("SELECT session_id, started_at FROM sessions WHERE ended_at IS NULL")
        recovered = 0
        for r in rows:
            try:
                started = datetime.fromisoformat(r['started_at'])
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - started > timedelta(hours=max_hours):
                    if self.end_session(r['session_id']):
                        recovered += 1
            except (ValueError, TypeError, *DB_ERRORS) as e:
                _sid = r['session_id'] if r else 'unknown'
                logger.warning(f"Failed to recover orphaned session {_sid!r}: {e}")
                continue
        return recovered

    def get_all_active_sessions(self) -> List[Session]:
        if self.db is None:
            return []
        rows = self.db.execute_query("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC")
        return [self._row_to_session(r) for r in rows]

    def get_session_history(self, profile_id: str, limit: int = 10) -> List[Session]:
        return self.get_profile_sessions(profile_id, limit)

    def get_usage_stats(self, profile_id: str, days: int = 7) -> dict:
        """Get usage statistics for a profile over the given number of days."""
        if self.db is None:
            return {'total_sessions': 0, 'total_questions': 0, 'total_minutes': 0, 'days': days}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.execute_query(
            "SELECT COUNT(*) as total_sessions, "
            "SUM(COALESCE(questions_asked,0)) as total_questions, "
            "SUM(COALESCE(duration_minutes,0)) as total_minutes "
            "FROM sessions WHERE profile_id = ? AND started_at >= ?",
            (profile_id, cutoff)
        )
        if not rows:
            return {'total_sessions': 0, 'total_questions': 0, 'total_minutes': 0, 'days': days}
        r = rows[0]
        return {
            'total_sessions': int(r['total_sessions'] or 0),
            'total_questions': int(r['total_questions'] or 0),
            'total_minutes': int(r['total_minutes'] or 0),
            'days': days,
        }

    def get_messages(self, session_id: str) -> list:
        """Get all messages for a session via conversation store."""
        if self.db is None:
            return []
        try:
            # Find conversations for this session, then get their messages
            rows = self.db.execute_query(
                "SELECT conversation_id FROM conversations WHERE session_id = ?",
                (session_id,)
            )
            messages = []
            for row in rows:
                conv_id = row['conversation_id'] if isinstance(row, dict) else row[0]
                msg_rows = self.db.execute_query(
                    "SELECT message_id, conversation_id, role, content, timestamp, "
                    "model_used, response_time_ms, tokens_used, safety_filtered "
                    "FROM messages WHERE conversation_id = ? ORDER BY timestamp",
                    (conv_id,)
                )
                for m in msg_rows:
                    if isinstance(m, dict):
                        messages.append(m)
                    else:
                        messages.append({
                            'message_id': m[0], 'conversation_id': m[1],
                            'role': m[2], 'content': m[3], 'timestamp': m[4],
                            'model_used': m[5], 'response_time_ms': m[6],
                            'tokens_used': m[7], 'safety_filtered': m[8],
                        })
            return messages
        except DB_ERRORS as e:
            logger.error(f"Failed to get messages for session {sanitize_log_value(session_id)!r}: {e}")
            return []


# Module-level convenience instance — uses db_manager for persistence
# (falls back gracefully to in-memory if database not yet initialized)
try:
    from storage.database import db_manager as _db
except ImportError:
    _db = None
session_manager = SessionManager(_db)
