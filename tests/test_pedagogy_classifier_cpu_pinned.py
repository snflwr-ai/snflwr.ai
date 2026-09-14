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
