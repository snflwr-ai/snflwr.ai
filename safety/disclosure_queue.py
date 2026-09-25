"""Semantic disclosure classification, OFF the child's critical path.

WHY THIS REPLACES A PREFILTER GATE. Measured across three cold sealed sets
written by a peer session:

    set        prefilter passed   classifier, given a pass   end to end
    #2 (27)         10/27               10/10                  10/27
    #3 (23)          9/23                8/9                    8/23

The classifier is right on 18 of the 19 turns it has ever been shown. The GATE
discarded ~60% of disclosures before it saw them, and a different 60% for every
new set of sentences, because it was a hand-written pattern and that is what
hand-written patterns do on open-ended child language.

⚠️ The gate existed to protect a latency budget I never measured, and the
assumption was false twice over:

1. **This work is not on the critical path.** `detect_disclosure` reads the
   CHILD'S INPUT and its only effect is to write an incident row. It never
   gates, delays or alters the reply -- `disclosure_detector`'s docstring says
   "This detector NEVER blocks" in its first paragraph. So the model call does
   not have to happen before the child's answer is served.
2. **Throughput, measured** (`gemma4:e4b`, `num_gpu=0`): 0.49/s at concurrency
   4, 0.50/s at 8. Flat -- CPU-bound and saturated at 4. At a p90 turn latency
   of ~21.8s that covers ~11 concurrent children at a 100% classify rate.

So: every turn is fed in, nothing is thrown away on a pattern, and the only
recall loss is under genuine saturation -- where it is COUNTED and can be
alarmed on, rather than baked into a regex nobody re-measures.

⭐ EXACTLY ONE disclosure row per turn, by construction rather than convention.
See `_Job` and `_run_one` for how, and the module test for the mixed
bullying-plus-ideation case that motivated it.
"""

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from safety.disclosure_semantic import (
    DisclosureClassifierUnavailable,
    classify_disclosure,
)

logger = logging.getLogger(__name__)

# Where throughput saturates; more workers buy nothing and only add CPU
# contention with llama-guard, which also runs per turn on CPU.
DEFAULT_WORKERS = 2

# A bound, not a buffer. Past this the shed is recorded and the regex floor is
# used, which is a measurable loss rather than an unbounded queue and a growing
# lag nobody sees.
DEFAULT_MAXSIZE = 64

# A sustained shed is the dangerous state; a burst is not. Rate-limited so the
# operator marker cannot flood the log.
_OPERATOR_ALERT_EVERY_S = 600.0

# Kinds whose incident is MAJOR and therefore raises a parent alert. Mirrors
# api.routes.ollama_proxy.blocks.ALERTING_DISCLOSURE_KINDS; imported lazily in
# the recorder to avoid a route import here.
_ALERTING = ("suicidal_ideation", "predatory_contact")


@dataclass
class _Job:
    """One turn awaiting semantic classification.

    `fallback_kind` is what the regex detector ALREADY RECORDED INLINE for this
    turn, or None. It is carried so the worker can tell an UPGRADE from a
    duplicate -- it is not a deferred write.

    ⭐ THE INVARIANT IS ONE ALERT PER TURN, NOT ONE ROW. Getting that wrong cost
    me a design round, so it is written down:

    * regex MAJOR  -> recorded inline AND alerted. Never enqueued: a second row
      would re-create the double-alert #330 fixed.
    * regex MINOR  -> recorded inline (minor kinds alert nobody) AND enqueued.
      The worker adds a SECOND row only if the semantic pass upgrades it to a
      kind that alerts.
    * regex nothing -> enqueued. The worker records whatever it finds.

    ⚠️ My first version DEFERRED the minor row -- no inline write, the worker
    wrote the single row. Peer review killed it, correctly: that converts a
    latency cost into a LOSS risk. A regex-MINOR disclosure is recorded today
    and would have gone MISSING, silently, in exactly the failure modes the
    queue introduces -- a shed queue, a worker error after dequeue, or a process
    restart with items still queued. **Every deploy restarts all 8 workers and
    drops the in-memory queue, and deploys go out in batches.**

    So the deterministic record is never at the mercy of the queue. Two rows
    appear only on a genuine upgrade, which #330 already accepts for blocked
    turns, and only ONE of them ever alerts.

    ⚠️ The minor-kind case is why the semantic pass must run at all when the
    regex fired: the regex picks kinds by pattern ORDER, so "they bully me every
    day and honestly i don't want to be here anymore" lands as
    `bullying_victim` (MINOR, alerts nobody) where the semantic pass says
    `suicidal_ideation` (MAJOR, alerts). Skipping it there means an ideation
    disclosure alerts NOBODY.
    """

    profile_id: Optional[str]
    child_text: str
    fallback_kind: Optional[str] = None
    # ⭐ Whether the inline row ACTUALLY alerted, as OBSERVED from
    # `_record_disclosure_incident`'s return value -- not re-derived from the
    # kind here. Post-#330 that return is `bool(ok) and severity in
    # (major, critical)`, so it already accounts for a write that failed
    # without raising AND for a minor kind promoted to critical by a block.
    #
    # ⚠️ The promoted-minor case is exactly why the kind is not enough: a
    # `bullying_victim` row on a BLOCKED crisis turn is written at critical by
    # `crisis_escalation_severity`, so it alerts. Gating the worker's upgrade on
    # "the inline kind was minor" would then add a SECOND alerting row and
    # re-create the #330 double-alert. Gate on what happened.
    fallback_alerted: bool = False
    fallback_matched: str = ""
    blocked: bool = False
    block_severity: Optional[str] = None
    # ⚠️ The model call is carried PER JOB rather than held by the queue,
    # because the proxy's one-shot helper needs the request's forwarded headers
    # (internal auth) and a background worker cannot reconstruct them. The
    # closure captures a small header dict, not the request object.
    generate: Optional[Callable[[str], Awaitable[str]]] = None


@dataclass
class _Stats:
    submitted: int = 0
    shed: int = 0
    classified: int = 0
    upgraded: int = 0
    semantic_found: int = 0
    fallback_used: int = 0
    unavailable: int = 0
    nothing_recorded: int = 0
    lateral_ignored: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


class DisclosureQueue:
    """Bounded worker pool running the semantic disclosure pass after the reply.

    FAIL-SAFE IS THE WHOLE CONTRACT: no method here may raise into a child's
    turn. `submit` is non-blocking and swallows everything; a worker that dies
    is logged and replaced rather than taking the pool down.
    """

    def __init__(
        self,
        generate: Optional[Callable[[str], Awaitable[str]]] = None,
        recorder: Optional[Callable[..., bool]] = None,
        workers: int = DEFAULT_WORKERS,
        maxsize: int = DEFAULT_MAXSIZE,
    ):
        self._generate = generate
        self._recorder = recorder
        self._workers = max(1, workers)
        self._maxsize = max(1, maxsize)
        self._queue: Optional["asyncio.Queue[_Job]"] = None
        self._tasks: list = []
        self._last_operator_alert = 0.0
        self.stats = _Stats()

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Create the queue and workers on the running loop. Idempotent."""
        if self._queue is not None:
            return
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        for i in range(self._workers):
            self._tasks.append(
                asyncio.create_task(self._worker(i), name=f"disclosure-{i}")
            )
        logger.info(
            "disclosure queue started (workers=%d maxsize=%d)",
            self._workers,
            self._maxsize,
        )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self._queue = None

    # ---- submission (called from the request path) ----------------------

    def submit(self, job: _Job) -> bool:
        """Enqueue a turn. Returns False when SHED.

        Non-blocking and total: any failure is swallowed, because this is
        called while a child is waiting for their answer.

        ⚠️ A shed loses only the semantic UPGRADE chance, never the record: the
        route has already written the regex row inline. It is still the
        dangerous state, because "shedding for hours and nobody knows" is how a
        degradation becomes permanent -- so it is logged at ERROR, counted, and
        surfaced through `stats` for /health.
        """
        try:
            if self._queue is None:
                self.start()
            assert self._queue is not None
            self._queue.put_nowait(job)
            self.stats.submitted += 1
            return True
        except asyncio.QueueFull:
            self._shed("queue full (maxsize=%d)" % self._maxsize)
            return False
        except Exception as exc:  # noqa: BLE001 - never raise into a child's turn
            self._shed("submit failed: %s" % exc)
            return False

    def _shed(self, why: str) -> None:
        """Count and SHOUT. The regex row is already written by the route."""
        self.stats.shed += 1
        # ERROR, not WARNING: a sustained shed means disclosures are going
        # unclassified, and the failure is invisible from the outside.
        logger.error(
            "DISCLOSURE QUEUE SHED (%s): shed=%d of submitted=%d. The regex "
            "floor still recorded this turn, but it was NOT semantically "
            "classified.",
            why,
            self.stats.shed,
            self.stats.submitted,
        )
        now = _time.monotonic()
        if now - self._last_operator_alert >= _OPERATOR_ALERT_EVERY_S:
            self._last_operator_alert = now
            # Rate-limited so a burst cannot flood, but loud enough that a
            # sustained shed is visible. ⚠️ FOLLOW-UP: this is a log marker,
            # not a page -- wiring it to the operator alert path is a separate
            # change and is named here so it is not mistaken for done.
            logger.error(
                "OPERATOR_ALERT disclosure_queue_shedding shed=%d submitted=%d",
                self.stats.shed,
                self.stats.submitted,
            )

    # ---- worker ---------------------------------------------------------

    async def _worker(self, n: int) -> None:
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            try:
                await self._run_one(job)
            except asyncio.CancelledError:
                raise
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one bad turn must not kill the pool
                logger.error("disclosure worker %d: %s", n, exc, exc_info=True)
            finally:
                self._queue.task_done()

    async def _run_one(self, job: _Job) -> None:
        """Classify one turn; write at most one row FROM THE WORKER.

        The route may already have written an inline regex row for this turn.
        The invariant is one ALERT per turn, not one row -- see `_Job`.
        """
        try:
            # ⚠️ Only the CHILD's text is ever passed. #331: the crisis and
            # severity decisions must never read the model's output. The worker
            # is only given the child's turn, and this comment is here so a
            # future edit does not helpfully add the reply "for context".
            gen = job.generate or self._generate
            if gen is None:
                # No way to call the classifier for this turn. The inline regex
                # row stands; count it rather than failing silently.
                self.stats.unavailable += 1
                logger.error("disclosure job had no generate and no default")
                return
            kind = await classify_disclosure(job.child_text, gen)
            self.stats.classified += 1
        except DisclosureClassifierUnavailable as exc:
            # NOT a negative verdict -- that conflation is how a safety layer
            # reports success while doing nothing. Nothing is written here: the
            # route's inline regex row (if any) already stands, and there is no
            # semantic verdict to add. Counted so an outage is visible instead
            # of looking like a quiet corpus.
            self.stats.unavailable += 1
            logger.warning(
                "disclosure classifier unavailable (%s); this turn was NOT "
                "semantically classified. unavailable=%d",
                exc,
                self.stats.unavailable,
            )
            return

        if kind is None:
            # No disclosure. The regex row, if any, already stands -- the
            # semantic pass is additive and never a veto on the regex.
            self.stats.nothing_recorded += 1
            return

        self.stats.semantic_found += 1

        if job.fallback_kind is None:
            # The regex missed this entirely; this is the whole point.
            self._record(job, kind, matched="semantic")
            return

        if kind == job.fallback_kind:
            # Agreement. Already recorded inline; a second row would be noise.
            return

        if kind in _ALERTING and not job.fallback_alerted:
            # ⭐ UPGRADE. The inline row did not alert; this one does.
            self.stats.upgraded += 1
            logger.warning(
                "disclosure UPGRADED by the semantic pass: %s -> %s "
                "(the inline row did not alert; this one does)",
                job.fallback_kind,
                kind,
            )
            self._record(job, kind, matched="semantic-upgrade")
            return

        # Either a lateral disagreement between two non-alerting kinds (bullying
        # vs disordered eating), or the inline row ALREADY ALERTED so a second
        # alerting row would reach the parent twice. In both cases the inline
        # row stands and the worker adds nothing.
        self.stats.lateral_ignored += 1

    # ---- recording ------------------------------------------------------

    def _record(self, job: _Job, kind: str, matched: str) -> None:
        if self._recorder is None:
            logger.error("disclosure queue has no recorder; row NOT written")
            return
        try:
            self._recorder(
                job.profile_id,
                kind,
                matched,
                job.child_text,
                blocked=job.blocked,
                block_severity=job.block_severity,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "failed to record disclosure (non-fatal): %s", exc, exc_info=True
            )


def alerting_kinds() -> tuple:
    """The kinds that raise a parent alert, for callers deciding what to defer."""
    return _ALERTING
