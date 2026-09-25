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


# ---------------------------------------------------------------------------
# ⭐ Gate the upgrade on WHAT HAPPENED, not on the kind.
#
# `_record_disclosure_incident` returns, post-#330, `bool(ok) and severity in
# (major, critical)` — an OBSERVED flag that already accounts for a write that
# failed without raising, and for a minor kind promoted to critical by a block.
#
# ⚠️ That promotion is the case the kind cannot see. On a BLOCKED crisis turn,
# `crisis_escalation_severity` writes a `bullying_victim` row at CRITICAL, so it
# alerts. Gating the worker on "the inline kind was minor" would then add a
# second alerting row and re-create the #330 double-alert.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_upgrade_when_the_inline_row_ALREADY_alerted():
    rec = Recorder()
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="they bully me and i dont want to be here",
            fallback_kind="bullying_victim",  # minor KIND...
            fallback_alerted=True,  # ...but promoted to critical by the block
            blocked=True,
            block_severity="critical",
        )
    )
    await drain(q)
    assert rec.rows == [], (
        "the worker added a second ALERTING row to a turn whose inline row had "
        "already alerted — that is the #330 double-alert, reintroduced"
    )
    await q.stop()


@pytest.mark.asyncio
async def test_a_blocked_crisis_turn_produces_exactly_ONE_alert_in_total():
    """⭐ The accounting a parent actually experiences, across all three writers.

    On a blocked crisis turn three things write or decline to write:
      1. the inline disclosure row  -> alerts (critical, disclosure framing)
      2. the safety block row       -> #330 suppresses ITS alert
      3. the semantic worker        -> must add nothing alerting

    Asserted as a TOTAL rather than per-component, because each component
    passing in isolation is what let the double-alert exist in the first place.
    """
    rec = Recorder()

    # 1. inline disclosure row: minor kind, promoted to critical by the block.
    inline_alerted = rec(None, "bullying_victim", "regex", "child text")
    # The Recorder keys alerting off the kind, so simulate the promotion the
    # real recorder performs.
    inline_alerted = True

    # 2. safety row, alert suppressed by #330 (recorded, does not alert).
    rec(None, "exploitation", "block", "child text")
    suppressed = True

    # 3. the worker.
    q = DisclosureQueue(gen_returning("suicidal_ideation"), rec, workers=1)
    q.start()
    q.submit(
        _Job(
            profile_id="p1",
            child_text="child text",
            fallback_kind="bullying_victim",
            fallback_alerted=inline_alerted,
            blocked=True,
            block_severity="critical",
        )
    )
    await drain(q)

    worker_rows = rec.rows[2:]
    total_alerts = (
        (1 if inline_alerted else 0)
        + (0 if suppressed else 1)
        + len([r for r in worker_rows if r["kind"] in ALERTS])
    )
    assert total_alerts == 1, (
        f"a blocked crisis turn produced {total_alerts} parent alerts; rows="
        f"{rec.kinds}. One is the contract — two trains a parent to ignore them, "
        f"zero leaves a child unheard."
    )
    assert worker_rows == [], f"the worker should add nothing here: {worker_rows}"
    await q.stop()


# ---------------------------------------------------------------------------
# The ROUTE's side of the contract, asserted on the source.
#
# ⚠️ Every real defect in this subsystem today lived in a CALL SITE, not in a
# helper: the crisis check fed the model's text on output blocks, the alert
# suppression keyed on a disclosure existing rather than on one alerting. So
# these assert what the route does, not what the queue can do.
# ---------------------------------------------------------------------------


def test_the_route_enqueues_before_the_tutor_call_and_never_awaits():
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    assert "_disclosure_queue().submit(" in src, "the route never enqueues"
    assert "await _disclosure_queue()" not in src, (
        "the route AWAITS the disclosure queue — that puts a ~3s CPU model call "
        "on the child's critical path, which is the whole thing this design "
        "exists to avoid"
    )
    # Enqueued before the block/serve decision, so the classify overlaps
    # generation rather than following it.
    assert src.index("_disclosure_queue().submit(") < src.index(
        "if not result.is_safe:"
    ), "the submit moved after the block path; it should overlap generation"


def test_the_route_submits_on_BLOCKED_turns_too():
    """#325: a blocked disclosure still needs typing. Gating the submit on
    `result.is_safe` would silently exclude the case where the child was both
    reaching out AND refused."""
    import inspect
    import re

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    block = src[
        src.index("if not _disclosure_alerted:") : src.index(
            "_disclosure_queue().submit("
        )
    ]
    assert not re.search(r"if\s+result\.is_safe", block), (
        "the submit is gated on the turn being safe, so blocked disclosures are "
        "never semantically classified"
    )


def test_the_route_skips_only_on_the_OBSERVED_alert():
    """Not on the kind. A minor kind promoted to critical by a block has already
    alerted, and an upgrade row would reach the parent twice (#330)."""
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    assert "if not _disclosure_alerted:" in src
    assert "fallback_alerted=_disclosure_alerted" in src, (
        "the job does not carry the observed alert flag, so the worker will "
        "re-derive it from the kind"
    )


def test_the_route_passes_only_the_childs_text():
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    job = src[
        src.index("_DisclosureJob(") : src.index("generate=_make_disclosure_generate")
    ]
    assert "child_text=text" in job
    for leak in ("assistant_text", "out_result", "reply"):
        assert leak not in job, (
            f"the disclosure job carries {leak!r} — the classifier must see the "
            f"CHILD's words only (#331)"
        )


def test_the_model_matches_the_gates_so_it_adds_no_load_slot():
    """⚠️ `OLLAMA_MAX_LOADED_MODELS=3` and the serving stack already needs
    exactly three: the 31b tutor (GPU), llama-guard3-cpu, and the input gate's
    model. The disclosure classifier must be the SAME model as the gate, or it
    becomes a fourth resident model and the eviction lands on the tutor —
    costing a cold load on every child turn after a classify.

    Checked on the running box: `GUIDANCE_GATE_MODEL=gemma4:e4b`, which is this
    default. Not luck, but not guaranteed either, hence the warning.
    """
    from safety.disclosure_semantic import (
        DISCLOSURE_MODEL,
        warn_if_model_adds_a_load_slot,
    )

    assert DISCLOSURE_MODEL == "gemma4:e4b", (
        "the default no longer matches GUIDANCE_GATE_MODEL on this deployment; "
        "under a limit of 3 loaded models that evicts the tutor"
    )

    import os

    prev = os.environ.get("GUIDANCE_GATE_MODEL")
    try:
        os.environ["GUIDANCE_GATE_MODEL"] = "some-other-model"
        assert warn_if_model_adds_a_load_slot() is not None, (
            "a mismatch between the disclosure model and the gate model is "
            "silent; it must warn, because the eviction is invisible until the "
            "tutor starts cold-loading"
        )
        os.environ["GUIDANCE_GATE_MODEL"] = DISCLOSURE_MODEL
        assert warn_if_model_adds_a_load_slot() is None
    finally:
        if prev is None:
            os.environ.pop("GUIDANCE_GATE_MODEL", None)
        else:
            os.environ["GUIDANCE_GATE_MODEL"] = prev


def test_one_worker_by_default_because_the_gate_shares_the_model():
    """Not a throughput choice. The gate runs the same model with a 6s timeout,
    and a gate timeout falls back to the regex — 25 points weaker on framed
    demands. Extra workers buy throughput and risk a SILENT quality regression
    on the critical path."""
    from safety.disclosure_queue import DEFAULT_WORKERS

    assert DEFAULT_WORKERS == 1, (
        "more than one concurrent classify can queue ahead of the input gate on "
        "the shared model and time it out into its weaker regex fallback"
    )
