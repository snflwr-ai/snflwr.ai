"""Tests for the Ollama-compatible proxy that replaces the OWU router fork."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import os


class TestOllamaProxyConfig:
    def test_proxy_target_defaults_to_ollama_host(self):
        from config import system_config
        assert hasattr(system_config, "OLLAMA_PROXY_TARGET")
        assert system_config.OLLAMA_PROXY_TARGET.startswith("http")
