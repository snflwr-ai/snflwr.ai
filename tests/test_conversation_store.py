"""
Test Suite for ConversationStore
Covers: search_conversations, get_conversation_messages, get_conversations_by_date,
        get_statistics, update/delete conversation, error paths, encryption, g() helper.
"""

import sys

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch, call

from storage.conversation_store import ConversationStore, Conversation, Message

# Grab the actual module object so patch.object bypasses the name collision
# between the ``storage.conversation_store`` *submodule* and the
# ``conversation_store`` *instance* re-exported by ``storage/__init__.py``.
# On Python 3.10 the instance wins when resolving the dotted patch path,
# causing AttributeError.  Using the module directly avoids the ambiguity.
_cs_module = sys.modules["storage.conversation_store"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _conv_dict(conv_id="conv-1", session_id="sess-1", profile_id="prof-1",
               subject_area="math", is_flagged=False, flag_reason=None,
               message_count=3, now_iso=None):
    now_iso = now_iso or _make_now_iso()
    return {
        "conversation_id": conv_id,
        "session_id": session_id,
        "profile_id": profile_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "message_count": message_count,
        "subject_area": subject_area,
        "is_flagged": is_flagged,
        "flag_reason": flag_reason,
    }


def _msg_dict(msg_id="msg-1", conv_id="conv-1", role="user",
              content="hello", now_iso=None, model_used=None,
              response_time_ms=None, tokens_used=None, safety_filtered=False):
    now_iso = now_iso or _make_now_iso()
    return {
        "message_id": msg_id,
        "conversation_id": conv_id,
        "role": role,
        "content": content,
        "timestamp": now_iso,
        "model_used": model_used,
        "response_time_ms": response_time_ms,
        "tokens_used": tokens_used,
        "safety_filtered": safety_filtered,
    }


def _conv_tuple(conv_id="conv-1", session_id="sess-1", profile_id="prof-1",
                now_iso=None, message_count=2, subject_area="science",
                is_flagged=0, flag_reason=None):
    now_iso = now_iso or _make_now_iso()
    # Matches SELECT * column order used in get_conversations_by_date:
    # conversation_id(0), session_id(1), profile_id(2), created_at(3),
    # updated_at(4), message_count(5), subject_area(6), is_flagged(7), flag_reason(8)
    return (conv_id, session_id, profile_id, now_iso, now_iso,
            message_count, subject_area, is_flagged, flag_reason)


def _msg_tuple(msg_id="msg-1", conv_id="conv-1", role="user",
               content="tuple content", now_iso=None, model_used=None,
               response_time_ms=None, tokens_used=0, safety_filtered=0):
    now_iso = now_iso or _make_now_iso()
    # message_id(0), conversation_id(1), role(2), content(3), timestamp(4),
    # model_used(5), response_time_ms(6), tokens_used(7), safety_filtered(8)
    return (msg_id, conv_id, role, content, now_iso,
            model_used, response_time_ms, tokens_used, safety_filtered)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_query.return_value = []
    db.execute_write.return_value = None
    return db


@pytest.fixture
def mock_enc():
    enc = MagicMock()
    enc.decrypt_string.side_effect = lambda s: s  # identity by default
    enc.encrypt_string.side_effect = lambda s: f"ENC[{s}]"
    enc.hmac_token.side_effect = lambda t: f"HASH:{t}"
    return enc


@pytest.fixture
def store(mock_db, mock_enc):
    """ConversationStore with mock DB and encryption, encryption disabled."""
    with patch.object(_cs_module, "safety_config") as mock_cfg:
        mock_cfg.ENCRYPT_CONVERSATIONS = False
        s = ConversationStore(db=mock_db, encryption=mock_enc)
        yield s, mock_db, mock_enc, mock_cfg


@pytest.fixture
def store_enc(mock_db, mock_enc):
    """ConversationStore with mock DB and encryption, encryption enabled."""
    with patch.object(_cs_module, "safety_config") as mock_cfg:
        mock_cfg.ENCRYPT_CONVERSATIONS = True
        s = ConversationStore(db=mock_db, encryption=mock_enc)
        yield s, mock_db, mock_enc, mock_cfg


# ===========================================================================
# 1. _tokenize
# ===========================================================================

class TestTokenize:
    def test_basic_words(self):
        tokens = ConversationStore._tokenize("Hello World")
        assert "hello" in tokens
        assert "world" in tokens

    def test_short_words_excluded(self):
        tokens = ConversationStore._tokenize("Hi a an the")
        # "the" is 3 chars — included; "hi", "a", "an" are too short
        assert "hi" not in tokens
        assert "a" not in tokens
        assert "an" not in tokens
        assert "the" in tokens

    def test_deduplication(self):
        tokens = ConversationStore._tokenize("cat cat cat")
        assert tokens == {"cat"}

    def test_punctuation_stripped(self):
        tokens = ConversationStore._tokenize("Hello, world! Testing.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "testing" in tokens

    def test_empty_string(self):
        tokens = ConversationStore._tokenize("")
        assert tokens == set()

    def test_numbers_and_words(self):
        tokens = ConversationStore._tokenize("abc 123 test")
        assert "abc" in tokens
        assert "123" in tokens
        assert "test" in tokens


# ===========================================================================
# 2. _maybe_decrypt
# ===========================================================================

class TestMaybeDecrypt:
    """
    _maybe_decrypt reads safety_config.ENCRYPT_CONVERSATIONS at call time.
    Keep the patch active across both construction and the method call.
    """

    def test_returns_empty_string_for_empty_input(self, mock_db, mock_enc):
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("")
        assert result == ""

    def test_returns_stored_value_when_no_encryption_manager(self, mock_db):
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=None)
            result = s._maybe_decrypt("some text")
        assert result == "some text"

    def test_decrypt_success(self, mock_db, mock_enc):
        mock_enc.decrypt_string.return_value = "plaintext"
        mock_enc.decrypt_string.side_effect = None  # override fixture default
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("ciphertext")
        assert result == "plaintext"

    def test_decrypt_returns_none_enc_off_returns_stored(self, mock_db, mock_enc):
        """If decrypt_string returns None and encryption is OFF, return stored value."""
        mock_enc.decrypt_string.return_value = None
        mock_enc.decrypt_string.side_effect = None
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("raw text")
        assert result == "raw text"

    def test_decrypt_returns_none_enc_on_returns_sentinel(self, mock_db, mock_enc):
        """If decrypt_string returns None and ENCRYPT_CONVERSATIONS is on, return sentinel."""
        mock_enc.decrypt_string.return_value = None
        mock_enc.decrypt_string.side_effect = None
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = True
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("raw text")
        assert result == "[Message could not be decrypted]"

    def test_decrypt_value_error_enc_off_fallback(self, mock_db, mock_enc):
        """ValueError during decryption with ENCRYPT_OFF → return stored value."""
        mock_enc.decrypt_string.side_effect = ValueError("bad data")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("stored text")
        assert result == "stored text"

    def test_decrypt_value_error_enc_on_returns_sentinel(self, mock_db, mock_enc):
        """ValueError during decryption with ENCRYPT_ON → return sentinel."""
        mock_enc.decrypt_string.side_effect = ValueError("bad data")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = True
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("stored text")
        assert result == "[Message could not be decrypted]"

    def test_decrypt_general_exception_enc_on(self, mock_db, mock_enc):
        """Unexpected exception with ENCRYPT_ON → return sentinel."""
        mock_enc.decrypt_string.side_effect = RuntimeError("key mismatch")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = True
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("ciphertext")
        assert result == "[Message could not be decrypted]"

    def test_decrypt_general_exception_enc_off_fallback(self, mock_db, mock_enc):
        """Unexpected exception with ENCRYPT_OFF → return stored text."""
        mock_enc.decrypt_string.side_effect = RuntimeError("unexpected")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt("fallback text")
        assert result == "fallback text"

    def test_none_stored_returns_none(self, mock_db, mock_enc):
        """None input passes through without calling decrypt."""
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            s = ConversationStore(db=mock_db, encryption=mock_enc)
            result = s._maybe_decrypt(None)
        assert result is None
        mock_enc.decrypt_string.assert_not_called()


# ===========================================================================
# 3. create_conversation
# ===========================================================================

class TestCreateConversation:
    def test_returns_conversation_object(self, store):
        s, db, enc, cfg = store
        conv = s.create_conversation("sess-1", "prof-1", "math")
        assert isinstance(conv, Conversation)
        assert conv.session_id == "sess-1"
        assert conv.profile_id == "prof-1"
        assert conv.subject_area == "math"
        assert conv.message_count == 0
        assert conv.messages == []

    def test_executes_db_insert(self, store):
        s, db, enc, cfg = store
        s.create_conversation("sess-x", "prof-x")
        # Should have been called for the INSERT at minimum
        assert any(
            "INSERT INTO conversations" in str(c)
            for c in db.execute_write.call_args_list
        )

    def test_conversation_id_is_hex(self, store):
        s, db, enc, cfg = store
        conv = s.create_conversation("sess-1", "prof-1")
        assert len(conv.conversation_id) == 32
        int(conv.conversation_id, 16)  # must be valid hex


# ===========================================================================
# 4. add_message
# ===========================================================================

class TestAddMessage:
    def test_returns_message_with_original_content(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = [{"profile_id": "prof-1"}]
        msg = s.add_message("conv-1", "user", "hello world")
        assert msg.role == "user"
        assert msg.content == "hello world"
        assert msg.conversation_id == "conv-1"

    def test_profile_lookup_as_dict(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = [{"profile_id": "prof-1"}]
        msg = s.add_message("conv-1", "assistant", "response", model_used="qwen")
        assert msg.model_used == "qwen"

    def test_profile_lookup_as_tuple(self, store):
        """Profile row returned as tuple — must not crash."""
        s, db, enc, cfg = store
        db.execute_query.return_value = [("prof-tuple",)]
        msg = s.add_message("conv-1", "user", "tuple test")
        assert msg.content == "tuple test"

    def test_profile_lookup_empty(self, store):
        """Empty query result — no crash, message still returned."""
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        msg = s.add_message("conv-1", "user", "no profile")
        assert msg.role == "user"

    def test_profile_lookup_empty_tuple_row(self, store):
        """Tuple row with length 0 — profile_id becomes None, no crash."""
        s, db, enc, cfg = store
        # Return a row that is a non-dict, non-empty sequence but its first element is falsy
        # to hit the else: profile_id = None branch
        db.execute_query.return_value = [()]  # empty tuple → hits line 304
        msg = s.add_message("conv-1", "user", "empty tuple")
        assert msg.role == "user"

    def test_db_error_on_profile_update_is_swallowed(self, store):
        """DB error when updating last_active must not raise."""
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("table locked")
        msg = s.add_message("conv-1", "user", "content")
        assert msg.content == "content"

    def test_encryption_enabled_stores_ciphertext(self, store_enc):
        s, db, enc, cfg = store_enc
        db.execute_query.return_value = [{"profile_id": "prof-1"}]
        s.add_message("conv-1", "user", "secret message")
        # Find the INSERT INTO messages call
        insert_call = None
        for c in db.execute_write.call_args_list:
            sql = c[0][0] if c[0] else ""
            if "INSERT INTO messages" in sql:
                insert_call = c
                break
        assert insert_call is not None
        stored_content = insert_call[0][1][3]
        assert stored_content == "ENC[secret message]"

    def test_encryption_failure_raises_runtime_error(self, mock_db):
        broken_enc = MagicMock()
        broken_enc.encrypt_string.side_effect = Exception("key error")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = True
            s = ConversationStore(db=mock_db, encryption=broken_enc)
        with pytest.raises(RuntimeError, match="COPPA compliance"):
            s.add_message("conv-1", "user", "message")

    def test_search_index_populated_when_enc_on(self, store_enc):
        s, db, enc, cfg = store_enc
        db.execute_query.return_value = [{"profile_id": "prof-1"}]
        s.add_message("conv-1", "user", "photosynthesis biology")
        # Should have INSERT INTO message_search_index calls
        index_calls = [
            c for c in db.execute_write.call_args_list
            if "INSERT INTO message_search_index" in str(c)
        ]
        assert len(index_calls) > 0

    def test_search_index_failure_does_not_raise(self, store_enc):
        """Non-fatal: search index failure should not block message storage."""
        s, db, enc, cfg = store_enc
        db.execute_query.return_value = [{"profile_id": "prof-1"}]
        enc.hmac_token.side_effect = Exception("hmac error")
        # Must not raise
        msg = s.add_message("conv-1", "user", "some content")
        assert msg.role == "user"


# ===========================================================================
# 5. get_conversation
# ===========================================================================

class TestGetConversation:
    def test_returns_none_when_not_found(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        result = s.get_conversation("nonexistent")
        assert result is None

    def test_returns_conversation_with_messages(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [_msg_dict(now_iso=now_iso)],
        ]
        conv = s.get_conversation("conv-1")
        assert conv is not None
        assert conv.conversation_id == "conv-1"
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "user"

    def test_returns_conversation_without_messages_when_disabled(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_conv_dict(now_iso=now_iso)]
        conv = s.get_conversation("conv-1", include_messages=False)
        assert conv is not None
        assert conv.messages == []

    def test_db_error_returns_none(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("db error")
        result = s.get_conversation("conv-1")
        assert result is None

    def test_decrypts_message_content(self, store_enc):
        s, db, enc, cfg = store_enc
        now_iso = _make_now_iso()
        enc.decrypt_string.side_effect = None  # clear identity side_effect
        enc.decrypt_string.return_value = "decrypted text"
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [_msg_dict(content="CIPHERTEXT", now_iso=now_iso)],
        ]
        conv = s.get_conversation("conv-1")
        assert conv.messages[0].content == "decrypted text"

    def test_conversation_is_flagged(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_dict(is_flagged=True, flag_reason="inappropriate", now_iso=now_iso)],
            [],
        ]
        conv = s.get_conversation("conv-1")
        assert conv.is_flagged is True
        assert conv.flag_reason == "inappropriate"


# ===========================================================================
# 6. get_profile_conversations
# ===========================================================================

class TestGetProfileConversations:
    def test_returns_list(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [
            _conv_dict(conv_id="conv-1", now_iso=now_iso),
            _conv_dict(conv_id="conv-2", now_iso=now_iso),
        ]
        convs = s.get_profile_conversations("prof-1")
        assert len(convs) == 2
        assert all(isinstance(c, Conversation) for c in convs)

    def test_returns_empty_list_on_error(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("fail")
        result = s.get_profile_conversations("prof-1")
        assert result == []

    def test_pagination_params_passed_to_db(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        s.get_profile_conversations("prof-1", limit=10, offset=20)
        call_args = db.execute_query.call_args
        params = call_args[0][1]
        assert 10 in params
        assert 20 in params


# ===========================================================================
# 7. search_conversations
# ===========================================================================

class TestSearchConversations:
    def test_no_search_text_returns_all(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_conv_dict(now_iso=now_iso)]
        result = s.search_conversations("prof-1")
        assert len(result) == 1

    def test_search_text_unencrypted_uses_like(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_conv_dict(now_iso=now_iso)]
        result = s.search_conversations("prof-1", search_text="math")
        call_sql = db.execute_query.call_args[0][0]
        assert "LIKE" in call_sql

    def test_search_text_encrypted_uses_hmac_index(self, store_enc):
        s, db, enc, cfg = store_enc
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_conv_dict(now_iso=now_iso)]
        result = s.search_conversations("prof-1", search_text="photosynthesis")
        call_sql = db.execute_query.call_args[0][0]
        assert "message_search_index" in call_sql

    def test_encrypted_search_empty_tokens_returns_empty(self, store_enc):
        s, db, enc, cfg = store_enc
        # Only 1-2 char words → no tokens after tokenize
        result = s.search_conversations("prof-1", search_text="a b")
        assert result == []
        # DB should NOT have been called for query (empty token set short-circuits)
        # The only calls are from __init__ CREATE TABLE/INDEX statements
        for c in db.execute_query.call_args_list:
            assert "message_search_index" not in str(c)

    def test_subject_area_filter_appended(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        s.search_conversations("prof-1", subject_area="math")
        call_sql = db.execute_query.call_args[0][0]
        assert "subject_area" in call_sql

    def test_flagged_only_filter_appended(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        s.search_conversations("prof-1", flagged_only=True)
        call_sql = db.execute_query.call_args[0][0]
        assert "is_flagged" in call_sql

    def test_subject_and_search_combined(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_conv_dict(now_iso=now_iso)]
        result = s.search_conversations("prof-1", search_text="water", subject_area="science")
        assert isinstance(result, list)

    def test_returns_empty_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("error")
        result = s.search_conversations("prof-1", search_text="anything")
        assert result == []

    def test_tuple_row_format(self, store):
        """Rows returned as tuples should be parsed correctly via g() helper."""
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        # Return tuple row — g() falls back to index access
        db.execute_query.return_value = [
            _conv_tuple(now_iso=now_iso)
        ]
        result = s.search_conversations("prof-1")
        assert len(result) == 1
        assert result[0].conversation_id == "conv-1"
        assert result[0].session_id == "sess-1"

    def test_special_chars_escaped_in_like_query(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        s.search_conversations("prof-1", search_text="50% off_sale")
        call_params = db.execute_query.call_args[0][1]
        # The % and _ in search text must be escaped
        like_param = str(call_params)
        assert "\\%" in like_param
        assert "\\_" in like_param

    def test_limit_appended_to_query(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        s.search_conversations("prof-1")
        call_sql = db.execute_query.call_args[0][0]
        assert "LIMIT 20" in call_sql


# ===========================================================================
# 8. flag_conversation
# ===========================================================================

class TestFlagConversation:
    def test_returns_true_on_success(self, store):
        s, db, enc, cfg = store
        result = s.flag_conversation("conv-1", "inappropriate content")
        assert result is True

    def test_executes_update(self, store):
        s, db, enc, cfg = store
        s.flag_conversation("conv-1", "reason")
        assert any("UPDATE conversations" in str(c) for c in db.execute_write.call_args_list)

    def test_returns_false_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_write.side_effect = sqlite3.OperationalError("locked")
        result = s.flag_conversation("conv-1", "reason")
        assert result is False


# ===========================================================================
# 9. delete_conversation
# ===========================================================================

class TestDeleteConversation:
    def test_returns_true_on_success(self, store):
        s, db, enc, cfg = store
        result = s.delete_conversation("conv-1")
        assert result is True

    def test_executes_delete_query(self, store):
        s, db, enc, cfg = store
        s.delete_conversation("conv-1")
        assert any("DELETE FROM conversations" in str(c)
                   for c in db.execute_write.call_args_list)

    def test_returns_false_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_write.side_effect = sqlite3.OperationalError("db locked")
        result = s.delete_conversation("conv-1")
        assert result is False


# ===========================================================================
# 10. export_conversation
# ===========================================================================

class TestExportConversation:
    def _make_conversation(self, conv_id="conv-1"):
        now = datetime.now(timezone.utc)
        msg = Message(
            message_id="msg-1",
            conversation_id=conv_id,
            role="user",
            content="What is 2+2?",
            timestamp=now,
        )
        return Conversation(
            conversation_id=conv_id,
            session_id="sess-1",
            profile_id="prof-1",
            created_at=now,
            updated_at=now,
            message_count=1,
            subject_area="math",
            is_flagged=False,
            flag_reason=None,
            messages=[msg],
        )

    def test_export_json(self, store):
        s, db, enc, cfg = store
        conv = self._make_conversation()
        with patch.object(s, "get_conversation", return_value=conv):
            result = s.export_conversation("conv-1", format="json")
        assert result is not None
        import json
        data = json.loads(result)
        assert data["conversation_id"] == "conv-1"

    def test_export_txt(self, store):
        s, db, enc, cfg = store
        conv = self._make_conversation()
        with patch.object(s, "get_conversation", return_value=conv):
            result = s.export_conversation("conv-1", format="txt")
        assert result is not None
        assert "USER:" in result
        assert "What is 2+2?" in result

    def test_export_markdown(self, store):
        s, db, enc, cfg = store
        conv = self._make_conversation()
        with patch.object(s, "get_conversation", return_value=conv):
            result = s.export_conversation("conv-1", format="markdown")
        assert result is not None
        assert "# Conversation Export" in result

    def test_export_unknown_format_returns_none(self, store):
        s, db, enc, cfg = store
        conv = self._make_conversation()
        with patch.object(s, "get_conversation", return_value=conv):
            result = s.export_conversation("conv-1", format="pdf")
        assert result is None

    def test_export_not_found_returns_none(self, store):
        s, db, enc, cfg = store
        with patch.object(s, "get_conversation", return_value=None):
            result = s.export_conversation("nonexistent")
        assert result is None

    def test_export_db_error_returns_none(self, store):
        """Exception during get_conversation → export_conversation returns None."""
        s, db, enc, cfg = store
        with patch.object(s, "get_conversation", side_effect=ValueError("bad data")):
            result = s.export_conversation("conv-1", format="json")
        assert result is None


# ===========================================================================
# 11. get_statistics
# ===========================================================================

class TestGetStatistics:
    def test_returns_dict_with_expected_keys(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = [
            [{"total": 5, "total_messages": 20}],
            [{"subject_area": "math", "count": 3}],
            [{"flagged_count": 1}],
        ]
        stats = s.get_statistics("prof-1", days=30)
        assert "total_conversations" in stats
        assert "total_messages" in stats
        assert "flagged_count" in stats
        assert "by_subject" in stats
        assert "period_days" in stats

    def test_correct_values_from_dict_rows(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = [
            [{"total": 7, "total_messages": 42}],
            [{"subject_area": "science", "count": 4}, {"subject_area": "math", "count": 3}],
            [{"flagged_count": 2}],
        ]
        stats = s.get_statistics("prof-1")
        assert stats["total_conversations"] == 7
        assert stats["total_messages"] == 42
        assert stats["flagged_count"] == 2
        assert stats["by_subject"]["science"] == 4

    def test_tuple_rows_for_totals(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = [
            [(10, 50)],        # total, total_messages as tuple
            [],                # subjects
            [(3,)],            # flagged_count as tuple
        ]
        stats = s.get_statistics("prof-1")
        assert stats["total_conversations"] == 10
        assert stats["total_messages"] == 50
        assert stats["flagged_count"] == 3

    def test_null_values_handled_as_zero(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = [
            [{"total": None, "total_messages": None}],
            [],
            [{"flagged_count": None}],
        ]
        stats = s.get_statistics("prof-1")
        assert stats["total_conversations"] == 0
        assert stats["total_messages"] == 0
        assert stats["flagged_count"] == 0

    def test_empty_results_gives_zeros(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        stats = s.get_statistics("prof-1")
        assert stats["total_conversations"] == 0
        assert stats["flagged_count"] == 0

    def test_returns_empty_dict_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("error")
        result = s.get_statistics("prof-1")
        assert result == {}

    def test_period_days_reflected(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = [
            [{"total": 0, "total_messages": 0}],
            [],
            [{"flagged_count": 0}],
        ]
        stats = s.get_statistics("prof-1", days=90)
        assert stats["period_days"] == 90


# ===========================================================================
# 12. get_conversation_messages
# ===========================================================================

class TestGetConversationMessages:
    def test_returns_list_of_messages(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [
            _msg_dict(msg_id="m1", now_iso=now_iso),
            _msg_dict(msg_id="m2", role="assistant", content="response", now_iso=now_iso),
        ]
        msgs = s.get_conversation_messages("conv-1")
        assert len(msgs) == 2
        assert msgs[0].message_id == "m1"
        assert msgs[1].role == "assistant"

    def test_returns_empty_list_on_no_rows(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        msgs = s.get_conversation_messages("conv-1")
        assert msgs == []

    def test_returns_empty_list_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("error")
        result = s.get_conversation_messages("conv-1")
        assert result == []

    def test_decrypts_content(self, store_enc):
        s, db, enc, cfg = store_enc
        now_iso = _make_now_iso()
        enc.decrypt_string.side_effect = None  # clear identity side_effect
        enc.decrypt_string.return_value = "decrypted"
        db.execute_query.return_value = [_msg_dict(content="CIPHER", now_iso=now_iso)]
        msgs = s.get_conversation_messages("conv-1")
        assert msgs[0].content == "decrypted"

    def test_tuple_row_format(self, store):
        """Tuple row support via g() fallback."""
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.return_value = [_msg_tuple(now_iso=now_iso)]
        msgs = s.get_conversation_messages("conv-1")
        assert len(msgs) == 1
        assert msgs[0].message_id == "msg-1"
        assert msgs[0].role == "user"
        assert msgs[0].content == "tuple content"

    def test_invalid_timestamp_falls_back_to_now(self, store):
        s, db, enc, cfg = store
        bad_msg = _msg_dict()
        bad_msg["timestamp"] = "not-a-date"
        db.execute_query.return_value = [bad_msg]
        msgs = s.get_conversation_messages("conv-1")
        assert isinstance(msgs[0].timestamp, datetime)

    def test_none_timestamp_falls_back_to_now(self, store):
        s, db, enc, cfg = store
        bad_msg = _msg_dict()
        bad_msg["timestamp"] = None
        db.execute_query.return_value = [bad_msg]
        msgs = s.get_conversation_messages("conv-1")
        assert isinstance(msgs[0].timestamp, datetime)


# ===========================================================================
# 13. get_conversations_by_date
# ===========================================================================

class TestGetConversationsByDate:
    def test_returns_empty_when_no_rows(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-01-31")
        assert result == []

    def test_accepts_date_objects(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        start = date(2025, 1, 1)
        end = date(2025, 1, 31)
        result = s.get_conversations_by_date("prof-1", start, end)
        assert result == []
        # end_date should be bumped by 1 day
        call_params = db.execute_query.call_args[0][1]
        assert "2025-02-01" in str(call_params)

    def test_accepts_string_dates(self, store):
        s, db, enc, cfg = store
        db.execute_query.return_value = []
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-01-31")
        assert result == []

    def test_dict_rows_parsed_correctly(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [],  # messages bulk query
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert len(result) == 1
        assert result[0].conversation_id == "conv-1"

    def test_tuple_rows_parsed_correctly(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_tuple(now_iso=now_iso)],
            [],  # messages bulk query
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert len(result) == 1
        assert result[0].conversation_id == "conv-1"

    def test_messages_attached_to_conversations(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_dict(conv_id="conv-1", now_iso=now_iso)],
            [_msg_dict(msg_id="m1", conv_id="conv-1", now_iso=now_iso),
             _msg_dict(msg_id="m2", conv_id="conv-1", role="assistant",
                       content="answer", now_iso=now_iso)],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert len(result) == 1
        assert len(result[0].messages) == 2

    def test_messages_with_tuple_rows(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_tuple(conv_id="conv-1", now_iso=now_iso)],
            [_msg_tuple(msg_id="m1", conv_id="conv-1", now_iso=now_iso)],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert len(result[0].messages) == 1

    def test_bulk_query_with_multiple_conversations(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        db.execute_query.side_effect = [
            [_conv_dict(conv_id="conv-1", now_iso=now_iso),
             _conv_dict(conv_id="conv-2", now_iso=now_iso)],
            # Messages: two for conv-1, one for conv-2
            [_msg_dict(msg_id="m1", conv_id="conv-1", now_iso=now_iso),
             _msg_dict(msg_id="m2", conv_id="conv-1", now_iso=now_iso),
             _msg_dict(msg_id="m3", conv_id="conv-2", now_iso=now_iso)],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert len(result) == 2
        conv1 = next(c for c in result if c.conversation_id == "conv-1")
        conv2 = next(c for c in result if c.conversation_id == "conv-2")
        assert len(conv1.messages) == 2
        assert len(conv2.messages) == 1

    def test_invalid_timestamp_fallback(self, store):
        s, db, enc, cfg = store
        row = _conv_dict()
        row["created_at"] = "not-a-date"
        row["updated_at"] = "also-bad"
        db.execute_query.side_effect = [
            [row],
            [],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert isinstance(result[0].created_at, datetime)

    def test_message_invalid_timestamp_fallback(self, store):
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        msg = _msg_dict(now_iso=now_iso)
        msg["timestamp"] = "bad-ts"
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [msg],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert isinstance(result[0].messages[0].timestamp, datetime)

    def test_returns_empty_on_db_error(self, store):
        s, db, enc, cfg = store
        db.execute_query.side_effect = sqlite3.OperationalError("fail")
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert result == []

    def test_no_message_query_when_no_conversations(self, store):
        """When rows is empty, skip the bulk message query entirely (line 924)."""
        s, db, enc, cfg = store
        # First call returns conversations; simulate none found
        db.execute_query.return_value = []
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert result == []
        # Only one execute_query call for the conversations SELECT — no message query
        assert db.execute_query.call_count == 1

    def test_message_with_none_timestamp_in_bulk(self, store):
        """Message row with None timestamp → falls back to now() (line 945)."""
        s, db, enc, cfg = store
        now_iso = _make_now_iso()
        msg_none_ts = _msg_dict(now_iso=now_iso)
        msg_none_ts["timestamp"] = None  # forces the else branch (line 945)
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [msg_none_ts],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert isinstance(result[0].messages[0].timestamp, datetime)

    def test_decrypts_message_content_in_bulk(self, store_enc):
        s, db, enc, cfg = store_enc
        now_iso = _make_now_iso()
        enc.decrypt_string.side_effect = None  # clear identity side_effect
        enc.decrypt_string.return_value = "plain"
        db.execute_query.side_effect = [
            [_conv_dict(now_iso=now_iso)],
            [_msg_dict(content="CIPHER", now_iso=now_iso)],
        ]
        result = s.get_conversations_by_date("prof-1", "2025-01-01", "2025-12-31")
        assert result[0].messages[0].content == "plain"


# ===========================================================================
# 14. _ensure_search_index_table (covered via __init__)
# ===========================================================================

class TestEnsureSearchIndexTable:
    def test_creates_table_on_init(self, mock_db, mock_enc):
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            ConversationStore(db=mock_db, encryption=mock_enc)
        calls = [str(c) for c in mock_db.execute_write.call_args_list]
        assert any("message_search_index" in c for c in calls)

    def test_exception_during_setup_is_swallowed(self, mock_enc):
        bad_db = MagicMock()
        bad_db.execute_write.side_effect = sqlite3.OperationalError("locked")
        with patch("storage.conversation_store.safety_config") as cfg:
            cfg.ENCRYPT_CONVERSATIONS = False
            # Must not raise
            s = ConversationStore(db=bad_db, encryption=mock_enc)
        assert s is not None


# ===========================================================================
# 15. Message and Conversation to_dict
# ===========================================================================

class TestDataclassSerialisation:
    def test_message_to_dict(self):
        now = datetime.now(timezone.utc)
        msg = Message(
            message_id="m1",
            conversation_id="c1",
            role="user",
            content="hello",
            timestamp=now,
            model_used="qwen",
            response_time_ms=100,
            tokens_used=50,
            safety_filtered=False,
        )
        d = msg.to_dict()
        assert d["message_id"] == "m1"
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert d["model_used"] == "qwen"

    def test_conversation_to_dict_includes_messages(self):
        now = datetime.now(timezone.utc)
        msg = Message("m1", "c1", "user", "hello", now)
        conv = Conversation(
            conversation_id="c1",
            session_id="s1",
            profile_id="p1",
            created_at=now,
            updated_at=now,
            message_count=1,
            subject_area="math",
            is_flagged=False,
            flag_reason=None,
            messages=[msg],
        )
        d = conv.to_dict()
        assert d["conversation_id"] == "c1"
        assert len(d["messages"]) == 1
        assert d["messages"][0]["role"] == "user"
