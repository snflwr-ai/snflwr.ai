"""
Tests for api/routes/thin_client.py — Thin Client Management API

Coverage:
    - GET /api/thin-client/manifest returns correct config shape
    - POST /api/thin-client/register accepts valid payload
    - POST /api/thin-client/register rejects missing fields
"""

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from api.server import app
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


# ── GET /api/thin-client/manifest ────────────────────────────────


class TestManifest:
    def test_manifest_returns_200(self, client):
        resp = client.get("/api/thin-client/manifest")
        assert resp.status_code == 200

    def test_manifest_shape(self, client):
        data = client.get("/api/thin-client/manifest").json()
        assert "version" in data
        assert "config" in data
        assert "launcher_version" in data
        assert "message" in data

    def test_manifest_config_keys(self, client):
        config = client.get("/api/thin-client/manifest").json()["config"]
        assert "OLLAMA_BASE_URL" in config
        assert "API_PORT" in config
        assert "OPEN_WEBUI_URL" in config
        assert "BASE_URL" in config

    def test_manifest_does_not_expose_secrets(self, client):
        data = client.get("/api/thin-client/manifest").json()
        flat = str(data)
        assert "JWT_SECRET" not in flat
        assert "DB_ENCRYPTION_KEY" not in flat
        assert "POSTGRES_PASSWORD" not in flat
        assert "INTERNAL_API_KEY" not in flat

    def test_manifest_no_bind_address(self, client):
        """API_HOST=0.0.0.0 is a bind address, not useful for thin clients."""
        config = client.get("/api/thin-client/manifest").json()["config"]
        assert "API_HOST" not in config


# ── POST /api/thin-client/register ───────────────────────────────


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/api/thin-client/register", json={
            "hostname": "lab-pc-01",
            "platform": "Linux",
            "version": "1.0.0",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"

    def test_register_missing_field(self, client):
        resp = client.post("/api/thin-client/register", json={
            "hostname": "lab-pc-01",
            # missing platform and version
        })
        assert resp.status_code == 422  # Pydantic validation error
