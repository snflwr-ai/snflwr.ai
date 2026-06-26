# storage/conversation_store/__init__.py  (was conversation_store.py — decomposed)
"""
Conversation Storage and Retrieval System
Manages chat history with encryption, search, and export capabilities
"""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import safety_config
from storage.database import db_manager
from storage.db_adapters import DB_ERRORS
from storage.encryption import encryption_manager
from utils.logger import get_logger

logger = get_logger(__name__)

from storage.conversation_store.models import Conversation, Message
from storage.conversation_store.queries import _ConversationQueryMixin


class ConversationStore(_ConversationQueryMixin):
    """Encrypted conversation persistence + search.

    Read/query/export methods live in _ConversationQueryMixin; write and
    lifecycle methods are below.
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


conversation_store = ConversationStore()


# Export public interface
__all__ = ["ConversationStore", "Conversation", "Message", "conversation_store"]
