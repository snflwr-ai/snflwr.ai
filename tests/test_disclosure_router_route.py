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
