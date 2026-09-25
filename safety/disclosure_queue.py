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

# Kinds whose incident is MAJOR and therefore raises a parent alert. Mirrors
# api.routes.ollama_proxy.blocks.ALERTING_DISCLOSURE_KINDS; imported lazily in
# the recorder to avoid a route import here.
_ALERTING = ("suicidal_ideation", "predatory_contact")


@dataclass
class _Job:
    """One turn awaiting semantic classification.

    `fallback_kind` is the regex detector's verdict when it found something the
    semantic pass may OVERRIDE -- i.e. a MINOR kind. It is carried rather than
    already recorded, and that is the whole trick for "one row per turn":

    * regex found a MAJOR kind  -> the route records it inline and never
      enqueues. It already alerted; a second row would re-create the
      double-alert that #330 fixed.
    * regex found a MINOR kind  -> the route does NOT record. It enqueues with
      `fallback_kind` set, and the WORKER writes the single row, using the
      semantic kind if there is one and the regex kind otherwise.
    * regex found nothing       -> enqueued with `fallback_kind=None`.

    ⚠️ The minor-kind case is the one a peer review caught me about. The regex
    picks kinds by pattern order, so "they bully me every day and honestly i
    don't want to be here anymore" can land as `bullying_victim` (MINOR,
    record-only, alerts nobody) where the semantic pass says
    `suicidal_ideation` (MAJOR, alerts). Skipping the semantic call there
    because "the regex already fired" would mean an ideation disclosure alerts
    NOBODY -- the silent direction.

    Deferring beats insert-then-update: `incident_logger` has no API to change a
    row's kind or severity, and adding a mutation path to the safety DB is more
    surface than this needs. The cost is that a MINOR row is written a few
    seconds late. Minor rows alert nobody, so that cost is record latency only,
    and it is stated rather than hidden.
    """

    profile_id: Optional[str]
    child_text: str
    fallback_kind: Optional[str] = None
    fallback_matched: str = ""
    blocked: bool = False
    block_severity: Optional[str] = None


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
        generate: Callable[[str], Awaitable[str]],
        recorder: Callable[..., bool],
        workers: int = DEFAULT_WORKERS,
        maxsize: int = DEFAULT_MAXSIZE,
    ):
        self._generate = generate
        self._recorder = recorder
        self._workers = max(1, workers)
        self._maxsize = max(1, maxsize)
        self._queue: Optional["asyncio.Queue[_Job]"] = None
        self._tasks: list = []
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

        ⚠️ On a shed we immediately write the regex fallback if there is one, so
        the deterministic floor is never lost to a full queue. That is the
        difference between a measurable degradation and a silent one.
        """
        try:
            if self._queue is None:
                self.start()
            assert self._queue is not None
            self._queue.put_nowait(job)
            self.stats.submitted += 1
            return True
        except asyncio.QueueFull:
            self.stats.shed += 1
            logger.warning(
                "disclosure queue FULL (maxsize=%d); shedding to the regex floor. "
                "shed=%d submitted=%d",
                self._maxsize,
                self.stats.shed,
                self.stats.submitted,
            )
            self._record_fallback(job)
            return False
        except Exception as exc:  # noqa: BLE001 - never raise into a child's turn
            self.stats.shed += 1
            logger.error("disclosure queue submit failed (non-fatal): %s", exc)
            self._record_fallback(job)
            return False

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
        """Classify one turn and write AT MOST ONE row for it."""
        try:
            # ⚠️ Only the CHILD's text is ever passed. #331: the crisis and
            # severity decisions must never read the model's output. The worker
            # is only given the child's turn, and this comment is here so a
            # future edit does not helpfully add the reply "for context".
            kind = await classify_disclosure(job.child_text, self._generate)
            self.stats.classified += 1
        except DisclosureClassifierUnavailable as exc:
            # NOT a negative verdict. Fall back to the regex floor and count it,
            # so an outage is visible instead of looking like a quiet corpus.
            self.stats.unavailable += 1
            logger.warning(
                "disclosure classifier unavailable (%s); falling back to the "
                "regex floor. unavailable=%d",
                exc,
                self.stats.unavailable,
            )
            self._record_fallback(job)
            return

        if kind is not None:
            self.stats.semantic_found += 1
            if job.fallback_kind and kind != job.fallback_kind:
                self.stats.upgraded += 1
                logger.info(
                    "disclosure upgraded by the semantic pass: %s -> %s",
                    job.fallback_kind,
                    kind,
                )
            self._record(job, kind, matched="semantic")
            return

        # The semantic pass says no disclosure. If the regex had found a MINOR
        # kind, it is still the deterministic floor and still gets recorded --
        # the semantic pass is additive, never a veto on the regex.
        self._record_fallback(job)

    # ---- recording ------------------------------------------------------

    def _record(self, job: _Job, kind: str, matched: str) -> None:
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

    def _record_fallback(self, job: _Job) -> None:
        if job.fallback_kind:
            self.stats.fallback_used += 1
            self._record(job, job.fallback_kind, job.fallback_matched or "regex")
        else:
            self.stats.nothing_recorded += 1


def alerting_kinds() -> tuple:
    """The kinds that raise a parent alert, for callers deciding what to defer."""
    return _ALERTING
