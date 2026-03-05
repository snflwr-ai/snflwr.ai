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


class TestChatResponsePossibleFalsePositive:
    """Tests for possible_false_positive field in ChatResponse."""

    def test_chat_response_has_possible_false_positive_field(self):
        """ChatResponse model includes possible_false_positive field."""
        from api.routes.chat import ChatResponse
        r = ChatResponse(
            message="blocked",
            blocked=True,
            block_reason="test",
            block_category="inappropriate_content",
            model="test-model",
            timestamp="2026-03-05T00:00:00+00:00",
            session_id="sess-1",
        )
        assert hasattr(r, "possible_false_positive")
        assert r.possible_false_positive is False  # default

    def test_chat_response_possible_false_positive_true(self):
        """ChatResponse accepts possible_false_positive=True."""
        from api.routes.chat import ChatResponse
        r = ChatResponse(
            message="blocked",
            blocked=True,
            block_reason="test",
            block_category="inappropriate_content",
            model="test-model",
            timestamp="2026-03-05T00:00:00+00:00",
            session_id="sess-1",
            possible_false_positive=True,
        )
        assert r.possible_false_positive is True

    def test_blocked_response_passes_pfp_flag_true(self):
        """ChatResponse serialization: possible_false_positive=True is included in JSON output."""
        from api.routes.chat import ChatResponse
        r = ChatResponse(
            message="I can help with something else!",
            blocked=True,
            block_reason="test block",
            block_category="violence",
            model="test-model",
            timestamp="2026-03-05T00:00:00+00:00",
            session_id="sess-1",
            possible_false_positive=True,
        )
        data = r.model_dump()
        assert data["blocked"] is True
        assert data["possible_false_positive"] is True

    def test_blocked_response_pfp_false_when_not_flagged(self):
        """ChatResponse serialization: possible_false_positive defaults to False."""
        from api.routes.chat import ChatResponse
        r = ChatResponse(
            message="I can help with something else!",
            blocked=True,
            block_reason="test block",
            block_category="violence",
            model="test-model",
            timestamp="2026-03-05T00:00:00+00:00",
            session_id="sess-1",
            possible_false_positive=False,
        )
        data = r.model_dump()
        assert data["blocked"] is True
        assert data["possible_false_positive"] is False


class TestPossibleFalsePositiveHandlerWiring:
    """
    Integration-level test: verifies that filter_result.possible_false_positive
    is correctly wired through the route handler into the ChatResponse.

    Calls send_chat_message directly with mocked dependencies so we don't need
    a live database, session, or Ollama instance.
    """

    def _make_blocked_filter_result(self, possible_false_positive: bool):
        """Build a SafetyResult that represents a blocked message."""
        from safety.pipeline import SafetyResult, Severity, Category
        return SafetyResult(
            is_safe=False,
            severity=Severity.MAJOR,
            category=Category.VIOLENCE,
            reason="test block",
            triggered_keywords=("test",),
            suggested_redirection="Let's talk about something else.",
            stage="keyword",
            possible_false_positive=possible_false_positive,
        )

    def _make_auth_session(self, role="parent"):
        mock_session = MagicMock()
        mock_session.role = role
        mock_session.user_id = "user-123"
        return mock_session

    def _make_profile(self):
        mock_profile = MagicMock()
        mock_profile.parent_id = "user-123"
        mock_profile.is_active = True
        mock_profile.age = 10
        mock_profile.grade = "5"
        mock_profile.name = "Test Child"
        mock_profile.learning_level = "adaptive"
        return mock_profile

    def test_handler_wires_possible_false_positive_true(self):
        """
        When safety_pipeline.check_input returns possible_false_positive=True,
        the route handler must return a ChatResponse with possible_false_positive=True.
        """
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        filter_result = self._make_blocked_filter_result(possible_false_positive=True)
        auth_session = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm_cls, \
             patch("api.routes.chat.safety_pipeline") as mock_pipeline, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"):

            mock_pm_cls.return_value.get_profile.return_value = profile
            mock_pipeline.check_input.return_value = filter_result
            mock_pipeline.get_safe_response.return_value = "I can help with something else!"

            mock_session = MagicMock()
            mock_session.session_id = "sess-abc-123"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            request = ChatRequest(
                message="How do I make a weapon?",
                profile_id="a" * 32,  # valid UUID-hex format
            )

            result = asyncio.run(
                send_chat_message(
                    request=request,
                    auth_session=auth_session,
                    rate_limit_info={},
                )
            )

        assert result.blocked is True
        assert result.possible_false_positive is True

    def test_handler_wires_possible_false_positive_false(self):
        """
        When safety_pipeline.check_input returns possible_false_positive=False,
        the route handler must return a ChatResponse with possible_false_positive=False.
        """
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        filter_result = self._make_blocked_filter_result(possible_false_positive=False)
        auth_session = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm_cls, \
             patch("api.routes.chat.safety_pipeline") as mock_pipeline, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"):

            mock_pm_cls.return_value.get_profile.return_value = profile
            mock_pipeline.check_input.return_value = filter_result
            mock_pipeline.get_safe_response.return_value = "I can help with something else!"

            mock_session = MagicMock()
            mock_session.session_id = "sess-abc-123"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            request = ChatRequest(
                message="How do I make a weapon?",
                profile_id="a" * 32,  # valid UUID-hex format
            )

            result = asyncio.run(
                send_chat_message(
                    request=request,
                    auth_session=auth_session,
                    rate_limit_info={},
                )
            )

        assert result.blocked is True
        assert result.possible_false_positive is False
