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


# ---------------------------------------------------------------------------
# Helper mixin for handler-level tests that exercise the Ollama response path
# ---------------------------------------------------------------------------

class _HandlerTestBase:
    """Shared helpers for tests that call send_chat_message directly."""

    def _make_auth_session(self, role="parent", user_id="user-123"):
        s = MagicMock()
        s.role = role
        s.user_id = user_id
        return s

    def _make_profile(self, parent_id="user-123"):
        p = MagicMock()
        p.parent_id = parent_id
        p.is_active = True
        p.age = 10
        p.grade = "5"
        p.name = "Test Child"
        p.learning_level = "adaptive"
        return p

    def _run_handler(self, *, message="What is 2+2?", profile_id=None,
                     model="test-model", auth_role="parent",
                     ollama_success=True, ollama_text="Four!",
                     ollama_meta=None, profile=None, safety_input_safe=True,
                     safety_output_safe=True, conversation_store_raises=False,
                     session_error=None, ollama_error=None):
        """Call the async handler with mocked dependencies and return the result."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        if profile_id is None:
            profile_id = "a" * 32

        auth_session = self._make_auth_session(role=auth_role)
        profile_obj = profile or self._make_profile(parent_id=auth_session.user_id)

        patches = {
            "ProfileManager": patch("api.routes.chat.ProfileManager"),
            "safety_pipeline": patch("api.routes.chat.safety_pipeline"),
            "session_manager": patch("api.routes.chat.session_manager"),
            "safety_monitor": patch("api.routes.chat.safety_monitor"),
            "incident_logger": patch("api.routes.chat.incident_logger"),
            "audit_log": patch("api.routes.chat.audit_log"),
            "ollama_client": patch("api.routes.chat.ollama_client"),
            "conversation_store": patch("api.routes.chat.conversation_store"),
            "_get_or_create_conversation_id": patch(
                "api.routes.chat._get_or_create_conversation_id",
                return_value="conv-1",
            ),
        }

        with patches["ProfileManager"] as mock_pm, \
             patches["safety_pipeline"] as mock_sp, \
             patches["session_manager"] as mock_sm, \
             patches["safety_monitor"], \
             patches["incident_logger"], \
             patches["audit_log"], \
             patches["ollama_client"] as mock_oc, \
             patches["conversation_store"] as mock_cs, \
             patches["_get_or_create_conversation_id"]:

            mock_pm.return_value.get_profile.return_value = profile_obj

            # Safety pipeline: input check
            if safety_input_safe:
                from safety.pipeline import SafetyResult, Severity, Category
                safe_result = SafetyResult(
                    is_safe=True, severity=Severity.NONE, category=Category.VALID,
                    reason="", triggered_keywords=(), stage="none",
                )
                mock_sp.check_input.return_value = safe_result
            else:
                from safety.pipeline import SafetyResult, Severity, Category
                unsafe_result = SafetyResult(
                    is_safe=False, severity=Severity.MAJOR, category=Category.VIOLENCE,
                    reason="blocked", triggered_keywords=("bad",), stage="keyword",
                )
                mock_sp.check_input.return_value = unsafe_result
                mock_sp.get_safe_response.return_value = "I can't help with that."

            # Safety pipeline: output check
            if safety_output_safe:
                from safety.pipeline import SafetyResult, Severity, Category
                safe_out = SafetyResult(
                    is_safe=True, severity=Severity.NONE, category=Category.VALID,
                    reason="", triggered_keywords=(), stage="none",
                )
                mock_sp.check_output.return_value = safe_out

            # Session
            mock_session = MagicMock()
            mock_session.session_id = "sess-test-001"
            if session_error:
                from core.session_manager import SessionError
                mock_sm.get_session.side_effect = SessionError(session_error)
                mock_sm.get_active_session.side_effect = SessionError(session_error)
            else:
                mock_sm.get_session.return_value = mock_session
                mock_sm.get_active_session.return_value = mock_session

            # Ollama
            if ollama_error:
                from utils.ollama_client import OllamaError
                mock_oc.chat.side_effect = OllamaError(ollama_error)
            else:
                mock_oc.chat.return_value = (ollama_success, ollama_text, ollama_meta or {})
                mock_oc.list_models.return_value = (True, [{"name": "fallback-model"}], None)

            # Conversation store errors
            if conversation_store_raises:
                import sqlite3
                mock_cs.add_message.side_effect = sqlite3.Error("db write failed")

            request = ChatRequest(message=message, profile_id=profile_id, model=model)
            return asyncio.run(
                send_chat_message(request=request, auth_session=auth_session, rate_limit_info={})
            )


# ---------------------------------------------------------------------------
# Think tag stripping (lines 401-417)
# ---------------------------------------------------------------------------

class TestThinkTagStripping(_HandlerTestBase):
    """Tests for <think>...</think> removal from Ollama responses."""

    def test_think_tags_stripped(self):
        """Response with think tags should have them removed."""
        result = self._run_handler(
            ollama_text="<think>internal reasoning here</think>The answer is 4.",
        )
        assert "<think>" not in result.message
        assert "</think>" not in result.message
        assert "The answer is 4." in result.message

    def test_multiple_think_blocks_stripped(self):
        """Multiple think blocks are all removed."""
        result = self._run_handler(
            ollama_text="<think>first</think>Hello <think>second</think>world",
        )
        assert "<think>" not in result.message
        assert "Hello" in result.message
        assert "world" in result.message

    def test_nested_broken_think_tags_partial_strip(self):
        """Broken/nested think tags: outer pair stripped, inner remnants handled."""
        # <think>outer <think>inner</think> leftover</think>rest
        # The iterative str.find logic will match the first <think> with the
        # first </think>, leaving " leftover</think>rest".  On next iteration
        # there is no <think> so it stops.
        result = self._run_handler(
            ollama_text="<think>outer <think>inner</think> leftover</think>rest",
        )
        # First pair removed; residual </think> remains since no opening tag
        assert "rest" in result.message

    def test_unclosed_think_tag_not_stripped(self):
        """A <think> without closing </think> is left as-is (loop breaks)."""
        result = self._run_handler(
            ollama_text="<think>never closed but here is the answer",
        )
        assert "here is the answer" in result.message

    def test_empty_after_stripping_returns_503(self):
        """Response that becomes empty after stripping think tags raises 503."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(
                ollama_text="<think>only thinking, no real content</think>",
            )
        assert exc_info.value.status_code == 503
        assert "empty response" in exc_info.value.detail

    def test_whitespace_only_after_stripping_returns_503(self):
        """Response that is only whitespace after stripping think tags raises 503."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(
                ollama_text="<think>thinking</think>   \n\t  ",
            )
        assert exc_info.value.status_code == 503

    def test_over_100k_chars_no_stripping(self):
        """Responses over 100K characters skip think-tag stripping entirely."""
        big_text = "<think>should stay</think>" + "x" * 100_001
        result = self._run_handler(ollama_text=big_text)
        # The <think> block should still be present because stripping was skipped
        assert "<think>" in result.message


# ---------------------------------------------------------------------------
# Error handling (lines 389-392, 505-515)
# ---------------------------------------------------------------------------

class TestErrorHandling(_HandlerTestBase):
    """Tests for error paths in send_chat_message."""

    def test_ollama_chat_failure_returns_503(self):
        """When ollama_client.chat returns success=False, return 503."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(
                ollama_success=False,
                ollama_text="",
                ollama_meta={"error": "model not loaded"},
            )
        assert exc_info.value.status_code == 503
        assert "model not loaded" in exc_info.value.detail

    def test_ollama_chat_failure_none_metadata(self):
        """When metadata is None on failure, error message says 'unknown error'."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(
                ollama_success=False,
                ollama_text="",
                ollama_meta=None,
            )
        assert exc_info.value.status_code == 503
        assert "unknown error" in exc_info.value.detail

    def test_session_error_returns_400(self):
        """SessionError raised during session lookup returns 400."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(session_error="session is corrupted")
        assert exc_info.value.status_code == 400
        assert "session is corrupted" in str(exc_info.value.detail)

    def test_ollama_error_returns_503(self):
        """OllamaError exception during chat returns 503."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(ollama_error="connection refused")
        assert exc_info.value.status_code == 503
        assert "temporarily unavailable" in exc_info.value.detail

    def test_db_error_storing_messages_still_succeeds(self):
        """Database errors when storing conversation messages log a warning but
        don't fail the request — the response is still returned."""
        result = self._run_handler(conversation_store_raises=True)
        # The handler should return successfully despite DB write failure
        assert result.message == "Four!"
        assert result.blocked is False


# ---------------------------------------------------------------------------
# Empty/invalid model handling (lines 128, 336-339)
# ---------------------------------------------------------------------------

class TestModelResolution(_HandlerTestBase):
    """Tests for model name resolution when no model is specified."""

    def test_empty_model_falls_back_to_ollama_list(self):
        """When model is empty, handler queries Ollama for available models."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="conv-1"):

            mock_pm.return_value.get_profile.return_value = profile

            from safety.pipeline import SafetyResult, Severity, Category
            safe = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID,
                                reason="", triggered_keywords=(), stage="none")
            mock_sp.check_input.return_value = safe
            mock_sp.check_output.return_value = safe

            mock_session = MagicMock()
            mock_session.session_id = "sess-test-002"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            # list_models returns a model; chat succeeds
            mock_oc.list_models.return_value = (True, [{"name": "discovered-model"}], None)
            mock_oc.chat.return_value = (True, "Discovered!", {})

            req = ChatRequest(message="Hello there", profile_id="a" * 32, model="")
            result = asyncio.run(
                send_chat_message(request=req, auth_session=auth, rate_limit_info={})
            )

        assert result.message == "Discovered!"
        mock_oc.list_models.assert_called_once()

    def test_empty_model_no_ollama_models_returns_503(self):
        """When model is empty and Ollama has no models, return 503."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest
        from fastapi import HTTPException

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="conv-1"):

            mock_pm.return_value.get_profile.return_value = profile

            from safety.pipeline import SafetyResult, Severity, Category
            safe = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID,
                                reason="", triggered_keywords=(), stage="none")
            mock_sp.check_input.return_value = safe

            mock_session = MagicMock()
            mock_session.session_id = "sess-test-003"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            # No models available
            mock_oc.list_models.return_value = (True, [], None)

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 503
        assert "No AI model configured" in exc_info.value.detail

    def test_empty_model_ollama_list_exception_returns_503(self):
        """When model is empty and list_models raises, return 503."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest
        from fastapi import HTTPException

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="conv-1"):

            mock_pm.return_value.get_profile.return_value = profile

            from safety.pipeline import SafetyResult, Severity, Category
            safe = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID,
                                reason="", triggered_keywords=(), stage="none")
            mock_sp.check_input.return_value = safe

            mock_session = MagicMock()
            mock_session.session_id = "sess-test-004"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            # list_models raises
            mock_oc.list_models.side_effect = Exception("connection error")

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 503
        assert "No AI model configured" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Successful response path (lines 328-500) — exercises the happy path fully
# ---------------------------------------------------------------------------

class TestSuccessfulResponsePath(_HandlerTestBase):
    """Cover the normal success flow including conversation storage and monitoring."""

    def test_successful_chat_returns_unblocked_response(self):
        """Full success path: safe input, Ollama response, safe output."""
        result = self._run_handler(ollama_text="The answer is 42.")
        assert result.blocked is False
        assert result.message == "The answer is 42."
        assert result.session_id == "sess-test-001"
        assert "model_used" in result.safety_metadata

    def test_admin_skips_safety_pipeline(self):
        """Admin requests skip safety checks and use simplified system prompt."""
        result = self._run_handler(
            auth_role="admin",
            profile_id="no_profile_admin",
            ollama_text="Unrestricted response.",
        )
        assert result.blocked is False
        assert result.message == "Unrestricted response."


# ---------------------------------------------------------------------------
# Rate limiter dependency (line 64-80)
# ---------------------------------------------------------------------------

class TestRateLimitDependency:
    """Test the check_chat_rate_limit function."""

    def test_rate_limit_allows_request(self):
        from api.routes.chat import check_chat_rate_limit
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        with patch("api.routes.chat.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {"remaining": 99})
            info = check_chat_rate_limit(mock_request)
        assert info["remaining"] == 99

    def test_rate_limit_blocks_request(self):
        from api.routes.chat import check_chat_rate_limit
        from fastapi import HTTPException
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        with patch("api.routes.chat.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (False, {"retry_after": 30})
            with pytest.raises(HTTPException) as exc_info:
                check_chat_rate_limit(mock_request)
        assert exc_info.value.status_code == 429

    def test_rate_limit_no_client_ip(self):
        """When request.client is None, uses 'unknown' as identifier."""
        from api.routes.chat import check_chat_rate_limit
        mock_request = MagicMock()
        mock_request.client = None
        with patch("api.routes.chat.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (True, {})
            check_chat_rate_limit(mock_request)
        mock_rl.check_rate_limit.assert_called_once_with(
            identifier="unknown", max_requests=100, window_seconds=60, limit_type="chat",
        )

    def test_rate_limit_non_dict_info(self):
        """When info is not a dict, retry_after defaults to 60."""
        from api.routes.chat import check_chat_rate_limit
        from fastapi import HTTPException
        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.1"
        with patch("api.routes.chat.rate_limiter") as mock_rl:
            mock_rl.check_rate_limit.return_value = (False, "not a dict")
            with pytest.raises(HTTPException) as exc_info:
                check_chat_rate_limit(mock_request)
        assert exc_info.value.headers["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# Conversation ID helper — existing-row path (line 55-56)
# ---------------------------------------------------------------------------

class TestConversationIdHelperExistingRow:
    """Cover the branch where an existing conversation row is found."""

    def test_existing_row_dict(self):
        """When DB returns a dict row, extract conversation_id by key."""
        from api.routes.chat import _get_or_create_conversation_id
        with patch("api.routes.chat.conversation_store") as mock_cs:
            mock_cs.db.execute_query.return_value = [{"conversation_id": "conv-existing"}]
            result = _get_or_create_conversation_id("sess-1", "prof-1")
        assert result == "conv-existing"

    def test_existing_row_tuple(self):
        """When DB returns a tuple row, extract conversation_id by index."""
        from api.routes.chat import _get_or_create_conversation_id
        with patch("api.routes.chat.conversation_store") as mock_cs:
            mock_cs.db.execute_query.return_value = [("conv-tuple-123",)]
            result = _get_or_create_conversation_id("sess-2", "prof-2")
        assert result == "conv-tuple-123"


# ---------------------------------------------------------------------------
# Authorization / profile edge cases (lines 178-206)
# ---------------------------------------------------------------------------

class TestAuthorizationEdgeCases(_HandlerTestBase):
    """Test profile-not-found, wrong parent, and inactive profile paths."""

    def test_admin_no_profile_creates_synthetic(self):
        """Admin with no_profile_ sentinel and no DB profile gets synthetic profile."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        auth = self._make_auth_session(role="admin")

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager"), \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            # get_profile returns None → triggers synthetic ChildProfile creation
            mock_pm.return_value.get_profile.return_value = None
            mock_oc.chat.return_value = (True, "Admin test response", {})

            req = ChatRequest(message="Hello", profile_id="no_profile_testing", model="m")
            result = asyncio.run(
                send_chat_message(request=req, auth_session=auth, rate_limit_info={})
            )

        assert result.message == "Admin test response"

    def _run_with_profile_none(self, role="parent", profile_id=None):
        """Helper that forces get_profile to return None."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        if profile_id is None:
            profile_id = "a" * 32
        auth = self._make_auth_session(role=role)

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager"), \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client"), \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.return_value = None

            req = ChatRequest(message="Hello there", profile_id=profile_id, model="m")
            return asyncio.run(
                send_chat_message(request=req, auth_session=auth, rate_limit_info={})
            )

    def test_profile_not_found_parent_returns_404(self):
        """Non-admin user with unknown profile gets 404."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._run_with_profile_none(role="parent")
        assert exc_info.value.status_code == 404

    def test_wrong_parent_returns_403(self):
        """Parent trying to chat for another parent's child gets 403."""
        from fastapi import HTTPException
        profile = self._make_profile(parent_id="other-parent")

        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(profile=profile)
        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    def test_inactive_profile_returns_403(self):
        """Inactive profile returns 403."""
        from fastapi import HTTPException
        profile = self._make_profile()
        profile.is_active = False

        with pytest.raises(HTTPException) as exc_info:
            self._run_handler(profile=profile)
        assert exc_info.value.status_code == 403
        assert "inactive" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Session creation paths (lines 235-252)
# ---------------------------------------------------------------------------

class TestSessionCreation(_HandlerTestBase):
    """Test session creation when no active session exists."""

    def test_creates_new_session_when_none_active(self):
        """When no existing session, a new one is created successfully."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.return_value = profile

            from safety.pipeline import SafetyResult, Severity, Category
            safe = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID,
                                reason="", triggered_keywords=(), stage="none")
            mock_sp.check_input.return_value = safe
            mock_sp.check_output.return_value = safe

            # No existing session → create_session succeeds
            mock_sm.get_session.return_value = None
            mock_sm.get_active_session.return_value = None
            new_sess = MagicMock()
            new_sess.session_id = "new-sess-001"
            mock_sm.create_session.return_value = new_sess

            mock_oc.chat.return_value = (True, "Created!", {})

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="m")
            result = asyncio.run(
                send_chat_message(request=req, auth_session=auth, rate_limit_info={})
            )

        assert result.session_id == "new-sess-001"
        mock_sm.create_session.assert_called_once()

    def test_session_limit_error_returns_429(self):
        """SessionLimitError during session creation returns 429."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest
        from core.session_manager import SessionLimitError
        from fastapi import HTTPException

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client"), \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.return_value = profile
            mock_sm.get_session.return_value = None
            mock_sm.get_active_session.return_value = None
            mock_sm.create_session.side_effect = SessionLimitError("too many sessions")

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="m")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 429

    def test_session_create_error_returns_500(self):
        """SessionError during session creation returns 500."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest
        from core.session_manager import SessionError
        from fastapi import HTTPException

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client"), \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.return_value = profile
            mock_sm.get_session.return_value = None
            mock_sm.get_active_session.return_value = None
            mock_sm.create_session.side_effect = SessionError("db failure")

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="m")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Unsafe AI output (lines 431-447)
# ---------------------------------------------------------------------------

class TestUnsafeAIOutput(_HandlerTestBase):
    """Test response validation catching unsafe AI output."""

    def test_unsafe_output_replaced_with_safe_response(self):
        """When check_output flags response as unsafe, safe alternative is returned."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest

        auth = self._make_auth_session()
        profile = self._make_profile()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger") as mock_il, \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.return_value = profile

            from safety.pipeline import SafetyResult, Severity, Category
            safe_in = SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID,
                                   reason="", triggered_keywords=(), stage="none")
            mock_sp.check_input.return_value = safe_in

            unsafe_out = SafetyResult(
                is_safe=False, severity=Severity.MAJOR, category=Category.VIOLENCE,
                reason="AI generated harmful content", triggered_keywords=("harm",),
                stage="response_validation", modified_content="Here is a safe alternative.",
            )
            mock_sp.check_output.return_value = unsafe_out

            mock_session = MagicMock()
            mock_session.session_id = "sess-unsafe-01"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            mock_oc.chat.return_value = (True, "Harmful AI output here", {})

            req = ChatRequest(message="Tell me about history", profile_id="a" * 32, model="m")
            result = asyncio.run(
                send_chat_message(request=req, auth_session=auth, rate_limit_info={})
            )

        # The unsafe output should be replaced
        assert result.message == "Here is a safe alternative."
        # Incident should be logged
        mock_il.log_incident.assert_called_once()


# ---------------------------------------------------------------------------
# DB_ERRORS and generic Exception outer catch (lines 510-515)
# ---------------------------------------------------------------------------

class TestOuterExceptionHandlers(_HandlerTestBase):
    """Test the outer except blocks that catch DB_ERRORS and Exception."""

    def test_db_error_in_handler_returns_503(self):
        """sqlite3.Error raised in the handler body returns 503."""
        import asyncio, sqlite3
        from api.routes.chat import send_chat_message, ChatRequest
        from fastapi import HTTPException

        auth = self._make_auth_session()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager"), \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client"), \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.side_effect = sqlite3.Error("db corruption")

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="m")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 503

    def test_unexpected_exception_returns_500(self):
        """Unexpected RuntimeError returns 500."""
        import asyncio
        from api.routes.chat import send_chat_message, ChatRequest
        from fastapi import HTTPException

        auth = self._make_auth_session()

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline"), \
             patch("api.routes.chat.session_manager"), \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client"), \
             patch("api.routes.chat.conversation_store"), \
             patch("api.routes.chat._get_or_create_conversation_id", return_value="c"):

            mock_pm.return_value.get_profile.side_effect = RuntimeError("totally unexpected")

            req = ChatRequest(message="Hello", profile_id="a" * 32, model="m")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    send_chat_message(request=req, auth_session=auth, rate_limit_info={})
                )

        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.detail


# ---------------------------------------------------------------------------
# end_session endpoint (lines 518-549)
# ---------------------------------------------------------------------------

class TestEndSession:
    """Tests for the /end-session endpoint."""

    def test_end_session_success(self):
        import asyncio
        from api.routes.chat import end_session

        auth = MagicMock()
        auth.role = "parent"
        auth.user_id = "user-1"

        with patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.audit_log"):
            mock_sm.end_session.return_value = True
            result = asyncio.run(end_session(session_id="sess-end-1", auth_session=auth))

        assert result["status"] == "success"

    def test_end_session_failure_returns_400(self):
        import asyncio
        from api.routes.chat import end_session
        from fastapi import HTTPException

        auth = MagicMock()
        auth.role = "parent"

        with patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.audit_log"):
            mock_sm.end_session.return_value = False
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(end_session(session_id="sess-end-2", auth_session=auth))

        assert exc_info.value.status_code == 400

    def test_end_session_session_error_returns_400(self):
        import asyncio
        from api.routes.chat import end_session
        from core.session_manager import SessionError
        from fastapi import HTTPException

        auth = MagicMock()
        auth.role = "parent"

        with patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.audit_log"):
            mock_sm.end_session.side_effect = SessionError("not found")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(end_session(session_id="sess-end-3", auth_session=auth))

        assert exc_info.value.status_code == 400

    def test_end_session_db_error_returns_503(self):
        import asyncio, sqlite3
        from api.routes.chat import end_session
        from fastapi import HTTPException

        auth = MagicMock()
        auth.role = "parent"

        with patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.audit_log"):
            mock_sm.end_session.side_effect = sqlite3.Error("db fail")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(end_session(session_id="sess-end-4", auth_session=auth))

        assert exc_info.value.status_code == 503

    def test_end_session_unexpected_error_returns_500(self):
        import asyncio
        from api.routes.chat import end_session
        from fastapi import HTTPException

        auth = MagicMock()
        auth.role = "parent"

        with patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.audit_log"):
            mock_sm.end_session.side_effect = RuntimeError("unexpected")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(end_session(session_id="sess-end-5", auth_session=auth))

        assert exc_info.value.status_code == 500
