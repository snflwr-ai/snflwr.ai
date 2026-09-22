"""Remote inference must actually route a child's turn, and fail closed.

Before 2026-09-22 remote mode was architecture-complete and physically
impossible. `core/inference/remote_driver.py` verified a tutor server -- its
TLS, its credential, and that the (engine, model, num_ctx) triple it advertises
has a sealed run -- and `RemoteDriver` was imported by nothing but its own
tests. Meanwhile all three request builders in `transport` pasted
`system_config.OLLAMA_PROXY_TARGET` inline, so every turn went to the local
Ollama no matter what the plan said.

That is the same defect class as a guarded upgrade that rebuilds a model nobody
serves: a guard examining one artifact while the traffic goes to another. Worse
here, because the thing being guarded is which tutor a child talks to.
"""

import httpx
import pytest

from api.routes.ollama_proxy import transport
from core import serving_plan
from core.serving_plan import ServingPlan


def _remote(**kw) -> ServingPlan:
    base = dict(
        engine="remote",
        tutor_model="snflwr.ai-31b",
        quality_tier="certified",
        tutoring_enabled=True,
        num_ctx=16384,
        max_concurrent_requests=8,
        reason="test",
        engine_args={"base_url": "https://tutor.example"},
    )
    base.update(kw)
    return ServingPlan(**base)


class TestTheTargetComesFromThePlan:
    def test_a_certified_remote_plan_routes_to_its_own_url(self, monkeypatch):
        monkeypatch.setattr(serving_plan, "get_plan", lambda: _remote())
        monkeypatch.setattr(serving_plan, "remote_token", lambda: "s3cret")
        base, headers = transport._upstream()
        assert base == "https://tutor.example"
        assert headers["Authorization"] == "Bearer s3cret"

    def test_the_url_is_NOT_read_from_the_environment(self, monkeypatch):
        """The plan verified a specific URL. If transport read the env var
        instead, traffic could go somewhere the plan never checked -- which is
        the whole bug this wiring removes."""
        monkeypatch.setenv("INFERENCE_REMOTE_URL", "https://attacker.example")
        monkeypatch.setattr(
            serving_plan, "get_plan",
            lambda: _remote(engine_args={"base_url": "https://verified.example"}),
        )
        monkeypatch.setattr(serving_plan, "remote_token", lambda: "t")
        base, _ = transport._upstream()
        assert base == "https://verified.example"


class TestItFailsCLOSEDNotBackToLocal:
    """The important half.

    A thin client running remote mode has NO local model. Silently forwarding to
    localhost:11434 would turn "the tutor server is down" into "the tutor
    answered oddly" -- an unmeasured tutor talking to a child instead of an
    honest outage. Every caller in transport maps a transport error to a
    graceful 503.
    """

    def test_an_unreachable_remote_raises_rather_than_using_local(self, monkeypatch):
        monkeypatch.setattr(
            serving_plan, "get_plan",
            lambda: _remote(tutoring_enabled=False, remote_reachable=False,
                            reason="remote did not answer with a usable plan"),
        )
        with pytest.raises(httpx.ConnectError) as exc:
            transport._upstream()
        assert "not serving a certified backbone" in str(exc.value)
        assert "usable plan" in str(exc.value), "the plan's own reason must survive"

    def test_an_UNCERTIFIED_remote_raises_too(self, monkeypatch):
        """Reachable but downgraded. Distinct from unreachable -- a network blip
        must not read as a downgraded server, and a downgraded server must not
        be excused as a blip -- but both refuse to serve."""
        monkeypatch.setattr(
            serving_plan, "get_plan",
            lambda: _remote(tutoring_enabled=False, remote_reachable=True,
                            reason="remote serves gemma4:e4b, which has no sealed run"),
        )
        with pytest.raises(httpx.ConnectError) as exc:
            transport._upstream()
        assert "no sealed run" in str(exc.value)

    def test_a_remote_plan_with_no_url_raises(self, monkeypatch):
        """Belt and braces: engine says remote, plan carries no target. Routing
        to local here would be the original bug wearing a different hat."""
        monkeypatch.setattr(serving_plan, "get_plan", lambda: _remote(engine_args={}))
        with pytest.raises(httpx.ConnectError):
            transport._upstream()


class TestLocalPathsAreUntouched:
    """The sealed tutoring result was measured on local Ollama. This wiring must
    not move that path by a single character."""

    @pytest.mark.parametrize("engine", ["ollama", "vllm"])
    def test_non_remote_engines_use_the_configured_target(self, monkeypatch, engine):
        from config import system_config

        monkeypatch.setattr(
            serving_plan, "get_plan",
            lambda: ServingPlan(engine=engine, tutor_model="snflwr.ai-31b",
                                quality_tier="certified", tutoring_enabled=True,
                                num_ctx=16384, max_concurrent_requests=1, reason="t"),
        )
        base, headers = transport._upstream()
        assert base == system_config.OLLAMA_PROXY_TARGET.rstrip("/")
        assert headers == {}, "no credential is sent to a loopback backend"

    def test_a_broken_plan_falls_back_to_local_rather_than_failing(self, monkeypatch):
        """Fail-SAFE for the local case, fail-CLOSED for remote. A planning bug
        must not take down a box that has a working local model."""
        def boom():
            raise RuntimeError("detection exploded")

        monkeypatch.setattr(serving_plan, "get_plan", boom)
        base, headers = transport._upstream()
        assert base and headers == {}


class TestTheCredentialIsNotOnThePlan:
    def test_serving_plan_exposes_a_token_accessor(self):
        assert callable(serving_plan.remote_token)

    def test_no_plan_field_carries_the_token(self):
        """Plan objects are logged, returned by /health and compared in tests. A
        bearer token on one would leak through all three, so the URL travels
        with the plan and the secret is read at point of use."""
        p = _remote()
        blob = repr(p).lower()
        assert "token" not in blob and "authorization" not in blob
