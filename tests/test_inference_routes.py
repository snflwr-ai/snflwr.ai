"""Tests for api/routes/inference.py — the tutor server's side of remote mode.

This endpoint describes the deployment (engine, model, context window,
capacity), so unlike the thin-client manifest it is authenticated: an
unauthenticated caller must learn nothing about this machine.
"""

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from api.routes import inference  # noqa: E402
from api.server import app  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

TOKEN = "a-tutor-server-token"


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def serving(monkeypatch):
    monkeypatch.setenv("INFERENCE_SERVER_TOKEN", TOKEN)


class TestNotATutorServer:
    """A server with no token set is not offering remote inference."""

    def test_no_token_configured_hides_the_endpoint(self, client, monkeypatch):
        monkeypatch.delenv("INFERENCE_SERVER_TOKEN", raising=False)
        resp = client.get(
            "/api/inference/plan", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 404


class TestAuth:
    def test_no_credential_is_rejected(self, client, serving):
        assert client.get("/api/inference/plan").status_code == 401

    def test_a_non_bearer_header_is_rejected(self, client, serving):
        resp = client.get(
            "/api/inference/plan", headers={"Authorization": "Basic abc"}
        )
        assert resp.status_code == 401

    def test_a_wrong_token_is_rejected(self, client, serving):
        resp = client.get(
            "/api/inference/plan", headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status_code == 401

    def test_the_rejection_does_not_echo_the_token(self, client, serving):
        resp = client.get(
            "/api/inference/plan", headers={"Authorization": "Bearer wrong"}
        )
        assert TOKEN not in resp.text

    def test_a_valid_token_is_admitted(self, client, serving):
        resp = client.get(
            "/api/inference/plan", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 200


class TestPlanShape:
    def test_the_advertised_fields_are_what_the_client_parses(self, client, serving):
        body = client.get(
            "/api/inference/plan", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
        for field in (
            "engine",
            "model",
            "num_ctx",
            "quality_tier",
            "max_concurrent",
            "sealed_on",
        ):
            assert field in body, field

    def test_it_reports_the_real_tier_not_a_flattering_one(
        self, client, serving, monkeypatch
    ):
        """A server that cannot tutor locally must not offer to tutor remotely.
        Reporting the real tier lets the client refuse for a readable reason
        instead of discovering the problem in a child's reply."""
        from core import serving_plan as sp

        unsupported = sp.ServingPlan(
            engine="ollama",
            tutor_model=None,
            quality_tier="unsupported",
            tutoring_enabled=False,
            num_ctx=0,
            max_concurrent_requests=1,
            reason="test",
        )
        monkeypatch.setattr(inference.serving_plan, "get_plan", lambda: unsupported)
        body = client.get(
            "/api/inference/plan", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
        assert body["quality_tier"] == "unsupported"
        assert body["model"] == ""
        assert body["sealed_on"] == ""

    def test_a_certified_plan_carries_its_sealed_date(
        self, client, serving, monkeypatch
    ):
        from core import serving_plan as sp

        certified = sp.ServingPlan(
            engine="ollama",
            tutor_model="snflwr.ai-31b",
            quality_tier="certified",
            tutoring_enabled=True,
            num_ctx=24576,
            max_concurrent_requests=3,
            reason="test",
        )
        monkeypatch.setattr(inference.serving_plan, "get_plan", lambda: certified)
        body = client.get(
            "/api/inference/plan", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
        assert body["model"] == "snflwr.ai-31b"
        assert body["num_ctx"] == 24576
        assert body["max_concurrent"] == 3
        assert body["sealed_on"] == "2026-09-17"
