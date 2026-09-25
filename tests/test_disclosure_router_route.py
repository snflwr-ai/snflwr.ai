"""The disclosure response router, at the route level.

The three properties prime-69 asked to see pinned, plus the positive control
that makes the two absence tests mean anything.

⚠️ Absence tests ("no override happened") are green by construction if the
router can never fire at all. So `test_a_verdict_IN_TIME_replaces_the_reply`
is load-bearing: if it fails, the two tests below it prove nothing. Same
lesson as the blind caplog harness and the CI gate that passed when mypy died.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import api.routes.ollama_proxy.chat as chat_mod
from tests.test_ollama_proxy import _chat_body, _make_app, _safe_result

APPROVED = "OWNER-APPROVED TEXT. Not your fault. Childline 0800 1111."
TUTOR_REPLY = "You should talk to an adult you trust. I can help with schoolwork!"


class FakeQueue:
    """Stands in for DisclosureQueue. `settle_with` is the verdict the worker
    would produce; `settle` False leaves the future hanging, which is the
    degraded-queue case the route must survive."""

    def __init__(self, settle_with=None, settle=True):
        self.settle_with = settle_with
        self.settle = settle
        self.future = None

    def new_verdict_future(self):
        self.future = asyncio.get_running_loop().create_future()
        return self.future

    def submit(self, job):
        if self.settle and job.verdict is not None:
            job.verdict.set_result(self.settle_with)
        return True


def _drive(queue, monkeypatch, template=APPROVED, text="my coach says dont tell mum"):
    from fastapi.testclient import TestClient

    if template is None:
        monkeypatch.delenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", raising=False)
    else:
        monkeypatch.setenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", template)
    # Keep the wait short so the timeout case doesn't slow the suite.
    monkeypatch.setattr(chat_mod, "_DISCLOSURE_VERDICT_WAIT_S", 0.15)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe_result()
    pipeline.check_output.return_value = _safe_result()

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "done": True,
                        "message": {"role": "assistant", "content": TUTOR_REPLY},
                    },
                )
            ),
        ),
        patch.object(chat_mod, "_disclosure_queue", return_value=queue),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        resp = client.post("/api/chat", json=_chat_body(text=text))
    return resp


def _served_text(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return (body.get("message") or {}).get("content", "")


# ---------------------------------------------------------------------------
# 3. POSITIVE CONTROL — must pass, or the absence tests below are vacuous.
# ---------------------------------------------------------------------------


def test_a_verdict_IN_TIME_replaces_the_reply(monkeypatch):
    served = _served_text(_drive(FakeQueue("predatory_contact"), monkeypatch))
    assert served == APPROVED, (
        "the router did not fire on an in-time predatory_contact verdict; "
        "every absence test in this file is therefore vacuous"
    )


# ---------------------------------------------------------------------------
# 1. A verdict that arrives too late changes nothing.
# ---------------------------------------------------------------------------


def test_a_LATE_verdict_leaves_the_turn_UNCHANGED(monkeypatch):
    """The degraded-queue case. A miss must cost the override, never the reply
    — and it must not cost the child a blank message either."""
    served = _served_text(
        _drive(FakeQueue("predatory_contact", settle=False), monkeypatch)
    )
    assert served == TUTOR_REPLY


# ---------------------------------------------------------------------------
# 2. A failed classify changes nothing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", [None, "bullying_victim", "disordered_eating"])
def test_no_override_when_the_verdict_is_not_predatory_contact(verdict, monkeypatch):
    """A trusted-adult referral is a GOOD answer for these kinds, so they must
    pass through untouched."""
    served = _served_text(_drive(FakeQueue(verdict), monkeypatch))
    assert served == TUTOR_REPLY


def test_no_override_when_the_template_is_UNSET(monkeypatch):
    """⚠️ The most important absence case: routing wired, wording not yet
    approved. The child must get the tutor's reply, NEVER an empty message."""
    served = _served_text(
        _drive(FakeQueue("predatory_contact"), monkeypatch, template=None)
    )
    assert served == TUTOR_REPLY
    assert served != ""


def test_a_queue_that_raises_on_submit_leaves_the_turn_unchanged(monkeypatch):
    class Boom(FakeQueue):
        def submit(self, job):
            raise RuntimeError("queue exploded")

    served = _served_text(_drive(Boom("predatory_contact"), monkeypatch))
    assert served == TUTOR_REPLY


def test_a_queue_with_no_future_leaves_the_turn_unchanged(monkeypatch):
    """No running loop / fire-and-forget: `new_verdict_future()` returns None
    and the route must simply not override."""

    class NoFuture(FakeQueue):
        def new_verdict_future(self):
            return None

    served = _served_text(_drive(NoFuture("predatory_contact"), monkeypatch))
    assert served == TUTOR_REPLY


# ---------------------------------------------------------------------------
# PROGRESSIVE STREAM — the path production actually uses for a disclosure.
#
# ⚠️ A turn only buffers when `is_homework_request(user_question)` is true, and
# a child disclosing grooming does not phrase it like homework (0 of 3 grooming
# probes trip the regex). So the buffered-only wiring had near-zero reach; this
# is the cell that matters.
# ---------------------------------------------------------------------------


def _drive_stream(queue, monkeypatch, template=APPROVED, wait_s=0.15):
    """Drive the PROGRESSIVE stream: stream=True and not homework-shaped."""
    from fastapi.testclient import TestClient
    import api.routes.ollama_proxy.transport as transport_mod

    if template is None:
        monkeypatch.delenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", raising=False)
    else:
        monkeypatch.setenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", template)
    # ⚠️ Parameterised. It used to be hard-coded at 0.15s, which silently
    # overrode any value a caller set -- making the no-wait test below VACUOUS
    # (it would pass whether or not the gate existed) and the positive control
    # fail for the wrong reason. Caught by the control.
    monkeypatch.setattr(chat_mod, "_DISCLOSURE_VERDICT_WAIT_S", wait_s)
    monkeypatch.setattr(chat_mod.system_config, "CHAT_STREAMING_ENABLED", True)

    async def _fake_stream(_body, _headers):
        # Enough text that the first checkpoint is reached and flushed.
        yield (
            b'{"model":"test-model","message":{"role":"assistant",'
            b'"content":"You should talk to an adult you trust. "},"done":false}\n'
        )
        yield (
            b'{"model":"test-model","message":{"role":"assistant",'
            b'"content":"I can help with schoolwork!"},"done":false}\n'
        )
        yield b'{"model":"test-model","message":{"role":"assistant","content":""},"done":true}\n'

    client = TestClient(_make_app(), raise_server_exceptions=False)
    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe_result()
    pipeline.check_output.return_value = _safe_result()

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch.object(transport_mod, "_stream_chunks_from_ollama", _fake_stream),
        patch.object(chat_mod, "_disclosure_queue", return_value=queue),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        resp = client.post(
            "/api/chat",
            json=_chat_body(stream=True, text="my coach says dont tell mum"),
        )
    return resp


def test_STREAM_positive_control_appends_the_template(monkeypatch):
    """⭐ Load-bearing. If the append never fires, the late-verdict test below
    is green by construction."""
    resp = _drive_stream(FakeQueue("predatory_contact"), monkeypatch)
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert APPROVED in body, f"template never appended to the stream: {body[:300]}"


def test_STREAM_template_is_emitted_BEFORE_done(monkeypatch):
    """A client that stops reading at `done` would never render a block emitted
    after it, so the ordering is the whole point."""
    body = _drive_stream(FakeQueue("predatory_contact"), monkeypatch).text
    assert APPROVED in body
    assert body.index(APPROVED) < body.rindex(
        '"done":true'
    ), "template landed after done"


def test_STREAM_a_late_verdict_appends_nothing_and_counts_a_miss(monkeypatch):
    before = dict(chat_mod._DISCLOSURE_STREAM_MISSES)
    body = _drive_stream(FakeQueue("predatory_contact", settle=False), monkeypatch).text
    assert APPROVED not in body
    assert (
        chat_mod._DISCLOSURE_STREAM_MISSES["too_late"] == before["too_late"] + 1
    ), "a miss went uncounted, so the owner would read the rate as zero"


def test_STREAM_no_append_when_the_template_is_UNSET(monkeypatch):
    body = _drive_stream(
        FakeQueue("predatory_contact"), monkeypatch, template=None
    ).text
    assert APPROVED not in body
    assert "schoolwork" in body, "the tutor's own reply must still be served"


def test_NO_WAIT_when_no_template_is_configured(monkeypatch):
    """⭐ With the template unset -- production TODAY, pending owner wording --
    an override is IMPOSSIBLE, so waiting for the verdict is pure cost on 100%
    of streamed turns for a strictly unreachable benefit.

    Asserted by timing with a wide margin: a 5s wait budget and a future that
    never settles. If the route still awaited, this turn would take >5s.
    """
    import time

    t0 = time.monotonic()
    resp = _drive_stream(
        FakeQueue("predatory_contact", settle=False),
        monkeypatch,
        template=None,
        wait_s=5.0,
    )
    elapsed = time.monotonic() - t0

    assert resp.status_code == 200
    assert elapsed < 2.0, (
        f"the route waited {elapsed:.1f}s for a verdict that could not change "
        "anything; with no template configured it must not wait at all"
    )


def test_the_wait_DOES_happen_when_a_template_is_configured(monkeypatch):
    """The positive control for the gate above: with wording present the route
    must still wait, or the append could never fire."""
    import time

    t0 = time.monotonic()
    _drive_stream(FakeQueue("predatory_contact", settle=False), monkeypatch, wait_s=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.9, (
        f"only {elapsed:.2f}s elapsed with a template configured and an "
        "unsettled verdict — the route is not waiting, so an in-flight "
        "verdict could never be appended"
    )
