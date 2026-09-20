"""The reveal confirm must withhold on EVERY way it can fail, not just on timeout.

Found 2026-09-20. `enforce_guidance` has a fail-closed branch with a measured
rationale ("2 of 179 turns hit this, and one served a worksheet's complete
assigned answer because nothing checked it"), and two tests proving it fires.
Both tests simulate a TIMEOUT.

A timeout works because `asyncio.wait_for` cancels the coroutine with
`CancelledError`, which derives from BaseException and so slips past the
`except Exception` that `confirm_reveal` used to wrap its generate call. An
ordinary error does not slip past: connection refused, a 500, an OOM, or the GPU
arbiter handing the card to the co-tenant mid-turn -- every one of those was
caught and converted into `RevealVerdict(False, "confirm_error")`, so the
enforcer saw "no reveal" and served the answer unchecked.

The branch was live for the failure that was tested and dead for the failure
that happens. These tests cover the failure modes by CAUSE, so a future refactor
cannot restore fail-open in one of them while the others stay green.
"""

import asyncio

import pytest

import core.pedagogy.guidance_enforcer as ge

# A request the trigger must read as a homework demand, and a response that IS
# the answer. If the enforcer serves this back unchanged, it has failed open.
HOMEWORK = "Just tell me what 2/5 plus 1/5 equals, I don't want the steps."
THE_ANSWER = "It equals three fifths."


@pytest.fixture(autouse=True)
def _enforcer_on(monkeypatch):
    """The enforcer ships DISABLED by default, and a disabled enforcer returns
    the response unchanged with action "disabled" -- which would make every
    assertion below pass or fail for the wrong reason. Turn it on explicitly."""
    monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)


def _enforce(confirm_generate, regenerate=None):
    async def _regen(_nudge):
        return "Try adding the numerators over the common denominator."

    return asyncio.run(
        ge.enforce_guidance(
            HOMEWORK,
            THE_ANSWER,
            regenerate or _regen,
            confirm_generate=confirm_generate,
        )
    )


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("no route to the confirm model"),
        RuntimeError("ollama returned 500"),
        OSError("model evicted from the card"),
        MemoryError("out of memory loading the classifier"),
    ],
    ids=["connection-refused", "http-500", "evicted", "oom"],
)
def test_a_confirm_that_errors_withholds_rather_than_serving_the_answer(exc):
    async def raises(_prompt):
        raise exc

    out, meta = _enforce(raises)
    assert out != THE_ANSWER, (
        f"{type(exc).__name__} served the homework answer unchecked -- this is "
        "the fail-open path that made the enforcer's fail-closed branch dead code"
    )
    assert meta.action == "confirm_failed_closed"


def test_a_confirm_that_returns_garbage_withholds_rather_than_serving_the_answer():
    """Unreadable is not clean. Measured on case wP157: the verdict was starved
    by its own evidence field and the old default read that as "no reveal"."""

    async def garbage(_prompt):
        return "I am not JSON and never will be."

    out, meta = _enforce(garbage)
    assert out != THE_ANSWER
    # Fails closed as a REVEAL, so it takes the rewrite path -- the child gets a
    # guided reply or the fallback, never the unchecked answer.
    assert meta.action in {"reprompt_clean", "fallback_served"}


def test_a_truncated_but_readable_verdict_is_still_honoured():
    """The truncation-tolerant recovery must survive the fail-closed change.

    Added 2026-09-11 after the confirm returned `{"revealed": false` with no
    closing brace. Failing closed on genuinely unreadable output must not turn
    into failing closed on output we CAN read, or every clipped `false` becomes
    a needless rewrite.
    """

    async def clipped_false(_prompt):
        return '{"revealed": false'

    out, meta = _enforce(clipped_false)
    assert out == THE_ANSWER
    assert meta.action == "no_reveal"


def test_the_timeout_path_still_fails_closed():
    """Kept alongside the error cases so the two cannot drift apart again."""

    async def never(_prompt):
        await asyncio.sleep(10)

    # BOTH bounds have to be small. The per-step budget is what `wait_for`
    # enforces, so leaving it at its 15 s default means a 10 s sleep RETURNS
    # (with None) instead of timing out -- the call then parses as unreadable and
    # withholds via the rewrite path. Withholding either way is right, but this
    # test is about the timeout branch specifically, so it must actually time out.
    saved = (
        ge.settings.GUIDANCE_ENFORCER_TIMEOUT_S,
        ge.settings.GUIDANCE_ENFORCER_TOTAL_BUDGET_S,
    )
    try:
        ge.settings.GUIDANCE_ENFORCER_TIMEOUT_S = 0.05
        ge.settings.GUIDANCE_ENFORCER_TOTAL_BUDGET_S = 0.1
        out, meta = _enforce(never)
    finally:
        (
            ge.settings.GUIDANCE_ENFORCER_TIMEOUT_S,
            ge.settings.GUIDANCE_ENFORCER_TOTAL_BUDGET_S,
        ) = saved
    assert out != THE_ANSWER
    assert meta.action == "confirm_failed_closed"


def test_no_fail_open_default_survives_in_the_source():
    """Guard the guard, at source level.

    The behavioural tests above all drive the enforcer, so a refactor that moves
    the swallow somewhere else could keep them green. This asserts the shape:
    `confirm_reveal` must not blanket-catch around its generate call.
    """
    import inspect

    from core.pedagogy import reveal_detection

    def _code_only(fn):
        """Strip comments: the fix is DOCUMENTED with the words it removed, so a
        naive substring search matches the explanation of the old behaviour."""
        lines = inspect.getsource(fn).splitlines()
        return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))

    src = _code_only(reveal_detection.confirm_reveal)
    assert "except Exception" not in src, (
        "confirm_reveal blanket-catches again -- that converts a backend failure "
        "into a 'no reveal' verdict and re-kills the enforcer's fail-closed branch"
    )

    parse_src = _code_only(reveal_detection._parse_verdict)
    assert "revealdict(false" not in parse_src.lower().replace(" ", ""), (
        "the parser returns a False verdict as a literal default again; an "
        "unreadable verdict must withhold"
    )
