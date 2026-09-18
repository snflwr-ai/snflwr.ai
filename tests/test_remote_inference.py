"""Phase 2: a box with no certified GPU offloads inference to a tutor server.

The two rules these tests exist to hold:

1. **Local hardware is never consulted in remote mode.** A thin client has no
   GPU, so asking this card about a remote card yields VRAM 0 and switches
   tutoring off -- the same failure shape as the API container that could not
   see nvidia-smi. Asserted by making the detectors explode, not by checking a
   number: a detector that still runs but happens to return something benign
   would pass a value assertion and fail in production.

2. **The quality floor is enforced on the CLIENT.** A server that has been
   misconfigured or quietly downgraded must not be able to hand a child a weaker
   tutor than the one that passed the sealed run.
"""

import pytest

from core import serving_plan
from core.inference.base import InferenceError
from core.inference.remote_driver import RemoteDriver

CERTIFIED = {
    "engine": "ollama",
    "model": "snflwr.ai-31b",
    "num_ctx": 16384,
    "quality_tier": "certified",
    "max_concurrent": 3,
    "sealed_on": "2026-09-17",
}


def _boom(*a, **k):  # pragma: no cover - called means the test failed
    raise AssertionError("local hardware detection ran in remote mode")


def _remote(monkeypatch, advertised, *, url="https://tutor.example", token="t"):
    """Compute a plan in remote mode with every local detector booby-trapped."""
    monkeypatch.setattr(serving_plan, "_detect_vram_gb", _boom)
    monkeypatch.setattr(serving_plan, "_detect_memory_gb", _boom)
    monkeypatch.setattr(serving_plan, "_vllm_reachable", _boom)
    monkeypatch.setattr(serving_plan, "_has_nvidia_gpu", _boom)
    monkeypatch.setattr(serving_plan, "_configured_tutor_model", _boom)
    monkeypatch.setattr(serving_plan, "_fetch_remote_plan", lambda u, t: advertised)
    monkeypatch.setenv("INFERENCE_REMOTE_URL", url)
    if token is None:
        monkeypatch.delenv("INFERENCE_REMOTE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("INFERENCE_REMOTE_TOKEN", token)
    return serving_plan.compute_plan()


class TestLocalHardwareIsNotConsulted:
    def test_a_certified_remote_tutors_on_a_box_with_no_gpu(self, monkeypatch):
        plan = _remote(monkeypatch, CERTIFIED)
        assert plan.engine == "remote"
        assert plan.tutoring_enabled is True
        assert plan.tutor_model == "snflwr.ai-31b"
        assert plan.quality_tier == "certified"

    def test_even_the_refusal_path_does_not_touch_local_hardware(self, monkeypatch):
        """A rejected remote must not fall back into local detection."""
        plan = _remote(monkeypatch, {**CERTIFIED, "model": "snflwr.ai"})
        assert plan.engine == "remote"
        assert plan.tutoring_enabled is False

    def test_vram_stays_zero_because_nobody_asked(self, monkeypatch):
        plan = _remote(monkeypatch, CERTIFIED)
        assert plan.vram_gb == 0.0


class TestTheQualityFloorTravels:
    def test_an_uncertified_model_is_refused(self, monkeypatch):
        plan = _remote(monkeypatch, {**CERTIFIED, "model": "snflwr.ai"})
        assert plan.tutoring_enabled is False
        assert "no sealed tutoring run" in plan.reason

    def test_an_uncertified_engine_is_refused(self, monkeypatch):
        """vLLM has not passed the parity re-run, remotely or locally."""
        plan = _remote(monkeypatch, {**CERTIFIED, "engine": "vllm"})
        assert plan.tutoring_enabled is False

    def test_a_context_window_above_the_validated_ceiling_is_refused(self, monkeypatch):
        plan = _remote(monkeypatch, {**CERTIFIED, "num_ctx": 32768})
        assert plan.tutoring_enabled is False
        assert "validated ceiling" in plan.reason

    def test_the_validated_ceiling_itself_is_accepted(self, monkeypatch):
        plan = _remote(monkeypatch, {**CERTIFIED, "num_ctx": 24576})
        assert plan.tutoring_enabled is True
        assert plan.num_ctx == 24576

    def test_a_server_claiming_certified_for_junk_is_still_refused(self, monkeypatch):
        """The tier the server claims is not the tier the client accepts."""
        plan = _remote(
            monkeypatch,
            {**CERTIFIED, "model": "definitely-not-a-backbone", "quality_tier": "certified"},
        )
        assert plan.tutoring_enabled is False


class TestUnreachableAndMalformed:
    def test_no_answer_means_no_tutoring(self, monkeypatch):
        plan = _remote(monkeypatch, None)
        assert plan.tutoring_enabled is False
        assert plan.quality_tier == "unsupported"

    def test_a_missing_token_is_named_in_the_reason(self, monkeypatch):
        plan = _remote(monkeypatch, None, token=None)
        assert "INFERENCE_REMOTE_TOKEN is not set" in plan.reason

    def test_an_unreadable_plan_is_refused(self, monkeypatch):
        plan = _remote(monkeypatch, {"engine": "ollama"})  # no model, no num_ctx
        assert plan.tutoring_enabled is False
        assert "not readable" in plan.reason

    def test_cleartext_to_another_host_is_refused_before_any_fetch(self, monkeypatch):
        plan = _remote(monkeypatch, CERTIFIED, url="http://tutor.example")
        assert plan.tutoring_enabled is False
        assert "cleartext" in plan.reason


class TestCapacityBelongsToTheServer:
    def test_the_advertised_slot_count_is_used(self, monkeypatch):
        """Serializing locally would cap a 64-slot server at one turn at a time."""
        plan = _remote(monkeypatch, {**CERTIFIED, "max_concurrent": 64})
        assert plan.max_concurrent_requests == 64

    def test_a_missing_slot_count_is_conservative(self, monkeypatch):
        advertised = {k: v for k, v in CERTIFIED.items() if k != "max_concurrent"}
        plan = _remote(monkeypatch, advertised)
        assert plan.max_concurrent_requests == 1


class TestRemoteDriverRefusesUnsafeLinks:
    def test_cleartext_off_box_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="cleartext"):
            RemoteDriver(base_url="http://tutor.example", token="t")

    def test_a_file_url_is_refused(self):
        with pytest.raises(ValueError):
            RemoteDriver(base_url="file:///etc/passwd", token="t")

    def test_loopback_cleartext_is_allowed(self):
        driver = RemoteDriver(base_url="http://localhost:39150", token="t")
        assert driver.name == "remote"

    def test_the_token_is_never_exposed(self):
        """Presence only -- the standing rule is that credentials are checked,
        never rendered."""
        driver = RemoteDriver(base_url="https://tutor.example", token="s3cret")
        assert driver.has_token is True
        assert "s3cret" not in repr(driver)
        assert not hasattr(driver, "token")

    def test_no_token_is_reported_honestly(self):
        driver = RemoteDriver(base_url="https://tutor.example")
        assert driver.has_token is False

    def test_the_engine_schedules_so_this_side_does_not(self):
        caps = RemoteDriver(base_url="https://tutor.example").capabilities()
        assert caps.max_slots == 0


@pytest.mark.asyncio
class TestFetchPlan:
    async def test_a_good_plan_is_parsed(self, monkeypatch):
        import httpx

        def handler(request):
            assert request.headers["Authorization"] == "Bearer tok"
            return httpx.Response(200, json=CERTIFIED)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(
            transport=transport, headers={"Authorization": "Bearer tok"}
        )
        driver = RemoteDriver(
            base_url="https://tutor.example", token="tok", client=client
        )
        plan = await driver.fetch_plan()
        assert plan.model == "snflwr.ai-31b"
        assert plan.num_ctx == 16384
        assert plan.max_concurrent == 3
        await driver.aclose()

    async def test_a_rejected_credential_says_so_without_printing_it(self):
        import httpx

        transport = httpx.MockTransport(lambda r: httpx.Response(401, text="nope"))
        client = httpx.AsyncClient(transport=transport)
        driver = RemoteDriver(
            base_url="https://tutor.example", token="s3cret", client=client
        )
        with pytest.raises(InferenceError) as excinfo:
            await driver.fetch_plan()
        assert "INFERENCE_REMOTE_TOKEN" in str(excinfo.value)
        assert "s3cret" not in str(excinfo.value)
        await driver.aclose()

    async def test_a_non_object_body_is_refused(self):
        import httpx

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[1, 2]))
        client = httpx.AsyncClient(transport=transport)
        driver = RemoteDriver(base_url="https://tutor.example", client=client)
        with pytest.raises(InferenceError, match="did not return an object"):
            await driver.fetch_plan()
        await driver.aclose()


class TestRemotePlansAreReVerified:
    """A remote plan describes ANOTHER machine's configuration, and someone can
    change it without telling us. Checking once at startup means a server
    reconfigured to an uncertified backbone keeps receiving children's turns
    until something restarts us -- the same shape as a safety classifier
    silently following a backbone swap.

    Local plans are deliberately NOT re-checked: they describe this box's own
    hardware, which does not change underneath a running process.
    """

    @pytest.fixture(autouse=True)
    def _clear(self, monkeypatch):
        monkeypatch.setattr(serving_plan, "_PLAN", None)
        monkeypatch.setattr(serving_plan, "_PLAN_AT", 0.0)

    def _serve(self, monkeypatch, advertised, clock):
        monkeypatch.setattr(serving_plan, "_fetch_remote_plan", lambda u, t: advertised())
        monkeypatch.setattr(serving_plan.time, "monotonic", clock)
        monkeypatch.setenv("INFERENCE_REMOTE_URL", "https://tutor.example")
        monkeypatch.setenv("INFERENCE_REMOTE_TOKEN", "t")

    def test_within_the_ttl_the_remote_is_not_asked_again(self, monkeypatch):
        calls = []

        def advertised():
            calls.append(1)
            return CERTIFIED

        now = [0.0]
        self._serve(monkeypatch, advertised, lambda: now[0])
        assert serving_plan.get_plan().tutoring_enabled is True
        now[0] = 10.0
        serving_plan.get_plan()
        assert len(calls) == 1

    def test_after_the_ttl_a_downgraded_remote_stops_being_used(self, monkeypatch):
        state = {"plan": CERTIFIED}
        now = [0.0]
        self._serve(monkeypatch, lambda: state["plan"], lambda: now[0])
        assert serving_plan.get_plan().tutoring_enabled is True

        # Someone repoints the tutor server at a backbone that never passed.
        state["plan"] = {**CERTIFIED, "model": "snflwr.ai"}
        now[0] = serving_plan.REMOTE_PLAN_TTL_S + 1
        plan = serving_plan.get_plan()
        assert plan.tutoring_enabled is False
        assert "no sealed tutoring run" in plan.reason

    def test_an_unreachable_remote_keeps_the_last_verified_plan(self, monkeypatch):
        """A blip is not evidence the remote changed. Taking the tutor down here
        would show a child 'not available on this computer' for what is really
        'try again in a moment'."""
        state = {"plan": CERTIFIED}
        now = [0.0]
        self._serve(monkeypatch, lambda: state["plan"], lambda: now[0])
        assert serving_plan.get_plan().tutoring_enabled is True

        state["plan"] = None  # fetch failed
        now[0] = serving_plan.REMOTE_PLAN_TTL_S + 1
        plan = serving_plan.get_plan()
        assert plan.tutoring_enabled is True, "a blip took the tutor offline"
        assert plan.tutor_model == "snflwr.ai-31b"

    def test_a_remote_that_comes_back_certified_is_used_again(self, monkeypatch):
        state = {"plan": CERTIFIED}
        now = [0.0]
        self._serve(monkeypatch, lambda: state["plan"], lambda: now[0])
        serving_plan.get_plan()
        state["plan"] = {**CERTIFIED, "model": "snflwr.ai"}
        now[0] = serving_plan.REMOTE_PLAN_TTL_S + 1
        assert serving_plan.get_plan().tutoring_enabled is False
        state["plan"] = CERTIFIED
        now[0] = 2 * serving_plan.REMOTE_PLAN_TTL_S + 2
        assert serving_plan.get_plan().tutoring_enabled is True

    def test_a_refusal_is_not_mistaken_for_a_blip(self, monkeypatch):
        """The distinction the whole design rests on: 'we could not ask' must not
        look like 'we asked and the answer was not certified'."""
        now = [0.0]
        self._serve(monkeypatch, lambda: {**CERTIFIED, "model": "nope"}, lambda: now[0])
        plan = serving_plan.get_plan()
        assert plan.remote_reachable is True
        assert plan.tutoring_enabled is False

        monkeypatch.setattr(serving_plan, "_fetch_remote_plan", lambda u, t: None)
        serving_plan._PLAN = None
        blip = serving_plan.get_plan()
        assert blip.remote_reachable is False

    def test_a_local_plan_is_never_re_verified(self, monkeypatch):
        """Local hardware does not change underneath a running process, and a
        re-check would put a detection probe on a child's turn for nothing."""
        calls = []

        def counting_compute():
            calls.append(1)
            return serving_plan.ServingPlan(
                engine="ollama",
                tutor_model="snflwr.ai-31b",
                quality_tier="certified",
                tutoring_enabled=True,
                num_ctx=24576,
                max_concurrent_requests=1,
                reason="local",
            )

        now = [0.0]
        monkeypatch.setattr(serving_plan, "compute_plan", counting_compute)
        monkeypatch.setattr(serving_plan.time, "monotonic", lambda: now[0])
        serving_plan.get_plan()
        now[0] = 10 * serving_plan.REMOTE_PLAN_TTL_S
        serving_plan.get_plan()
        assert len(calls) == 1

    def test_the_ttl_is_configurable(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_REMOTE_PLAN_TTL_S", "30")
        assert serving_plan._remote_ttl_s() == 30.0

    def test_a_junk_ttl_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_REMOTE_PLAN_TTL_S", "soon")
        assert serving_plan._remote_ttl_s() == serving_plan.REMOTE_PLAN_TTL_S
