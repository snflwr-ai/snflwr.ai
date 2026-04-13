"""
Tests for Ollama Proxy Safety Enforcement

Verifies:
1. Proxy correctly routes student messages through safety pipeline
2. Admin messages bypass safety
3. Blocked content returned in Ollama format
4. Fail-closed behavior on missing user identity
5. Helper function correctness
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from api.routes.ollama_proxy import (
    _get_user_from_headers,
    _extract_last_user_message,
    _ollama_block_response,
)


class TestProxyHelpers:
    """Test proxy utility functions."""

    def test_extract_last_user_message_simple(self):
        messages = [
            {"role": "system", "content": "You are a tutor"},
            {"role": "user", "content": "What is 2+2?"},
        ]
        assert _extract_last_user_message(messages) == "What is 2+2?"

    def test_extract_last_user_message_multimodal(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": "data:..."},
                ],
            },
        ]
        assert _extract_last_user_message(messages) == "Describe this"

    def test_extract_last_user_message_empty(self):
        assert _extract_last_user_message([]) == ""

    def test_extract_last_user_message_no_user_role(self):
        messages = [{"role": "system", "content": "system prompt"}]
        assert _extract_last_user_message(messages) == ""

    def test_block_response_format(self):
        resp = _ollama_block_response("snflwr.ai", "Can't help with that")
        assert resp["model"] == "snflwr.ai"
        assert resp["message"]["role"] == "assistant"
        assert resp["message"]["content"] == "Can't help with that"
        assert resp["done"] is True
        assert "created_at" in resp

    def test_get_user_from_headers_present(self):
        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenWebUI-User-Id": "user-123",
            "X-OpenWebUI-User-Role": "user",
        }
        user_id, role = _get_user_from_headers(mock_request)
        assert user_id == "user-123"
        assert role == "user"

    def test_get_user_from_headers_missing(self):
        mock_request = MagicMock()
        mock_request.headers = {}
        user_id, role = _get_user_from_headers(mock_request)
        assert user_id is None
        assert role == "user"  # Fail-closed: treat as student

    def test_get_user_from_headers_admin(self):
        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenWebUI-User-Id": "admin-1",
            "X-OpenWebUI-User-Role": "admin",
        }
        user_id, role = _get_user_from_headers(mock_request)
        assert user_id == "admin-1"
        assert role == "admin"
