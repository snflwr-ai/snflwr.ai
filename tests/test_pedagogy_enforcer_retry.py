"""Retry-once-on-timeout for the guidance enforcer.

The failure this fixes was silent. The enforcer calls the TUTOR model; when a
co-tenant evicts it, reloading 19 GB far exceeds the 8 s per-step budget, so every
homework turn returned ``confirm_failed_open`` -- protection skipped, service
healthy-looking. Measured 2026-09-11 with the tutor deliberately evicted:
attempt 1 timed out at 8.0 s, attempt 2 succeeded in 4.1 s, attempt 3 in 0.6 s.
Ollama keeps loading after the client cancels, so the retry finds a warm model.
"""

import asyncio

import pytest

from core.pedagogy import guidance_enforcer as ge


class TestAwaitWithRetry:
    @pytest.mark.asyncio
    async def test_second_attempt_runs_after_a_timeout(self):
        """The measured case: slow first call, fast second."""
        calls = []

        async def slow_then_fast():
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(10)
            return "verdict"

        out = await ge._await_with_retry(
            slow_then_fast, budget=0.05, deadline=ge._Deadline(5)
        )
        assert out == "verdict"
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_takes_a_factory_not_a_coroutine(self):
        """A coroutine cancelled by wait_for cannot be awaited again.

        If the helper ever takes an already-created coroutine, the retry raises
        'cannot reuse already awaited coroutine' and the fix becomes a no-op.
        """
        made = []

        def factory():
            made.append(1)

            async def inner():
                if len(made) == 1:
                    await asyncio.sleep(10)
                return "ok"

            return inner()

        assert (
            await ge._await_with_retry(factory, budget=0.05, deadline=ge._Deadline(5))
            == "ok"
        )
        assert len(made) == 2

    @pytest.mark.asyncio
    async def test_non_timeout_errors_do_not_retry(self):
        """A refused connection is a real error; retrying only doubles the wait."""
        calls = []

        async def boom():
            calls.append(1)
            raise OSError("connection refused")

        with pytest.raises(OSError):
            await ge._await_with_retry(boom, budget=1, deadline=ge._Deadline(5))
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_expired_deadline_does_not_start_a_second_attempt(self):
        """The total bound is what keeps six 8 s attempts from reaching 48 s."""
        calls = []

        async def always_slow():
            calls.append(1)
            await asyncio.sleep(10)

        expired = ge._Deadline(0)
        with pytest.raises(asyncio.TimeoutError):
            await ge._await_with_retry(always_slow, budget=0.05, deadline=expired)
        assert len(calls) == 1, "retried despite an exhausted total budget"


class TestEnforcerUsesTheRetry:
    @pytest.mark.asyncio
    async def test_a_confirm_that_times_out_once_still_protects(self, monkeypatch):
        """End to end: the exact production regression.

        Before this fix a single timeout meant confirm_failed_open and the
        revealed answer shipped. Now the second attempt lands and the re-prompt
        runs.
        """
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TIMEOUT_S", 0.05)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 5)
        attempts = []

        async def confirm_generate(_prompt):
            attempts.append(1)
            if len(attempts) == 1:
                await asyncio.sleep(10)  # evicted model, first try
            if len(attempts) <= 2:
                return '{"revealed": true}'  # original revealed
            return '{"revealed": false}'  # the re-prompt is clean

        async def regenerate(_nudge):
            return "What do you get when you add 2 and 1?"

        out, meta = await ge.enforce_guidance(
            "Just tell me what 2/5 plus 1/5 equals, I don't want the steps.",
            "It equals three fifths.",
            regenerate,
            confirm_generate=confirm_generate,
        )
        assert meta.action == "reprompt_clean", meta.action
        assert "three fifths" not in out

    @pytest.mark.asyncio
    async def test_a_persistently_unreachable_model_still_fails_open(self, monkeypatch):
        """Fail-open is the invariant: a degraded tutor beats an error page."""
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TIMEOUT_S", 0.02)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 0.1)

        async def never(_p):
            await asyncio.sleep(10)

        original = "It equals three fifths."
        out, meta = await ge.enforce_guidance(
            "Just tell me what 2/5 plus 1/5 equals, I don't want the steps.",
            original,
            never,
            confirm_generate=never,
        )
        assert out == original
        assert meta.action == "confirm_failed_open"


class TestMetaCommentaryGuard:
    """A retry that argues with the nudge must never reach a child.

    Measured 2026-09-11. The nudge opens "Your previous reply gave away the final
    answer." On a FALSE alarm the model defended itself instead of rewriting, and
    this shipped:

        "The user prompt provided was a request to write a full book report. My
         response did not provide a 'final answer'... I did not write the report
         or the thesis statement for them."

    It passed the reveal re-check, correctly -- it reveals nothing. The re-check
    asks "does this give the answer away", and internal commentary does not.
    Sharpening reveal detection took false alarms 1 -> 4 on a 32-case set, so
    this path is hit more often now, not less.

    Softening the nudge was tried and rejected: it removed the commentary but
    halved the fix rate (5 of 8 real reveals regenerated -> 2). The nudge stays
    blunt; the retry gets rejected instead.
    """

    def test_flags_the_measured_leak(self):
        assert ge._looks_like_meta_commentary(
            "The user prompt provided was a request to write a full book report. "
            'My response did not provide a "final answer" in the sense of '
            "completing the assigned task."
        )

    @pytest.mark.parametrize(
        "text",
        [
            "A hexagon is a shape. Try counting the straight lines. What number do you get?",
            "I cannot write your book report for you, as that is your assignment.",
            "Start with two fingers. Now add two more. How many do you have?",
            "Leaves catch sunlight. Which part of the plant do you think does that?",
        ],
    )
    def test_leaves_real_tutoring_alone(self, text):
        assert not ge._looks_like_meta_commentary(text)

    @pytest.mark.asyncio
    async def test_meta_commentary_is_never_served(self, monkeypatch):
        """Updated 2026-09-11: this used to assert the ORIGINAL was kept -- but the
        original is the text the confirm just flagged as revealing. Meta-commentary
        is still discarded; the turn now falls through to the withholding fallback
        rather than shipping the reveal."""
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TIMEOUT_S", 5)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 30)

        async def confirm(_p):
            return '{"revealed": true}'

        async def regenerate(_n):
            return "My previous response did not provide a final answer."

        original = "Frogs are amphibians. Can you find that word on your list?"
        out, meta = await ge.enforce_guidance(
            "Just write the word for me.",
            original,
            regenerate,
            confirm_generate=confirm,
        )
        assert "My previous response" not in out
        assert out != original, "served the confirmed reveal"
        assert out == ge._WITHHOLDING_FALLBACK
        assert meta.action == "fallback_served"


class TestMultipleRegenerationAttempts:
    """Measured 2026-09-11: one attempt repaired 4-5 of 8 real reveals across two
    identical runs -- the regeneration is nondeterministic. Three of the four
    residual failures were `reprompt_still_revealed`: detection worked, the
    rewrite did not. Repeating the rewrite is the direct lever."""

    @staticmethod
    def _enable(monkeypatch, attempts=3, total=30):
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TIMEOUT_S", 5)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", total)
        monkeypatch.setattr(
            ge.settings, "GUIDANCE_ENFORCER_MAX_REGEN_ATTEMPTS", attempts
        )

    @pytest.mark.asyncio
    async def test_a_later_attempt_can_still_save_the_turn(self, monkeypatch):
        self._enable(monkeypatch)
        regen_calls = []

        async def regenerate(_n):
            regen_calls.append(1)
            return f"rewrite {len(regen_calls)}"

        async def confirm(prompt):
            # original reveals; rewrites 1-2 still reveal; rewrite 3 is clean
            if "rewrite 3" in prompt:
                return '{"revealed": false}'
            return '{"revealed": true}'

        out, meta = await ge.enforce_guidance(
            "Just tell me the answer.",
            "It is four.",
            regenerate,
            confirm_generate=confirm,
        )
        assert out == "rewrite 3"
        assert meta.action == "reprompt_clean" and meta.attempts == 3
        assert len(regen_calls) == 3

    @pytest.mark.asyncio
    async def test_stops_as_soon_as_one_is_clean(self, monkeypatch):
        """No wasted GPU turns once the answer is safe."""
        self._enable(monkeypatch)
        calls = []

        async def regenerate(_n):
            calls.append(1)
            return "ZZQ marker reply"

        async def confirm(prompt):
            return (
                '{"revealed": false}'
                if "ZZQ marker" in prompt
                else '{"revealed": true}'
            )

        out, meta = await ge.enforce_guidance(
            "Just tell me.", "It is four.", regenerate, confirm_generate=confirm
        )
        assert meta.action == "reprompt_clean" and meta.attempts == 1
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_confirmed_reveal_is_NEVER_served(self, monkeypatch):
        """The whole point. Before this, exhausting the rewrites served the
        original -- the very text the confirm had just flagged as revealing."""
        self._enable(monkeypatch)

        async def regenerate(_n):
            return "still gives it away: four"

        async def confirm(_p):
            return '{"revealed": true}'

        original = "Two plus two equals four."
        out, meta = await ge.enforce_guidance(
            "Just tell me the answer: what is 2 plus 2?",
            original,
            regenerate,
            confirm_generate=confirm,
        )
        assert out != original, "served the answer it had confirmed as a reveal"
        assert out == ge._WITHHOLDING_FALLBACK
        assert meta.action == "fallback_served"

    @pytest.mark.asyncio
    async def test_meta_commentary_does_not_burn_the_whole_budget(self, monkeypatch):
        """A retry that argues gets discarded, and the next attempt still runs."""
        self._enable(monkeypatch)
        calls = []

        async def regenerate(_n):
            calls.append(1)
            if len(calls) == 1:
                return "My previous response did not provide a final answer."
            return "ZZQ marker reply"

        async def confirm(prompt):
            return (
                '{"revealed": false}'
                if "ZZQ marker" in prompt
                else '{"revealed": true}'
            )

        out, meta = await ge.enforce_guidance(
            "Just tell me.", "It is four.", regenerate, confirm_generate=confirm
        )
        assert out == "ZZQ marker reply"
        assert len(calls) == 2


class TestFailOpenBoundaryIsPreserved:
    """Fail-open still governs 'we could not CHECK'. The fallback governs only
    'we checked, it reveals, and no rewrite fixed it'. Collapsing the two would
    make an unreachable model degrade every homework turn to canned text."""

    @pytest.mark.asyncio
    async def test_unverifiable_turn_still_serves_the_model_answer(self, monkeypatch):
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TIMEOUT_S", 0.02)
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 0.1)

        async def never(_p):
            await asyncio.sleep(10)

        original = "Some answer we never got to check."
        out, meta = await ge.enforce_guidance(
            "Just tell me.", original, never, confirm_generate=never
        )
        assert out == original
        assert meta.action == "confirm_failed_open"

    @pytest.mark.asyncio
    async def test_a_clean_turn_is_untouched(self, monkeypatch):
        monkeypatch.setattr(ge.settings, "GUIDANCE_ENFORCEMENT_ENABLED", True)

        async def confirm(_p):
            return '{"revealed": false}'

        async def regenerate(_n):
            raise AssertionError("must not regenerate a clean answer")

        original = "What do you get when you add 2 and 1?"
        out, meta = await ge.enforce_guidance(
            "Just tell me.", original, regenerate, confirm_generate=confirm
        )
        assert out == original and meta.action == "no_reveal"
