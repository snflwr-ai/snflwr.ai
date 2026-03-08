# storage/conversation_store.py
"""
Conversation Storage and Retrieval System
Manages chat history with encryption, search, and export capabilities
"""

import secrets
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json

from config import system_config, safety_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager
from utils.logger import get_logger

logger = get_logger(__name__)


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


class ConversationStore:
    """
    Manages conversation storage and retrieval
    Handles encryption, search, and export
    """

    def __init__(self, db=None, encryption=None):
        """
        Initialize conversation store

        Args:
            db: DatabaseManager instance (optional, uses global if None)
            encryption: EncryptionManager instance (optional, uses global if None)
        """
        self.db = db if db is not None else db_manager
        self.encryption = encryption if encryption is not None else encryption_manager

        logger.info("Conversation store initialized")
        self._ensure_search_index_table()

    def _ensure_search_index_table(self):
        """Create search index table if it doesn't exist (SQLite auto-migration)."""
        try:
            self.db.execute_write("""
                CREATE TABLE IF NOT EXISTS message_search_index (
                    id INTEGER PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                )
            """)
            self.db.execute_write(
                "CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash)"
            )
            self.db.execute_write(
                "CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id)"
            )
        except Exception as e:
            logger.debug(f"Search index table setup: {e}")

    @staticmethod
    def _tokenize(text: str) -> set:
        """Tokenize text for search indexing. Returns deduplicated lowercase tokens >= 3 chars."""
        import re

        tokens = re.split(r"[\s\W]+", text.lower())
        return {t for t in tokens if len(t) >= 3}

    def _maybe_decrypt(self, stored: str) -> str:
        """Decrypt message content, falling back to plaintext for unencrypted messages."""
        if not stored or not self.encryption:
            return stored
        try:
            result = self.encryption.decrypt_string(stored)
            if result is not None:
                return result
        except (ValueError, TypeError) as e:
            logger.warning(f"Decryption error (data format): {type(e).__name__}")
        except Exception as e:
            # InvalidToken or unexpected errors — possible key mismatch or corruption
            logger.error(
                f"Decryption failed (possible key mismatch or data corruption): {type(e).__name__}"
            )
        # Decryption failed or returned None.
        # If encryption is enabled, this is a real failure — don't leak ciphertext.
        if safety_config.ENCRYPT_CONVERSATIONS:
            logger.warning(f"Decryption failed for stored message (len={len(stored)})")
            return "[Message could not be decrypted]"
        # Encryption disabled — assume legacy plaintext
        return stored

    def create_conversation(
        self, session_id: str, profile_id: str, subject_area: Optional[str] = None
    ) -> Conversation:
        """
        Create new conversation

        Args:
            session_id: Session identifier
            profile_id: Child profile identifier
            subject_area: Subject being discussed

        Returns:
            Conversation object

        Raises:
            Exception: If conversation creation fails
        """
        conversation_id = secrets.token_hex(16)
        now = datetime.now(timezone.utc)

        self.db.execute_write(
            """
            INSERT INTO conversations (
                conversation_id, session_id, profile_id,
                created_at, updated_at, message_count, subject_area
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                session_id,
                profile_id,
                now.isoformat(),
                now.isoformat(),
                0,
                subject_area,
            ),
        )

        logger.info(f"Conversation created: {conversation_id}")

        return Conversation(
            conversation_id=conversation_id,
            session_id=session_id,
            profile_id=profile_id,
            created_at=now,
            updated_at=now,
            message_count=0,
            subject_area=subject_area,
            is_flagged=False,
            flag_reason=None,
            messages=[],
        )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model_used: Optional[str] = None,
        response_time_ms: Optional[int] = None,
        tokens_used: Optional[int] = None,
        safety_filtered: bool = False,
    ) -> Message:
        """
        Add message to conversation

        Args:
            conversation_id: Conversation identifier
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            model_used: AI model used (for assistant messages)
            response_time_ms: Response time in milliseconds
            tokens_used: Tokens consumed
            safety_filtered: Whether content was filtered

        Returns:
            Message object

        Raises:
            Exception: If message creation fails
        """
        message_id = secrets.token_hex(16)
        now = datetime.now(timezone.utc)

        # Encrypt message content if enabled (COPPA/FERPA compliance)
        # Fail closed: if encryption is required but fails, do NOT store plaintext.
        store_content = content
        if safety_config.ENCRYPT_CONVERSATIONS and self.encryption:
            try:
                store_content = self.encryption.encrypt_string(content)
            except Exception as e:
                logger.critical(
                    f"COPPA VIOLATION PREVENTED: encryption failed, refusing to store plaintext: {e}"
                )
                raise RuntimeError(
                    "Message encryption failed; refusing to store plaintext (COPPA compliance)"
                ) from e

        # Add message
        self.db.execute_write(
            """
            INSERT INTO messages (
                message_id, conversation_id, role, content,
                timestamp, model_used, response_time_ms,
                tokens_used, safety_filtered
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                conversation_id,
                role,
                store_content,
                now.isoformat(),
                model_used,
                response_time_ms,
                tokens_used,
                safety_filtered,
            ),
        )

        # Index tokens for encrypted search (before encryption, content is still plaintext)
        if safety_config.ENCRYPT_CONVERSATIONS and self.encryption:
            try:
                tokens = self._tokenize(content)
                for token in tokens:
                    token_hash = self.encryption.hmac_token(token)
                    self.db.execute_write(
                        """
                        INSERT INTO message_search_index (message_id, conversation_id, token_hash)
                        VALUES (?, ?, ?)
                        """,
                        (message_id, conversation_id, token_hash),
                    )
            except Exception as e:
                # Non-fatal: search index failure should not block message storage
                logger.warning(f"Failed to index message tokens for search: {e}")

        # Update conversation
        self.db.execute_write(
            """
            UPDATE conversations
            SET message_count = message_count + 1,
                updated_at = ?
            WHERE conversation_id = ?
            """,
            (now.isoformat(), conversation_id),
        )

        # Update profile's last_active timestamp
        try:
            # Get profile_id from conversation
            conv_rows = self.db.execute_query(
                "SELECT profile_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            if conv_rows and len(conv_rows) > 0:
                row = conv_rows[0]
                # Safely extract profile_id whether row is dict or tuple
                if isinstance(row, dict):
                    profile_id = row.get("profile_id")
                elif row and len(row) > 0:
                    profile_id = row[0]
                else:
                    profile_id = None

                if profile_id:
                    self.db.execute_write(
                        "UPDATE child_profiles SET last_active = ? WHERE profile_id = ?",
                        (now.isoformat(), profile_id),
                    )
        except DB_ERRORS as e:
            logger.debug(f"Could not update last_active: {e}")

        logger.debug(f"Message added to conversation {conversation_id}")

        return Message(
            message_id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            timestamp=now,
            model_used=model_used,
            response_time_ms=response_time_ms,
            tokens_used=tokens_used,
            safety_filtered=safety_filtered,
        )

    def get_conversation(
        self, conversation_id: str, include_messages: bool = True
    ) -> Optional[Conversation]:
        """
        Retrieve conversation by ID

        Args:
            conversation_id: Conversation identifier
            include_messages: Whether to include messages

        Returns:
            Conversation object or None
        """
        try:
            # Get conversation metadata
            results = self.db.execute_query(
                """
                SELECT conversation_id, session_id, profile_id,
                       created_at, updated_at, message_count,
                       subject_area, is_flagged, flag_reason
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            )

            if not results:
                return None

            row = results[0]

            # Get messages if requested
            messages = []
            if include_messages:
                message_results = self.db.execute_query(
                    """
                    SELECT message_id, conversation_id, role, content,
                           timestamp, model_used, response_time_ms,
                           tokens_used, safety_filtered
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (conversation_id,),
                )

                for msg_row in message_results:
                    message = Message(
                        message_id=msg_row["message_id"],
                        conversation_id=msg_row["conversation_id"],
                        role=msg_row["role"],
                        content=self._maybe_decrypt(msg_row["content"]),
                        timestamp=datetime.fromisoformat(msg_row["timestamp"]),
                        model_used=msg_row["model_used"],
                        response_time_ms=msg_row["response_time_ms"],
                        tokens_used=msg_row["tokens_used"],
                        safety_filtered=bool(msg_row["safety_filtered"]),
                    )
                    messages.append(message)

            conversation = Conversation(
                conversation_id=row["conversation_id"],
                session_id=row["session_id"],
                profile_id=row["profile_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                message_count=row["message_count"],
                subject_area=row["subject_area"],
                is_flagged=bool(row["is_flagged"]),
                flag_reason=row["flag_reason"],
                messages=messages,
            )

            return conversation

        except DB_ERRORS + (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to retrieve conversation: {e}")
            return None

    def get_profile_conversations(
        self, profile_id: str, limit: int = 50, offset: int = 0
    ) -> List[Conversation]:
        """
        Get conversations for a profile

        Args:
            profile_id: Profile identifier
            limit: Maximum conversations to return
            offset: Offset for pagination

        Returns:
            List of Conversation objects (without messages)
        """
        try:
            results = self.db.execute_query(
                """
                SELECT conversation_id, session_id, profile_id,
                       created_at, updated_at, message_count,
                       subject_area, is_flagged, flag_reason
                FROM conversations
                WHERE profile_id = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (profile_id, limit, offset),
            )

            conversations = []
            for row in results:
                conversation = Conversation(
                    conversation_id=row["conversation_id"],
                    session_id=row["session_id"],
                    profile_id=row["profile_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    message_count=row["message_count"],
                    subject_area=row["subject_area"],
                    is_flagged=bool(row["is_flagged"]),
                    flag_reason=row["flag_reason"],
                    messages=[],
                )
                conversations.append(conversation)

            return conversations

        except DB_ERRORS + (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to get profile conversations: {e}")
            return []

    def search_conversations(
        self,
        profile_id: str,
        search_text: Optional[str] = None,
        subject_area: Optional[str] = None,
        flagged_only: bool = False,
    ) -> List[Conversation]:
        """
        Search conversations by content and/or subject area

        Args:
            profile_id: Profile identifier
            search_text: Optional text to search for in message content
            subject_area: Optional subject filter
            flagged_only: Only return flagged conversations

        Returns:
            List of matching conversations
        """
        try:
            # Build query
            if search_text:
                if safety_config.ENCRYPT_CONVERSATIONS and self.encryption:
                    # Encrypted mode: search via HMAC token index
                    search_tokens = self._tokenize(search_text)
                    if not search_tokens:
                        return []
                    token_hashes = [
                        self.encryption.hmac_token(t) for t in search_tokens
                    ]
                    placeholders = ",".join("?" * len(token_hashes))
                    query = f"""
                        SELECT DISTINCT c.conversation_id, c.session_id, c.profile_id,
                               c.created_at, c.updated_at, c.message_count,
                               c.subject_area, c.is_flagged, c.flag_reason
                        FROM conversations c
                        JOIN message_search_index msi ON c.conversation_id = msi.conversation_id
                        WHERE c.profile_id = ?
                        AND msi.token_hash IN ({placeholders})
                    """
                    params = [profile_id] + token_hashes
                else:
                    # Unencrypted mode: use SQL LIKE as before
                    query = """
                        SELECT DISTINCT c.conversation_id, c.session_id, c.profile_id,
                               c.created_at, c.updated_at, c.message_count,
                               c.subject_area, c.is_flagged, c.flag_reason
                        FROM conversations c
                        JOIN messages m ON c.conversation_id = m.conversation_id
                        WHERE c.profile_id = ?
                        AND m.content LIKE ?
                    """
                    escaped_search = search_text.replace("%", "\\%").replace("_", "\\_")
                    params = [profile_id, f"%{escaped_search}%"]
            else:
                # Just filter conversations
                query = """
                    SELECT conversation_id, session_id, profile_id,
                           created_at, updated_at, message_count,
                           subject_area, is_flagged, flag_reason
                    FROM conversations
                    WHERE profile_id = ?
                """
                params = [profile_id]

            if subject_area:
                query += (
                    " AND subject_area = ?"
                    if "WHERE" in query
                    else " WHERE subject_area = ?"
                )
                params.append(subject_area)

            if flagged_only:
                query += " AND is_flagged = 1"

            query += " ORDER BY updated_at DESC LIMIT 20"

            results = self.db.execute_query(query, tuple(params))

            conversations = []
            for row in results:

                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, TypeError):
                        return row[idx] if idx < len(row) else None

                conversation = Conversation(
                    conversation_id=g("conversation_id", 0),
                    session_id=g("session_id", 1),
                    profile_id=g("profile_id", 2),
                    created_at=datetime.fromisoformat(g("created_at", 3)),
                    updated_at=datetime.fromisoformat(g("updated_at", 4)),
                    message_count=g("message_count", 5),
                    subject_area=g("subject_area", 6),
                    is_flagged=bool(g("is_flagged", 7)),
                    flag_reason=g("flag_reason", 8),
                    messages=[],
                )
                conversations.append(conversation)

            return conversations

        except DB_ERRORS + (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to search conversations: {e}")
            return []

    def flag_conversation(self, conversation_id: str, reason: str) -> bool:
        """
        Flag conversation for parent review

        Args:
            conversation_id: Conversation identifier
            reason: Reason for flagging

        Returns:
            True if successful
        """
        try:
            self.db.execute_write(
                """
                UPDATE conversations
                SET is_flagged = 1, flag_reason = ?
                WHERE conversation_id = ?
                """,
                (reason, conversation_id),
            )

            logger.info(f"Conversation flagged: {conversation_id}, reason: {reason}")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to flag conversation: {e}")
            return False

    def export_conversation(
        self, conversation_id: str, format: str = "json"
    ) -> Optional[str]:
        """
        Export conversation to text format

        Args:
            conversation_id: Conversation identifier
            format: Export format ('json', 'txt', 'markdown')

        Returns:
            Formatted conversation string or None
        """
        try:
            conversation = self.get_conversation(conversation_id, include_messages=True)

            if not conversation:
                return None

            if format == "json":
                return json.dumps(conversation.to_dict(), indent=2)

            elif format == "txt":
                lines = [
                    f"Conversation: {conversation.conversation_id}",
                    f"Created: {conversation.created_at.strftime('%Y-%m-%d %H:%M')}",
                    f"Subject: {conversation.subject_area or 'General'}",
                    f"Messages: {conversation.message_count}",
                    "=" * 60,
                    "",
                ]

                for msg in conversation.messages:
                    lines.append(
                        f"[{msg.timestamp.strftime('%H:%M')}] {msg.role.upper()}:"
                    )
                    lines.append(msg.content)
                    lines.append("")

                return "\n".join(lines)

            elif format == "markdown":
                lines = [
                    f"# Conversation Export",
                    f"",
                    f"**ID**: {conversation.conversation_id}",
                    f"**Created**: {conversation.created_at.strftime('%Y-%m-%d %H:%M')}",
                    f"**Subject**: {conversation.subject_area or 'General'}",
                    f"**Messages**: {conversation.message_count}",
                    f"",
                    "---",
                    "",
                ]

                for msg in conversation.messages:
                    role_emoji = (
                        "[USER]"
                        if msg.role == "user"
                        else "[AI]" if msg.role == "assistant" else "[INFO]"
                    )
                    lines.append(
                        f"### {role_emoji} {msg.role.title()} - {msg.timestamp.strftime('%H:%M')}"
                    )
                    lines.append(f"")
                    lines.append(msg.content)
                    lines.append(f"")

                return "\n".join(lines)

            else:
                logger.warning(f"Unknown export format: {format}")
                return None

        except DB_ERRORS + (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as e:
            logger.error(f"Failed to export conversation: {e}")
            return None

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete conversation and all messages

        Args:
            conversation_id: Conversation identifier

        Returns:
            True if successful
        """
        try:
            # Messages will be deleted automatically (ON DELETE CASCADE)
            self.db.execute_write(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )

            logger.info(f"Conversation deleted: {conversation_id}")
            return True

        except DB_ERRORS as e:
            logger.error(f"Failed to delete conversation: {e}")
            return False

    def get_statistics(self, profile_id: str, days: int = 30) -> Dict:
        """
        Get conversation statistics for a profile

        Args:
            profile_id: Profile identifier
            days: Number of days to analyze

        Returns:
            Dictionary of statistics
        """
        try:
            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

            # Total conversations
            total_results = self.db.execute_query(
                """
                SELECT COUNT(*) as total,
                       SUM(message_count) as total_messages
                FROM conversations
                WHERE profile_id = ? AND created_at >= ?
                """,
                (profile_id, cutoff_date),
            )

            # Subject distribution
            subject_results = self.db.execute_query(
                """
                SELECT subject_area, COUNT(*) as count
                FROM conversations
                WHERE profile_id = ? AND created_at >= ?
                AND subject_area IS NOT NULL
                GROUP BY subject_area
                ORDER BY count DESC
                """,
                (profile_id, cutoff_date),
            )

            # Flagged conversations
            flagged_results = self.db.execute_query(
                """
                SELECT COUNT(*) as flagged_count
                FROM conversations
                WHERE profile_id = ? AND created_at >= ?
                AND is_flagged = 1
                """,
                (profile_id, cutoff_date),
            )

            # Safely extract statistics with proper null checks
            total_conversations = 0
            total_messages = 0
            flagged_count = 0

            if total_results and len(total_results) > 0:
                row = total_results[0]
                if isinstance(row, dict):
                    total_conversations = row.get("total") or 0
                    total_messages = row.get("total_messages") or 0
                elif row and len(row) >= 2:
                    total_conversations = row[0] or 0
                    total_messages = row[1] or 0

            if flagged_results and len(flagged_results) > 0:
                row = flagged_results[0]
                if isinstance(row, dict):
                    flagged_count = row.get("flagged_count") or 0
                elif row and len(row) > 0:
                    flagged_count = row[0] or 0

            stats = {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "flagged_count": flagged_count,
                "by_subject": {
                    row["subject_area"]: row["count"] for row in subject_results
                },
                "period_days": days,
            }

            return stats

        except DB_ERRORS + (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to get conversation statistics: {e}")
            return {}

    def get_conversation_messages(self, conversation_id: str) -> List[Message]:
        """
        Get all messages for a conversation

        Args:
            conversation_id: Conversation identifier

        Returns:
            List of Message objects
        """
        try:
            rows = self.db.execute_query(
                """
                SELECT message_id, conversation_id, role, content, timestamp,
                       model_used, response_time_ms, tokens_used, safety_filtered
                FROM messages
                WHERE conversation_id = ?
                ORDER BY timestamp ASC
                """,
                (conversation_id,),
            )

            messages = []
            for row in rows:

                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, IndexError, TypeError):
                        return row[idx] if idx < len(row) else None

                timestamp_str = g("timestamp", 4)
                try:
                    timestamp = (
                        datetime.fromisoformat(timestamp_str)
                        if timestamp_str
                        else datetime.now(timezone.utc)
                    )
                except ValueError:
                    timestamp = datetime.now(timezone.utc)

                message = Message(
                    message_id=g("message_id", 0),
                    conversation_id=g("conversation_id", 1),
                    role=g("role", 2),
                    content=self._maybe_decrypt(g("content", 3)),
                    timestamp=timestamp,
                    model_used=g("model_used", 5),
                    response_time_ms=g("response_time_ms", 6),
                    tokens_used=g("tokens_used", 7),
                    safety_filtered=bool(g("safety_filtered", 8)),
                )
                messages.append(message)

            return messages

        except DB_ERRORS + (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to get conversation messages: {e}")
            return []

    def get_conversations_by_date(
        self, profile_id: str, start_date, end_date
    ) -> List[Conversation]:
        """
        Get conversations within a date range

        Args:
            profile_id: Profile identifier
            start_date: Start date (ISO format string or date object)
            end_date: End date (ISO format string or date object)

        Returns:
            List of Conversation objects
        """
        try:
            # Convert date objects to strings if needed
            from datetime import date

            if isinstance(start_date, date):
                start_date_str = start_date.isoformat()
            else:
                start_date_str = start_date

            if isinstance(end_date, date):
                # Include the whole end day by adding one day
                from datetime import timedelta

                end_date_obj = end_date + timedelta(days=1)
                end_date_str = end_date_obj.isoformat()
            else:
                end_date_str = end_date

            rows = self.db.execute_query(
                """
                SELECT * FROM conversations
                WHERE profile_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at DESC
                """,
                (profile_id, start_date_str, end_date_str),
            )

            if not rows:
                return []

            # Collect all conversation IDs for bulk message query
            conversation_ids = []
            conversations = []

            for row in rows:

                def g(key, idx):
                    try:
                        return row[key]
                    except (KeyError, IndexError, TypeError):
                        return row[idx] if idx < len(row) else None

                conv_id = g("conversation_id", 0)
                conversation_ids.append(conv_id)

                created_str = g("created_at", 3)
                updated_str = g("updated_at", 4)

                try:
                    created_at = (
                        datetime.fromisoformat(created_str)
                        if created_str
                        else datetime.now(timezone.utc)
                    )
                    updated_at = (
                        datetime.fromisoformat(updated_str)
                        if updated_str
                        else datetime.now(timezone.utc)
                    )
                except ValueError:
                    created_at = updated_at = datetime.now(timezone.utc)

                conversation = Conversation(
                    conversation_id=conv_id,
                    session_id=g("session_id", 1),
                    profile_id=g("profile_id", 2),
                    created_at=created_at,
                    updated_at=updated_at,
                    message_count=g("message_count", 5) or 0,
                    subject_area=g("subject_area", 6),
                    is_flagged=bool(g("is_flagged", 7)),
                    flag_reason=g("flag_reason", 8),
                    messages=[],  # Will be filled by bulk query
                )
                conversations.append(conversation)

            # Bulk query: Get ALL messages for ALL conversations in ONE query
            # Check if there are any conversations first to avoid empty IN clause
            if conversation_ids:
                placeholders = ",".join("?" * len(conversation_ids))
                all_messages_rows = self.db.execute_query(
                    f"""
                    SELECT * FROM messages
                    WHERE conversation_id IN ({placeholders})
                    ORDER BY conversation_id, timestamp ASC
                    """,
                    tuple(conversation_ids),
                )
            else:
                # No conversations, skip message query
                all_messages_rows = []

            # Group messages by conversation_id
            messages_by_conv: dict = {}
            for msg_row in all_messages_rows:

                def gm(key, idx):
                    try:
                        return msg_row[key]
                    except (KeyError, IndexError, TypeError):
                        return msg_row[idx] if idx < len(msg_row) else None

                msg_conv_id = gm("conversation_id", 1)

                # Parse timestamp
                timestamp_str = gm("timestamp", 4)
                if timestamp_str:
                    try:
                        msg_timestamp = datetime.fromisoformat(timestamp_str)
                    except (ValueError, TypeError):
                        msg_timestamp = datetime.now(timezone.utc)
                else:
                    msg_timestamp = datetime.now(timezone.utc)

                message = Message(
                    message_id=gm("message_id", 0),
                    conversation_id=msg_conv_id,
                    role=gm("role", 2),
                    content=self._maybe_decrypt(gm("content", 3)),
                    timestamp=msg_timestamp,
                    model_used=gm("model_used", 5),
                    response_time_ms=gm("response_time_ms", 6),
                    tokens_used=gm("tokens_used", 7) or 0,
                    safety_filtered=bool(gm("safety_filtered", 8)),
                )

                if msg_conv_id not in messages_by_conv:
                    messages_by_conv[msg_conv_id] = []
                messages_by_conv[msg_conv_id].append(message)

            # Assign messages to their conversations
            for conversation in conversations:
                conversation.messages = messages_by_conv.get(
                    conversation.conversation_id, []
                )

            return conversations

        except DB_ERRORS + (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to get conversations by date: {e}")
            return []


# Singleton instance
conversation_store = ConversationStore()


# Export public interface
__all__ = ["ConversationStore", "Conversation", "Message", "conversation_store"]
