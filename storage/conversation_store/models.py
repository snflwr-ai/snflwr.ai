"""Conversation data models (extracted verbatim from conversation_store.py)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Message:
    """Individual message in a conversation"""

    message_id: str
    conversation_id: str
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: datetime
    model_used: Optional[str] = None
    response_time_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    safety_filtered: bool = False

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "model_used": self.model_used,
            "response_time_ms": self.response_time_ms,
            "tokens_used": self.tokens_used,
            "safety_filtered": self.safety_filtered,
        }


@dataclass
class Conversation:
    """Complete conversation with metadata"""

    conversation_id: str
    session_id: str
    profile_id: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    subject_area: Optional[str]
    is_flagged: bool
    flag_reason: Optional[str]
    messages: List[Message]

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": self.message_count,
            "subject_area": self.subject_area,
            "is_flagged": self.is_flagged,
            "flag_reason": self.flag_reason,
            "messages": [m.to_dict() for m in self.messages],
        }
