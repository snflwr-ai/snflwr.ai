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


class TestTurnRoute:
    """The route a real remote turn actually lands on.

    This is the seam the first cut of phase 2 was missing entirely: the plan
    endpoint existed, but turns still went to `/api/chat`, which on a snflwr box
    is the session-authenticated student API rather than an engine. Every unit
    test passed because they all stopped at exactly this boundary, so this test
    drives the whole path -- token auth, admission, engine -- through the app.
    """

    @pytest.fixture
    def fake_engine(self, monkeypatch):
        import json as _json

        class FakeClient:
            def __init__(self):
                self.bodies = []
                self.turns = 0

            def turn(self):
                # The admission dependency takes a real turn slot before the
                # handler runs; a fake without this would skip the gate the
                # route exists to apply.
                outer = self

                class _Turn:
                    async def __aenter__(self):
                        outer.turns += 1

                    async def __aexit__(self, *exc):
                        return False

                return _Turn()

            async def chat_ollama_bytes(self, body, *, timeout_s):
                self.bodies.append(body)
                return _json.dumps(
                    {
                        "model": "snflwr.ai-31b",
                        "message": {"role": "assistant", "content": "Let's work it out."},
                        "done": True,
                    }
                ).encode()

            async def stream_ollama_ndjson(self, body, *, timeout_s):
                self.bodies.append(body)
                for part in ("Let's ", "work it out."):
                    yield _json.dumps(
                        {"model": "m", "message": {"content": part}, "done": False}
                    ).encode() + b"\n"
                yield _json.dumps(
                    {"model": "m", "message": {"content": ""}, "done": True}
                ).encode() + b"\n"

        fake = FakeClient()
        monkeypatch.setattr(inference.inference_client, "get_client", lambda: fake)
        # The route refuses to serve turns unless this server can tutor.
        from core import serving_plan as sp

        monkeypatch.setattr(
            inference.serving_plan,
            "get_plan",
            lambda: sp.ServingPlan(
                engine="ollama",
                tutor_model="snflwr.ai-31b",
                quality_tier="certified",
                tutoring_enabled=True,
                num_ctx=24576,
                max_concurrent_requests=3,
                reason="test",
            ),
        )
        return fake

    def test_a_turn_with_a_valid_token_is_served(self, client, serving, fake_engine):
        resp = client.post(
            "/api/inference/chat",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"model": "snflwr.ai-31b", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["message"]["content"] == "Let's work it out."
        assert fake_engine.bodies, "the turn never reached the engine"
        assert fake_engine.turns == 1, "the server did not take an admission slot"

    def test_a_streamed_turn_is_ndjson(self, client, serving, fake_engine):
        resp = client.post(
            "/api/inference/chat",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"model": "m", "messages": [], "stream": True},
        )
        assert resp.status_code == 200
        lines = [ln for ln in resp.text.splitlines() if ln.strip()]
        joined = "".join(__import__("json").loads(ln)["message"]["content"] for ln in lines)
        assert joined == "Let's work it out."

    def test_an_unauthenticated_turn_never_reaches_the_engine(
        self, client, serving, fake_engine
    ):
        resp = client.post(
            "/api/inference/chat", json={"model": "m", "messages": []}
        )
        assert resp.status_code == 401
        assert not fake_engine.bodies

    def test_a_wrong_token_never_reaches_the_engine(self, client, serving, fake_engine):
        resp = client.post(
            "/api/inference/chat",
            headers={"Authorization": "Bearer wrong"},
            json={"model": "m", "messages": []},
        )
        assert resp.status_code == 401
        assert not fake_engine.bodies

    def test_a_server_that_cannot_tutor_refuses_to_serve_turns(
        self, client, serving, fake_engine, monkeypatch
    ):
        from core import serving_plan as sp

        monkeypatch.setattr(
            inference.serving_plan,
            "get_plan",
            lambda: sp.ServingPlan(
                engine="ollama",
                tutor_model=None,
                quality_tier="unsupported",
                tutoring_enabled=False,
                num_ctx=0,
                max_concurrent_requests=1,
                reason="no certified backbone here",
            ),
        )
        resp = client.post(
            "/api/inference/chat",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"model": "m", "messages": []},
        )
        assert resp.status_code == 503
        assert not fake_engine.bodies

    def test_a_non_json_body_is_refused(self, client, serving, fake_engine):
        resp = client.post(
            "/api/inference/chat",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
            content=b"not json",
        )
        assert resp.status_code == 400
        assert not fake_engine.bodies


class TestTheClientAndServerAgreeOnPaths:
    """A path typo here is invisible to every mocked test on both sides."""

    def test_the_driver_posts_to_the_route_the_server_exposes(self):
        from core.inference import remote_driver

        routes = {r.path for r in app.routes if hasattr(r, "path")}
        assert remote_driver.CHAT_PATH in routes
        assert remote_driver.PLAN_PATH in routes
