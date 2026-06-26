"""Profile data model + exception hierarchy (extracted verbatim)."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ChildProfile:
    profile_id: str
    parent_id: str
    name: str
    age: int
    grade: str
    avatar: str = "default"
    learning_level: str = "adaptive"
    daily_time_limit_minutes: int = 120
    is_active: bool = True
    total_sessions: int = 0
    total_questions: int = 0
    last_active: Optional[str] = None
    subjects_focus: Optional[List[str]] = None
    # COPPA: True once a parent has completed verifiable consent for an under-13
    # child (coppa_verified in the DB). The dashboard's "Action Required" card
    # reads this — if it's dropped, the card can never clear.
    parental_consent_verified: bool = False

    def to_dict(self) -> dict:
        """Convert profile to dictionary for JSON serialization"""
        return {
            "profile_id": self.profile_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "age": self.age,
            "grade": self.grade,
            "avatar": self.avatar,
            "learning_level": self.learning_level,
            "daily_time_limit_minutes": self.daily_time_limit_minutes,
            "is_active": self.is_active,
            "total_sessions": self.total_sessions,
            "total_questions": self.total_questions,
            "last_active": self.last_active,
            "subjects_focus": self.subjects_focus or [],
            "parental_consent_verified": self.parental_consent_verified,
        }


class ProfileError(Exception):
    pass


class ProfileValidationError(ProfileError):
    pass


class ProfileNotFoundError(ProfileError):
    pass


class PermissionDeniedError(ProfileError):
    pass
