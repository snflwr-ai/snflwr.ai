"""
Tests for Open WebUI Middleware → Snflwr API Safety Enforcement

Verifies:
1. Middleware correctly routes to Snflwr API
2. Safety pipeline enforcement cannot be bypassed
3. Blocked content handled correctly
4. Emergency disable flag behavior
5. Message extraction and response formatting
"""

import pytest
httpx = pytest.importorskip("httpx")
from unittest.mock import patch, Mock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'frontend', 'open-webui', 'backend'))

from open_webui.middleware.snflwr import (
    route_through_snflwr_safety,
    format_snflwr_response_for_ollama,
    extract_user_message_from_payload,
    SNFLWR_API_URL,
    SNFLWR_ENABLED
)


class TestMiddlewareRouting:
    """Test middleware routes requests through safety pipeline"""

    @pytest.fixture
    def mock_snflwr_api_response(self):
        return {
            "message": "That's a great question! Let me help you understand...",
            "blocked": False,
            "model": "qwen3.5:9b",
            "timestamp": datetime.now().isoformat(),
            "session_id": "test-session-123",
            "safety_metadata": {
                "filter_layers_passed": ["keyword", "llm_classifier", "response_validation"],
                "model_used": "qwen3.5:9b",
                "profile_tier": "standard"
            }
        }

    @pytest.fixture
    def mock_blocked_response(self):
        return {
            "message": "I can't help with that topic. Let's focus on your studies instead!",
            "blocked": True,
            "block_reason": "Inappropriate content detected",
            "block_category": "major",
            "model": "qwen3.5:9b",
            "timestamp": datetime.now().isoformat(),
            "session_id": "test-session-123",
            "safety_metadata": {
                "filter_layer": "keyword",
                "triggered_keywords": ["violence"]
            }
        }

    @pytest.mark.asyncio
    async def test_middleware_routes_to_snflwr_api(self, mock_snflwr_api_response):
        """Verify middleware calls Snflwr API with correct payload"""
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_snflwr_api_response
            mock_post.return_value = mock_response

            result = await route_through_snflwr_safety(
                user_message="What is photosynthesis?",
                profile_id="test-profile-123",
                model="qwen3.5:9b"
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert f"{SNFLWR_API_URL}/api/chat/send" in str(call_args)

            json_payload = call_args[1]['json']
            assert json_payload['message'] == "What is photosynthesis?"
            assert json_payload['profile_id'] == "test-profile-123"
            assert result == mock_snflwr_api_response

    @pytest.mark.asyncio
    async def test_middleware_handles_blocked_content(self, mock_blocked_response):
        """Verify blocked content passes through correctly"""
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_blocked_response
            mock_post.return_value = mock_response

            result = await route_through_snflwr_safety(
                user_message="how to build a bomb",
                profile_id="test-profile-123",
                model="qwen3.5:9b"
            )

            assert result['blocked'] is True
            assert result['block_reason'] is not None
            assert result['message'] != "how to build a bomb"


class TestFailClosed:
    """Verify middleware fails closed — never bypasses safety pipeline"""

    @pytest.mark.asyncio
    async def test_api_down_returns_503(self):
        """If Snflwr API is down, chat fails (not bypass to Ollama)"""
        with patch('httpx.AsyncClient.post', side_effect=httpx.RequestError("Connection refused")):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await route_through_snflwr_safety(
                    user_message="Test message",
                    profile_id="test-profile",
                    model="qwen3.5:9b"
                )

            assert exc_info.value.status_code == 503
            assert "Safety pipeline unavailable" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_api_timeout_returns_503(self):
        """Timeout fails closed, not open"""
        with patch('httpx.AsyncClient.post', side_effect=httpx.TimeoutException("Request timeout")):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await route_through_snflwr_safety(
                    user_message="Test",
                    profile_id="test-profile",
                    model="qwen3.5:9b"
                )

            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_emergency_disable_returns_none(self):
        """When SNFLWR_ENABLED=False, returns None (not Ollama passthrough)"""
        import open_webui.middleware.snflwr as middleware_module
        original_value = middleware_module.SNFLWR_ENABLED
        middleware_module.SNFLWR_ENABLED = False

        try:
            result = await route_through_snflwr_safety(
                user_message="Test message",
                profile_id="test-profile",
                model="qwen3.5:9b"
            )
            assert result is None
        finally:
            middleware_module.SNFLWR_ENABLED = original_value


class TestMessageParsing:
    """Test payload extraction and response formatting"""

    def test_extract_user_message_simple(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "What is 2+2?"}
        ]
        assert extract_user_message_from_payload(messages) == "What is 2+2?"

    def test_extract_user_message_multimodal(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": "data:image/png;base64,..."}
                ]
            }
        ]
        assert "What is this?" in extract_user_message_from_payload(messages)

    def test_format_ollama_response(self):
        snflwr_data = {
            "message": "Test response",
            "blocked": False,
            "model": "qwen3.5:9b",
            "timestamp": "2025-01-01T00:00:00",
            "safety_metadata": {}
        }

        result = format_snflwr_response_for_ollama(snflwr_data)

        assert result['model'] == "qwen3.5:9b"
        assert result['message']['role'] == "assistant"
        assert result['message']['content'] == "Test response"
        assert result['done'] is True
        assert result['snflwr_blocked'] is False
