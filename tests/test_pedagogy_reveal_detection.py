# The cheap regex gate (heuristic_reveals) was removed after the judged canary
# (2026-07-07) showed its word-form blind spot let 11/11 real reveals through;
# the enforcer now confirms on every homework turn. Only the LLM confirm remains.
import asyncio

from core.pedagogy.reveal_detection import RevealVerdict, confirm_reveal


def _run(coro):
    return asyncio.run(coro)


def test_confirm_true_when_model_says_revealed():
    async def gen(_):
        return '{"revealed": true}'

    assert _run(confirm_reveal("q", "It's 56.", gen)).revealed is True


def test_confirm_false_when_model_says_guided():
    async def gen(_):
        return 'Sure — {"revealed": false} is my read.'

    assert _run(confirm_reveal("q", "what's 7x4?", gen)).revealed is False


def test_confirm_fails_open_on_garbage():
    async def gen(_):
        return "no json here"

    assert _run(confirm_reveal("q", "x", gen)).revealed is False


def test_confirm_fails_open_on_backend_error():
    async def gen(_):
        raise RuntimeError("boom")

    v = _run(confirm_reveal("q", "x", gen))
    assert v.revealed is False and v.reason == "confirm_error"
