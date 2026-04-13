"""Tests for the Ollama-compatible proxy that replaces the OWU router fork."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import httpx


class TestOllamaProxyConfig:
    def test_proxy_target_defaults_to_ollama_host(self):
        from config import system_config
        assert hasattr(system_config, "OLLAMA_PROXY_TARGET")
        assert system_config.OLLAMA_PROXY_TARGET.startswith("http")


class TestOllamaPassThrough:
    def test_tags_endpoint_proxied(self):
        from fastapi.testclient import TestClient
        from api.routes.ollama_proxy import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            return_value=httpx.Response(200, json={"models": [{"name": "snflwr.ai"}]}),
        ):
            resp = client.get("/api/tags")
            assert resp.status_code == 200
            assert "models" in resp.json()

    def test_ollama_unreachable_returns_503(self):
        from fastapi.testclient import TestClient
        from api.routes.ollama_proxy import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch(
            "api.routes.ollama_proxy._forward_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            resp = client.get("/api/tags")
            assert resp.status_code == 503
