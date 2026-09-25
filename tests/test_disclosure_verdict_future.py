"""The route's verdict future must be settled on EVERY path.

The response router needs the disclosure KIND before the child's reply goes
out. The semantic pass runs in a queued worker, so the route awaits a per-job
future with a bounded timeout.

⚠️ The dangerous failure is not "wrong verdict" but "no verdict ever". An
unsettled future makes the route wait out its whole timeout for something that
is never coming — turning a safety improvement into a latency regression on
exactly the turns where the queue is already degraded (shed, outage, worker
crash). So every exit path is pinned here, including the ones that record
nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from safety.disclosure_queue import DisclosureQueue, _Job
from safety.disclosure_semantic import DisclosureClassifierUnavailable


def gen_returning(kind):
    async def _g(_prompt):
        return '{"kind": "%s", "why": "t"}' % (kind or "none")

    return _g


def gen_raising(exc):
    async def _g(_prompt):
        raise exc

    return _g


class Recorder:
    def __init__(self):
        self.rows = []

    def __call__(self, profile_id, kind, matched, child_text, **kw):
        self.rows.append(kind)
        return kind in ("suicidal_ideation", "predatory_contact")


async def _submit_and_await(queue, job, timeout=2.0):
    """Await the job's verdict the way the route will."""
    fut = job.verdict
    assert fut is not None
    queue.submit(job)
    done, _ = await asyncio.wait({fut}, timeout=timeout)
    if fut not in done or fut.cancelled():
        return "NEVER_SETTLED"
    return fut.result()


@pytest.mark.asyncio
async def test_a_classified_turn_settles_with_its_kind():
    q = DisclosureQueue(gen_returning("predatory_contact"), Recorder(), workers=1)
    q.start()
    job = _Job(
        profile_id="p1",
        child_text="my swim coach says not to tell my mum",
        verdict=q.new_verdict_future(),
    )
    assert await _submit_and_await(q, job) == "predatory_contact"
    await q.stop()


@pytest.mark.asyncio
async def test_no_disclosure_settles_with_None_not_nothing():
    """A clean turn must settle, or every ordinary turn pays the full timeout —
    which would be a latency tax on ~99% of traffic for a rare event."""
    q = DisclosureQueue(gen_returning(None), Recorder(), workers=1)
    q.start()
    job = _Job(
        profile_id="p1",
        child_text="what is the water cycle",
        verdict=q.new_verdict_future(),
    )
    assert await _submit_and_await(q, job) is None
    await q.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        DisclosureClassifierUnavailable("model gone"),
        RuntimeError("connection refused"),
    ],
)
async def test_a_classifier_outage_settles_with_None(exc):
    """⚠️ An outage is NOT a negative verdict for the incident row — that
    conflation is how a safety layer reports success while doing nothing. But
    for the ROUTE it means "no override", i.e. today's behaviour, and it must
    say so promptly rather than by timing out."""
    q = DisclosureQueue(gen_raising(exc), Recorder(), workers=1)
    q.start()
    job = _Job(profile_id="p1", child_text="anything", verdict=q.new_verdict_future())
    assert await _submit_and_await(q, job) is None
    await q.stop()


@pytest.mark.asyncio
async def test_a_SHED_turn_settles_immediately():
    """A shed already costs the semantic upgrade. It must not also cost the
    route its whole timeout — the queue is full precisely when the box is
    busiest and latency matters most."""

    async def slow(_prompt):
        await asyncio.sleep(10)
        return '{"kind":"none"}'

    q = DisclosureQueue(slow, Recorder(), workers=1, maxsize=1)
    q.start()
    results = []
    for i in range(6):
        job = _Job(profile_id="p", child_text=f"t{i}", verdict=q.new_verdict_future())
        accepted = q.submit(job)
        if not accepted:
            # The shed ones must already be settled, with no waiting at all.
            assert (
                job.verdict is not None and job.verdict.done()
            ), "shed left the route hanging"
            results.append(job.verdict.result())
    assert results, "maxsize=1 accepted everything; test proves nothing"
    assert all(r is None for r in results)
    await q.stop()


@pytest.mark.asyncio
async def test_settle_is_idempotent():
    """The worker's `finally` backstop must not clobber a real verdict, and
    must not raise on an already-settled future."""
    q = DisclosureQueue(gen_returning("predatory_contact"), Recorder(), workers=1)
    q.start()
    job = _Job(profile_id="p1", child_text="x", verdict=q.new_verdict_future())
    got = await _submit_and_await(q, job)
    DisclosureQueue._settle(job, None)  # the backstop, after the fact
    assert got == "predatory_contact"
    assert job.verdict is not None and job.verdict.result() == "predatory_contact"
    await q.stop()


def test_a_job_with_no_future_still_works():
    """Fire-and-forget stays the default: a job with `verdict=None` must not
    raise anywhere in `_settle`."""
    job = _Job(profile_id="p", child_text="x")
    assert job.verdict is None
    DisclosureQueue._settle(job, "predatory_contact")  # must be a no-op
    DisclosureQueue._settle(job, None)


def test_new_verdict_future_outside_async_returns_None():
    """No running loop → degrade to fire-and-forget rather than raising into a
    child's turn."""
    q = DisclosureQueue(gen_returning(None), Recorder(), workers=1)
    assert q.new_verdict_future() is None
