"""Exactly ONE disclosure row per turn, and never fewer than the regex floor.

⚠️ This replaces a prefilter GATE that discarded ~60% of disclosures before the
classifier saw them (10/27 and 9/23 on two cold sealed sets, while the
classifier was right on 18 of the 19 turns it was shown). The gate existed to
protect a latency budget that was never measured, and the work is not on the
child's critical path at all -- it only writes an incident row.

The hard part is not the queue. It is that the regex and the semantic pass can
BOTH have an opinion about one turn, and the wrong combination rule produces
either two parent alerts or none.
"""

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
    """Stands in for _record_disclosure_incident."""

    def __init__(self):
        self.rows = []

    def __call__(
        self, profile_id, kind, matched, child_text, blocked=False, block_severity=None
    ):
        self.rows.append(
            dict(
                profile_id=profile_id,
                kind=kind,
                matched=matched,
                child_text=child_text,
                blocked=blocked,
                block_severity=block_severity,
            )
        )
        return kind in ("suicidal_ideation", "predatory_contact")


async def drain(q: DisclosureQueue):
    """Let the workers finish everything queued."""
    assert q._queue is not None
    await q._queue.join()


@pytest.mark.asyncio
async def test_a_turn_the_regex_missed_is_recorded_by_the_classifier():
    rec = Recorder()
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(profile_id="p1", child_text="i just want to go to sleep and never wake up")
    )
    await drain(q)
    assert len(rec.rows) == 1
    assert rec.rows[0]["kind"] == "suicidal_ideation"
    await q.stop()


@pytest.mark.asyncio
async def test_a_minor_regex_hit_is_UPGRADED_not_duplicated():
    """⭐ The case peer review caught, and the reason this design defers.

    The regex picks kinds by pattern order, so a turn like "they bully me every
    day and honestly i don't want to be here anymore" lands as
    `bullying_victim` — MINOR, record-only, alerts NOBODY — where the semantic
    pass says `suicidal_ideation`, which alerts.

    Skipping the semantic call because "the regex already fired" would leave an
    ideation disclosure alerting nobody. Recording BOTH would re-create the
    double-alert #330 fixed. So: one row, the upgraded kind.
    """
    rec = Recorder()
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they bully me every day and honestly i dont want to be here anymore",
            fallback_kind="bullying_victim",
            fallback_matched="bullied",
        )
    )
    await drain(q)
    assert len(rec.rows) == 1, f"expected exactly one row, got {rec.rows}"
    assert (
        rec.rows[0]["kind"] == "suicidal_ideation"
    ), "the minor regex verdict survived and the ideation disclosure alerts nobody"
    assert q.stats.upgraded == 1
    await q.stop()


@pytest.mark.asyncio
async def test_the_regex_floor_survives_a_semantic_NO():
    """The semantic pass is additive, never a veto on the deterministic layer."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning(None), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they keep making fun of me",
            fallback_kind="bullying_victim",
            fallback_matched="making fun",
        )
    )
    await drain(q)
    assert [r["kind"] for r in rec.rows] == ["bullying_victim"]
    assert q.stats.fallback_used == 1
    await q.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc", [RuntimeError("connection refused"), OSError("evicted")]
)
async def test_a_classifier_outage_falls_back_and_is_COUNTED(exc):
    """⚠️ An outage must not look like a quiet corpus. It falls back to the
    regex floor AND increments a counter, so it can be alarmed on."""
    rec = Recorder()
    q = DisclosureQueue(gen_raising(exc), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they keep making fun of me",
            fallback_kind="bullying_victim",
        )
    )
    await drain(q)
    assert [r["kind"] for r in rec.rows] == ["bullying_victim"]
    assert q.stats.unavailable == 1
    await q.stop()


@pytest.mark.asyncio
async def test_an_outage_with_nothing_to_fall_back_on_is_counted_not_silent():
    rec = Recorder()
    q = DisclosureQueue(gen_raising(RuntimeError("down")), rec, workers=1)
    q.start()
    q.submit(_Job(profile_id="p1", child_text="hello"))
    await drain(q)
    assert rec.rows == []
    assert q.stats.unavailable == 1
    assert q.stats.nothing_recorded == 1
    await q.stop()


@pytest.mark.asyncio
async def test_a_full_queue_SHEDS_measurably_and_keeps_the_floor():
    """⚠️ The whole point of a bound. Under saturation the loss is COUNTED and
    the deterministic floor is still written -- unlike a pattern gate, whose
    loss is invisible and permanent."""
    rec = Recorder()

    async def slow(_prompt):
        await asyncio.sleep(10)
        return '{"kind":"none"}'

    q = DisclosureQueue(slow, rec, workers=1, maxsize=1)
    q.start()
    ok = [
        q.submit(
            _Job(profile_id="p", child_text=f"t{i}", fallback_kind="bullying_victim")
        )
        for i in range(6)
    ]
    assert ok.count(False) >= 3, f"a maxsize=1 queue accepted everything: {ok}"
    assert q.stats.shed >= 3
    # Every shed turn still produced its regex row.
    assert len(rec.rows) == q.stats.shed
    await q.stop()


def test_submit_never_raises_into_a_childs_turn():
    """No loop running, so start() will fail. It must still not raise."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning(None), rec, workers=1)
    assert (
        q.submit(_Job(profile_id="p", child_text="hi", fallback_kind="bullying_victim"))
        is False
    )
    # and the floor was still recorded
    assert [r["kind"] for r in rec.rows] == ["bullying_victim"]


@pytest.mark.asyncio
async def test_only_the_childs_text_reaches_the_classifier():
    """#331's rule, restated here: the crisis and severity decisions must never
    read the model's output. The worker is only handed the child's turn, and
    this pins it so a future edit cannot helpfully add the reply 'for context'."""
    seen = {}

    async def spy(prompt):
        seen["prompt"] = prompt
        return '{"kind":"none"}'

    rec = Recorder()
    q = DisclosureQueue(spy, rec, workers=1)
    q.start()
    q.submit(_Job(profile_id="p", child_text="CHILD-TEXT-MARKER"))
    await drain(q)
    assert "CHILD-TEXT-MARKER" in seen["prompt"]
    await q.stop()


@pytest.mark.asyncio
async def test_one_bad_job_does_not_kill_the_pool():
    rec = Recorder()
    calls = {"n": 0}

    async def flaky(_prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DisclosureClassifierUnavailable("first one dies")
        return '{"kind":"suicidal_ideation","why":"t"}'

    q = DisclosureQueue(flaky, rec, workers=1)
    q.start()
    q.submit(_Job(profile_id="p", child_text="a"))
    q.submit(_Job(profile_id="p", child_text="b"))
    await drain(q)
    assert q.stats.unavailable == 1
    assert [r["kind"] for r in rec.rows] == ["suicidal_ideation"]
    await q.stop()
