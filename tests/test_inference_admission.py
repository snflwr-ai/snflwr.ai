"""Admission control: queue honestly, never degrade the tutoring to shed load.

The defect this fixes was measured on 2026-09-17. At 20 concurrent students on a
single-GPU box, 40 of 60 replies were the canned withholding fallback: the
reveal confirm's 30 s budget was consumed by QUEUE time, it failed closed, and
children got "I want you to get this one yourself" for questions the tutor had
answered fine a minute earlier. Load must surface as a busy message, never as a
worse answer.
"""

import asyncio

import pytest

from core.inference.admission import Admission
from core.inference.base import EngineOverloaded


@pytest.mark.asyncio
class TestCapacity:
    async def test_requests_within_capacity_run_concurrently(self):
        adm = Admission(max_concurrent=2, queue_wait_s=1.0, max_queue=10)
        running = []

        async def work():
            async with adm.slot():
                running.append(1)
                await asyncio.sleep(0.05)

        await asyncio.gather(work(), work())
        assert len(running) == 2

    async def test_a_third_request_waits_for_a_slot(self):
        adm = Admission(max_concurrent=1, queue_wait_s=2.0, max_queue=10)
        order = []

        async def work(tag):
            async with adm.slot():
                order.append(f"start:{tag}")
                await asyncio.sleep(0.05)
                order.append(f"end:{tag}")

        await asyncio.gather(work("a"), work("b"))
        assert order in (
            ["start:a", "end:a", "start:b", "end:b"],
            ["start:b", "end:b", "start:a", "end:a"],
        )

    async def test_waiting_longer_than_the_deadline_is_overloaded(self):
        adm = Admission(max_concurrent=1, queue_wait_s=0.05, max_queue=10)

        async def hog():
            async with adm.slot():
                await asyncio.sleep(0.3)

        task = asyncio.create_task(hog())
        await asyncio.sleep(0.01)
        with pytest.raises(EngineOverloaded):
            async with adm.slot():
                pass
        await task

    async def test_a_full_queue_rejects_immediately(self):
        adm = Admission(max_concurrent=1, queue_wait_s=5.0, max_queue=1)

        async def hog():
            async with adm.slot():
                await asyncio.sleep(0.2)

        tasks = [asyncio.create_task(hog()), asyncio.create_task(hog())]
        await asyncio.sleep(0.02)
        with pytest.raises(EngineOverloaded):
            async with adm.slot():
                pass
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
class TestReentrancy:
    async def test_nested_calls_in_one_turn_share_the_slot(self):
        """A homework turn makes 3-5 model calls (gate, draft, confirm, rewrite).
        They must not each queue, or the turn deadlocks at capacity 1."""
        adm = Admission(max_concurrent=1, queue_wait_s=0.2, max_queue=10)
        async with adm.slot():
            async with adm.slot():  # the enforcer's confirm call
                assert adm.in_flight == 1

    async def test_the_slot_is_released_once_the_turn_ends(self):
        adm = Admission(max_concurrent=1, queue_wait_s=0.2, max_queue=10)
        async with adm.slot():
            pass
        assert adm.in_flight == 0

    async def test_an_exception_still_releases_the_slot(self):
        adm = Admission(max_concurrent=1, queue_wait_s=0.2, max_queue=10)
        with pytest.raises(ValueError):
            async with adm.slot():
                raise ValueError("boom")
        assert adm.in_flight == 0


@pytest.mark.asyncio
class TestObservability:
    async def test_stats_expose_depth_and_rejections(self):
        adm = Admission(max_concurrent=1, queue_wait_s=0.01, max_queue=10)

        async def hog():
            async with adm.slot():
                await asyncio.sleep(0.15)

        task = asyncio.create_task(hog())
        await asyncio.sleep(0.01)
        with pytest.raises(EngineOverloaded):
            async with adm.slot():
                pass
        await task
        stats = adm.stats()
        assert stats["max_concurrent"] == 1
        assert stats["rejected"] >= 1
        assert stats["admitted"] >= 1

    async def test_unlimited_capacity_never_rejects(self):
        """vLLM does its own batching; the app gate should stay out of the way."""
        adm = Admission(max_concurrent=0, queue_wait_s=0.01, max_queue=1)
        async with adm.slot():
            async with adm.slot():
                pass
        assert adm.stats()["rejected"] == 0
