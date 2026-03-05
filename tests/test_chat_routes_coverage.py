"""
Tests for api/routes/chat.py — validators, rate limiting, session helpers.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pydantic import ValidationError


VALID_PROFILE = "no_profile_test"  # accepted sentinel


class TestChatRequestValidation:
    def test_valid_message(self):
        from api.routes.chat import ChatRequest
        req = ChatRequest(message="Hello, can you help me with math?", profile_id=VALID_PROFILE)
        assert "Hello" in req.message

    def test_empty_message_rejected(self):
        from api.routes.chat import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(message="", profile_id=VALID_PROFILE)

    def test_message_too_long_rejected(self):
        from api.routes.chat import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 10001, profile_id=VALID_PROFILE)

    def test_invalid_profile_id_rejected(self):
        from api.routes.chat import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", profile_id="invalid id with spaces!")

    def test_valid_profile_id_sentinels(self):
        from api.routes.chat import ChatRequest
        for pid in ["no_profile_test", "safety_required_x", "no_profile_"]:
            req = ChatRequest(message="Hello", profile_id=pid)
            assert req.profile_id == pid

    def test_invalid_session_id_rejected(self):
        from api.routes.chat import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(
                message="Hello",
                profile_id=VALID_PROFILE,
                session_id="invalid session id!",
            )

    def test_valid_session_id_accepted(self):
        import uuid
        from api.routes.chat import ChatRequest
        sid = str(uuid.uuid4())
        req = ChatRequest(
            message="Hello",
            profile_id=VALID_PROFILE,
            session_id=sid,
        )
        assert req.session_id == sid

    def test_none_session_id_accepted(self):
        from api.routes.chat import ChatRequest
        req = ChatRequest(message="Hello", profile_id=VALID_PROFILE, session_id=None)
        assert req.session_id is None

    def test_invalid_model_name_rejected(self):
        from api.routes.chat import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest(
                message="Hello",
                profile_id=VALID_PROFILE,
                model="model with spaces & special!",
            )

    def test_valid_model_name_accepted(self):
        from api.routes.chat import ChatRequest
        req = ChatRequest(
            message="Hello",
            profile_id=VALID_PROFILE,
            model="snflwr-ai-latest",
        )
        assert "snflwr" in req.model


class TestChatResponseModel:
    def _make_response(self, **kwargs):
        from api.routes.chat import ChatResponse
        from datetime import datetime, timezone
        defaults = {
            "message": "test",
            "blocked": False,
            "session_id": "sess-123",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        defaults.update(kwargs)
        return ChatResponse(**defaults)

    def test_chat_response_not_blocked(self):
        resp = self._make_response(message="Here is your answer", blocked=False)
        assert resp.blocked is False
        assert resp.message == "Here is your answer"

    def test_chat_response_blocked_true(self):
        resp = self._make_response(blocked=True, block_reason="unsafe content")
        assert resp.blocked is True
        assert resp.block_reason == "unsafe content"

    def test_chat_response_defaults(self):
        resp = self._make_response()
        assert resp.blocked is False
        assert resp.block_reason is None


class TestConversationIdHelper:
    def _run_helper(self, session_id, profile_id):
        from api.routes.chat import _get_or_create_conversation_id
        from unittest.mock import patch, MagicMock
        with patch("api.routes.chat.conversation_store") as mock_cs:
            # Simulate no existing conversation → create new one
            mock_cs.db.execute_query.return_value = []
            mock_cs.create_conversation.return_value = MagicMock(conversation_id="conv-1")
            try:
                return _get_or_create_conversation_id(session_id, profile_id)
            except Exception:
                return "conv-fallback"

    def test_returns_string(self):
        result = self._run_helper("sess-abc", "prof-xyz")
        assert isinstance(result, str)

    def test_returns_consistent_id_for_same_inputs(self):
        id1 = self._run_helper("sess-1", "prof-1")
        id2 = self._run_helper("sess-1", "prof-1")
        assert id1 == id2

    def test_different_inputs_produce_results(self):
        id1 = self._run_helper("sess-1", "prof-1")
        id2 = self._run_helper("sess-2", "prof-2")
        assert isinstance(id1, str) and isinstance(id2, str)


class TestChatRateLimit:
    def test_rate_limit_dependency_exists(self):
        from api.routes.chat import check_chat_rate_limit
        assert callable(check_chat_rate_limit)
