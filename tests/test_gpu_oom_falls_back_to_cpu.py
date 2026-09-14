"""A full GPU must degrade to a slower answer, never to no answer.

Two ollama daemons share this card: snflwr's (container-internal) and a
host-level co-tenant. NEITHER sees the other's models in /api/ps, so fit cannot
be predicted -- only detected. When the co-tenant holds the card, a load here
dies with `cudaMalloc failed: out of memory` and ollama answers 500.

Measured 2026-09-14: the child then received a canned "I need to rephrase my
response", because the safety classifier -- correctly failing closed on an empty
body -- had nothing to check. A 13.6 s CPU answer beats that.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest


_OOM_BODY = (
    '{"error":"llama-server process has terminated: exit status 1: '
    'cudaMalloc failed: out of memory"}'
)


@pytest.mark.asyncio
async def test_gpu_oom_is_retried_pinned_to_cpu():
    from api.routes.ollama_proxy import transport

    sent: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            sent.append(json.loads(kw["content"]))
            if len(sent) == 1:
                return httpx.Response(500, text=_OOM_BODY)
            return httpx.Response(200, json={"message": {"content": "ok"}})

    with patch.object(transport.httpx, "AsyncClient", lambda **kw: _Client()):
        resp = await transport._forward_request(
            "POST", "/api/chat",
            content=json.dumps({"model": "snflwr.ai",
                                "messages": [{"role": "user", "content": "hi"}]}).encode(),
        )

    assert resp.status_code == 200, "a full GPU returned an error to the child"
    assert len(sent) == 2, "no CPU retry was attempted"
    assert sent[1]["options"]["num_gpu"] == 0, "the retry was not pinned to the CPU"


@pytest.mark.asyncio
async def test_a_real_500_is_not_retried_forever():
    from api.routes.ollama_proxy import transport

    calls = {"n": 0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            calls["n"] += 1
            # A 500 that is NOT an OOM: a genuine backend fault passes through.
            return httpx.Response(500, text='{"error":"something else broke"}')

    with patch.object(transport.httpx, "AsyncClient", lambda **kw: _Client()):
        resp = await transport._forward_request(
            "POST", "/api/chat", content=json.dumps({"model": "m"}).encode())

    assert resp.status_code == 500
    assert calls["n"] == 1, "a non-OOM 500 must not trigger the CPU retry"
