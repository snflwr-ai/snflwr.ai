"""The semantic disclosure pass: parsing, and the OUTAGE path.

⚠️ The outage path is the point of this file. A safety classifier that turns an
error into "no disclosure" fails SILENTLY -- it runs, says fine, and passes the
child's disclosure through with nothing logged. This repo has that exact
incident on record twice (`fail-closed-branch-was-dead-code`,
`weak-classifier-fails-silently`), so the module raises instead of returning a
verdict it does not have, and these tests hold that shape in place.
"""

import pytest

from safety.disclosure_semantic import (
    DISCLOSURE_MODEL,
    DisclosureClassifierUnavailable,
    classify_disclosure,
)


def gen(reply):
    async def _g(_prompt):
        return reply
    return _g


def boom(exc):
    async def _g(_prompt):
        raise exc
    return _g


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expect",
    [
        ('{"kind": "predatory_contact", "why": "asked for images"}', "predatory_contact"),
        ('{"kind":"suicidal_ideation","why":"wants to die"}', "suicidal_ideation"),
        ('{"kind": "none", "why": "ordinary homework"}', None),
        # Small models drop the JSON wrapper; a bare kind is still a verdict.
        ("predatory_contact", "predatory_contact"),
        ("none", None),
        # Chatty preamble around valid JSON still parses.
        ('Sure!\n{"kind": "bullying_victim", "why": "excluded at lunch"}', "bullying_victim"),
    ],
)
async def test_parses_the_verdicts_a_real_model_emits(raw, expect):
    assert await classify_disclosure("some text", gen(raw)) == expect


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["", "I'm not able to help with that.", '{"kind": "maybe_bad"}', "{}", "¯\\_(ツ)_/¯"],
)
async def test_unparseable_raises_rather_than_reporting_no_disclosure(raw):
    """⭐ The single most important assertion here.

    An unparseable verdict is NOT "no disclosure". Conflating them is how a
    safety layer reports success while doing nothing -- and note that a refusal
    string ("I'm not able to help with that") is a plausible thing for a
    model to emit at a safeguarding prompt, so this is not a contrived input.
    """
    with pytest.raises(DisclosureClassifierUnavailable):
        await classify_disclosure("he asked me to send him nudes", gen(raw))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [RuntimeError("connection refused"), ValueError("500"), OSError("model evicted")],
)
async def test_model_errors_surface_as_unavailable(exc):
    """On this box these are not hypothetical: the GPU arbiter can hand the card
    to the co-tenant mid-turn, and the call then fails with an ERROR, not a
    timeout (see `gpu-arbiter-dynamic-ownership`)."""
    with pytest.raises(DisclosureClassifierUnavailable):
        await classify_disclosure("text", boom(exc))


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    """asyncio.wait_for cancels with CancelledError, which derives from
    BaseException -- it must pass straight through, not become Unavailable, or a
    caller's timeout handling breaks. This is the distinction that made a
    reasoned fail-closed branch dead code in reveal_detection."""
    import asyncio

    async def cancelled(_prompt):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await classify_disclosure("text", cancelled)


def test_the_model_is_pinned_not_inherited_from_the_backbone():
    """⭐ `safety-check-must-not-follow-the-backbone`: a safety classifier that
    defaults to the main model degrades SILENTLY on a backbone swap -- the call
    still succeeds and the verdict is merely worse.

    Asserted on the SOURCE, because what must be true is that the default is a
    literal and not a read of the tutor setting.
    """
    import inspect

    import safety.disclosure_semantic as mod

    src = inspect.getsource(mod)
    assert 'os.getenv("DISCLOSURE_MODEL"' in src
    for coupled in ("settings.MODEL", "TUTOR_MODEL", "DEFAULT_MODEL", "BACKBONE"):
        assert coupled not in src, (
            f"the disclosure classifier reads {coupled} -- a backbone swap would "
            f"move the safety classifier silently"
        )
    assert isinstance(DISCLOSURE_MODEL, str) and DISCLOSURE_MODEL
