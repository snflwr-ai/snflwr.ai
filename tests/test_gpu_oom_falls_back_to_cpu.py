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


@pytest.mark.asyncio
async def test_the_STREAMING_path_also_retries_on_cpu():
    """The student path streams, and it builds its own request.

    The first version of this fix lived only in _forward_request, so it covered
    the non-streaming path and MISSED the one children actually use -- the same
    class of mistake as the enforcer that never ran on streamed turns. This test
    exists so that cannot happen silently again.
    """
    from api.routes.ollama_proxy import transport

    sent: list[dict] = []

    class _Resp:
        def __init__(self, status, body=b""):
            self.status_code = status
            self._body = body
            self.text = body.decode() if body else ""

        async def aread(self):
            return self._body

        async def aclose(self):
            return None

        async def aiter_bytes(self):
            yield b'{"message":{"content":"ok"},"done":true}\n'

    class _Client:
        def build_request(self, method, url, content=None, headers=None):
            return {"content": content}

        async def send(self, req, stream=False):
            sent.append(json.loads(req["content"]))
            if len(sent) == 1:
                return _Resp(500, _OOM_BODY.encode())
            return _Resp(200)

        async def aclose(self):
            return None

    with (
        patch.object(transport.httpx, "AsyncClient", lambda **kw: _Client()),
        patch.object(transport.ollama_circuit, "can_execute", lambda: True),
    ):
        chunks = [
            c
            async for c in transport._stream_chunks_from_ollama(
                json.dumps({"model": "snflwr.ai",
                            "messages": [{"role": "user", "content": "hi"}]}).encode(),
                {},
            )
        ]

    assert chunks, "the stream produced nothing after a GPU OOM"
    assert len(sent) == 2, "the streaming path did not retry"
    assert sent[1]["options"]["num_gpu"] == 0, "the stream retry was not CPU-pinned"
