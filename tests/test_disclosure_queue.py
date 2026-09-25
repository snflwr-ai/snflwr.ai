"""One ALERT per turn — and the regex record never at the mercy of the queue.

⚠️ This replaces a prefilter GATE that discarded ~60% of disclosures before the
classifier saw them (10/27 and 9/23 on two cold sealed sets, while the
classifier was right on 18 of the 19 turns it was shown). The gate protected a
latency budget that was never measured, and the work is not on the child's
critical path at all — it only writes an incident row.

The hard part is not the queue. It is that the regex and the semantic pass can
BOTH have an opinion about one turn, and the wrong combination rule produces
either two parent alerts, or none, or a lost record.
"""

import asyncio

import pytest

from safety.disclosure_queue import DisclosureQueue, _Job
from safety.disclosure_semantic import DisclosureClassifierUnavailable

ALERTS = ("suicidal_ideation", "predatory_contact")


def gen_returning(kind):
    async def _g(_prompt):
        return '{"kind": "%s", "why": "t"}' % (kind or "none")

    return _g


def gen_raising(exc):
    async def _g(_prompt):
        raise exc

    return _g


class Recorder:
    """Stands in for _record_disclosure_incident. Returns whether it alerted."""

    def __init__(self):
        self.rows = []

    def __call__(
        self, profile_id, kind, matched, child_text, blocked=False, block_severity=None
    ):
        self.rows.append({"kind": kind, "matched": matched, "child_text": child_text})
        return kind in ALERTS

    @property
    def kinds(self):
        return [r["kind"] for r in self.rows]

    @property
    def alerting(self):
        return [k for k in self.kinds if k in ALERTS]


async def drain(q):
    assert q._queue is not None
    await q._queue.join()


@pytest.mark.asyncio
async def test_a_turn_the_regex_missed_is_recorded_by_the_classifier():
    """The whole point: the regex passed 9/23 on a cold set; the classifier
    catches what it missed."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(profile_id="p1", child_text="i just want to go to sleep and never wake up")
    )
    await drain(q)
    assert rec.kinds == ["suicidal_ideation"]
    assert rec.alerting == ["suicidal_ideation"]
    await q.stop()


@pytest.mark.asyncio
async def test_a_minor_regex_hit_is_UPGRADED_by_a_second_ALERTING_row():
    """⭐ The case peer review caught twice — once for the hole, once for my fix.

    The regex picks kinds by pattern ORDER, so "they bully me every day and
    honestly i don't want to be here anymore" lands as `bullying_victim` —
    MINOR, alerts NOBODY — where the semantic pass says `suicidal_ideation`,
    which alerts. Skipping the semantic call because "the regex already fired"
    leaves an ideation disclosure alerting nobody.

    ⚠️ My first fix DEFERRED the minor row so there would be exactly one row.
    That was wrong, and the reason is worth keeping: it put a record that
    EXISTS TODAY at the mercy of a shed queue, a worker error, or a process
    restart — and every deploy restarts all 8 workers and drops the in-memory
    queue, with deploys going out in batches.

    The invariant is one ALERT per turn, not one row. `fallback_kind` here is
    what the route already wrote inline.
    """
    rec = Recorder()
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they bully me every day and honestly i dont want to be here anymore",
            fallback_kind="bullying_victim",
        )
    )
    await drain(q)
    assert rec.kinds == ["suicidal_ideation"], "the worker must add the upgrade row"
    assert len(rec.alerting) == 1, "exactly one alerting row for the turn"
    assert q.stats.upgraded == 1
    await q.stop()


@pytest.mark.asyncio
async def test_agreement_adds_no_second_row():
    rec = Recorder()
    q = DisclosureQueue(gen_returning("bullying_victim"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they keep making fun of me",
            fallback_kind="bullying_victim",
        )
    )
    await drain(q)
    assert rec.rows == [], f"duplicated an already-recorded row: {rec.rows}"
    await q.stop()


@pytest.mark.asyncio
async def test_a_semantic_NO_leaves_the_inline_row_alone():
    """The semantic pass is additive, never a veto on the deterministic layer."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning(None), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they keep making fun of me",
            fallback_kind="bullying_victim",
        )
    )
    await drain(q)
    assert rec.rows == [], "the worker must not re-write or retract the inline row"
    await q.stop()


@pytest.mark.asyncio
async def test_a_lateral_minor_disagreement_adds_no_row():
    """bullying vs disordered eating: neither alerts, so a second row buys a
    reviewer one more row and no alert. The inline row stands."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning("disordered_eating"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="i skip lunch because of them",
            fallback_kind="bullying_victim",
        )
    )
    await drain(q)
    assert rec.rows == []
    assert q.stats.lateral_ignored == 1
    await q.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc", [RuntimeError("connection refused"), OSError("evicted")]
)
async def test_a_classifier_outage_is_COUNTED_and_writes_nothing(exc):
    """⚠️ An outage must not look like a quiet corpus. Nothing is written — the
    inline regex row already stands and there is no semantic verdict to add —
    and the counter moves so it can be alarmed on."""
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
    assert rec.rows == []
    assert q.stats.unavailable == 1
    await q.stop()


@pytest.mark.asyncio
async def test_a_full_queue_sheds_LOUDLY_and_costs_only_the_upgrade():
    """⭐ Why the deferral design was wrong, as a test.

    A shed costs the semantic UPGRADE chance and NOTHING ELSE, because the route
    already wrote the regex row inline. Under the deferral design these turns
    would have lost their record entirely.
    """
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
    assert rec.rows == [], "the worker wrote rows for shed turns"
    await q.stop()


def test_submit_never_raises_into_a_childs_turn():
    """No running loop, so start() fails. It must still not raise — a child is
    waiting for their answer on the other side of this call."""
    rec = Recorder()
    q = DisclosureQueue(gen_returning(None), rec, workers=1)
    assert (
        q.submit(_Job(profile_id="p", child_text="hi", fallback_kind="bullying_victim"))
        is False
    )
    assert q.stats.shed == 1
    assert rec.rows == []


@pytest.mark.asyncio
async def test_only_the_childs_text_reaches_the_classifier():
    """#331's rule, restated where it now lives. The crisis and severity
    decisions must never read the model's output, and this pins it so a future
    edit cannot helpfully pass the reply 'for context'."""
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
    assert rec.kinds == ["suicidal_ideation"]
    await q.stop()
