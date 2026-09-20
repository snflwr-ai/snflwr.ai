"""The pedagogy classifiers (input gate + reveal confirm) must be CPU-pinned.

The GPU placement policy prefers the GPU for any model not already resident, so
on a card that holds one big model these classifiers competed with the co-tenant
brain -- and lost. Measured 2026-09-13 with a 17.8 GB co-tenant model resident:

    GPU-preferring   verdict WRONG (model load failed, HTTP 500)   29.3 s
    num_gpu 0        verdict CORRECT                               13.6 s cold
                                                                    0.8 s warm

A classifier trades latency for never being evicted. Here it costs no latency at
all. Same reasoning that pins llama-guard3-cpu; see also the case where a wrong
verdict is indistinguishable from a working check.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_oneshot_pins_num_gpu_zero():
    from api.routes.ollama_proxy import chat as chat_mod

    sent = {}

    async def _fwd(method, path, content=None, headers=None, **kw):
        sent.update(json.loads(content))
        return httpx.Response(
            200,
            json={"model": "m", "done": True,
                  "message": {"role": "assistant", "content": '{"revealed": true}'}},
        )

    with patch("api.routes.ollama_proxy.transport._forward_request", new=_fwd):
        await chat_mod._pedagogy_oneshot("is this a reveal?", "gemma4:e4b", {})

    assert sent["options"]["num_gpu"] == 0, (
        "the classifier was left to the GPU placement policy; it will be evicted "
        "by the co-tenant brain and return a wrong verdict"
    )
    assert sent["options"]["temperature"] == 0
    assert sent["think"] is False


@pytest.mark.asyncio
async def test_placement_injection_respects_the_explicit_pin():
    # transport._inject_gpu_placement must not override a caller-supplied
    # num_gpu, or the pin above would be silently undone in transit.
    from api.routes.ollama_proxy import transport

    body = json.dumps({"model": "gemma4:e4b", "options": {"num_gpu": 0}}).encode()
    out = json.loads(transport._inject_gpu_placement("/api/chat", body))
    assert out["options"]["num_gpu"] == 0


def test_the_selftest_and_production_cannot_drift():
    """The deploy-time self-test must send what production sends.

    A guard that tests different options than production is not a guard: it can
    fail while production is fine, or pass while production is broken. Both
    happened in one session, so the options live in ONE place and this asserts
    both call sites read it.
    """
    import inspect

    from core.pedagogy import PEDAGOGY_CLASSIFIER_OPTIONS
    import scripts.postdeploy_smoke as smoke
    from api.routes.ollama_proxy import chat as chat_mod

    assert PEDAGOGY_CLASSIFIER_OPTIONS["num_gpu"] == 0
    assert PEDAGOGY_CLASSIFIER_OPTIONS["temperature"] == 0

    for mod, name in (
        (chat_mod, "_pedagogy_oneshot"),
        (smoke, "_check_confirm_actually_detects_a_reveal"),
    ):
        src = inspect.getsource(getattr(mod, name))
        assert "classifier_options" in src, (
            f"{name} builds classifier options by hand instead of using the "
            "shared derivation; it will drift from production"
        )

    # The pin is no longer unconditional, so "both read the same constant" is no
    # longer enough: they must agree on the CONDITION too. The confirm running on
    # the tutor has two consequences -- drop the CPU pin AND replace the persona
    # -- and a self-test that mirrors one but not the other tests a configuration
    # nothing runs. That is what this guard caught when the pin became
    # conditional: production stopped pinning while the self-test kept pinning.
    smoke_src = inspect.getsource(smoke._check_confirm_actually_detects_a_reveal)
    assert "CLASSIFIER_SYSTEM" in smoke_src, (
        "the deploy self-test calls the confirm without the persona override "
        "production uses; on a one-tutor deployment (both GUIDANCE_* vars "
        "default to \"\") it would exercise the 0/27-unparseable path instead"
    )
    assert "system=" in smoke_src, (
        "the self-test computes a system override but never sends it"
    )
