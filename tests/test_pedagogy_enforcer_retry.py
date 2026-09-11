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

        assert await ge._await_with_retry(
            factory, budget=0.05, deadline=ge._Deadline(5)
        ) == "ok"
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
                await asyncio.sleep(10)          # evicted model, first try
            if len(attempts) <= 2:
                return '{"revealed": true}'      # original revealed
            return '{"revealed": false}'         # the re-prompt is clean

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
            original, never, confirm_generate=never,
        )
        assert out == original
        assert meta.action == "confirm_failed_open"
