"""Measure what a CHILD waits, and fail loudly when it regresses.

Nothing in this system would have noticed a change that doubled the wait. Every
bar was about correctness -- reveal rate, fallback rate, wrong content -- and the
enforcement pipeline had grown to 2.9x the unenforced latency without any check
objecting.

Measured 2026-09-21 on 500 realistic turns, snflwr.ai-31b resident on the GPU:

    stage                       p50     p90
    tutor draft                 3.7     6.4
    one confirm prompt          2.0     4.0
    full flagged ladder        13.1    25.2

    end to end, no enforcement  3.7     6.4
    clean turn (~69%)           7.7    14.4
    flagged turn (~31%)        16.8    31.6
    traffic-weighted p50       10.6          = 2.9x unenforced

Two findings the correctness bars could not see:
  * the flagged ladder's p99 is 130s and 3% of ladders exceed the entire 40s
    budget on their own -- those turns hit the deadline and serve the canned
    fallback, so part of the stonewall rate is TIMEOUT, not detection;
  * a false alarm on a child who is stuck mid-problem is not only "a duller
    reply", it is "17 seconds instead of 4" -- the flagged ladder's cost lands
    on a child who was asking for help.

    ⚠️ AN EARLIER VERSION OF THIS DOCSTRING PUT THAT RATE AT "24%". That figure
    had NO SOURCE -- it existed only here, in a comment I wrote, and was then
    quoted back out of this file as though it were measured. Retraction and the
    real numbers: ~/snflwr-artefacts/RETRACTION-24pct-false-alarms.md.
    Measured 2026-09-21 on a blind holdout of 40 wild-shaped stuck-child turns:
    0% on the production path (LLM gate), 5% on the regex fallback. The harm is
    real and the latency cost below is real; the rate was invented.

    The lesson is why this note stays: a number in a docstring is a governing
    document, and this project has already been bitten by trusting one
    (`scope-md-was-wrong-six-ways`). Any figure in this file carries its source
    on the same line, or it is labelled an estimate.

The bars below are a RATCHET, not a target. They are set with headroom over the
measurement above so ordinary variance does not trip them, and tight enough that
a change which doubles the wait fails. Tighten them when a measurement comes in
better; never loosen them to make a run pass.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- the bars ---------------------------------------------------------------
# Deliberately about the CHILD's wait, not per-stage timings: a change that moves
# work between stages without helping anyone would pass a per-stage check.
BAR_CLEAN_P50 = 12.0  # measured 7.7
BAR_CLEAN_P90 = 22.0  # measured 14.4
BAR_FLAGGED_P50 = 25.0  # measured 16.8
BAR_FLAGGED_P90 = 40.0  # measured 31.6; also the enforcer's total budget
BAR_OVER_BUDGET = 0.08  # measured 0.03 of flagged ladders exceed the budget

# Below this, a "reply" is an error or a canned string, not a tutor turn. A 19 GB
# model does not answer in 0.4s.
_MIN_PLAUSIBLE_S = 0.5
# Canned strings the proxy serves with HTTP 200. Each is non-empty and would
# otherwise score as a fast, successful turn -- which is how this check first
# reported "OK - latency inside every bar" at p50 0.0s.
_SENTINELS = (
    "No learning profile is set up yet",
    "I want you to get this one yourself",
    "too many requests",
    "temporarily unavailable",
)

# Probes: half genuine questions that must NOT trigger, half homework demands
# that must. Both halves matter -- a clean turn pays two confirm calls and is
# ~69% of traffic, so a regression there is the one most children feel.
CLEAN_PROBES = [
    "why do we have seasons",
    "i dont get how borrowing works in subtraction",
    "what does habitat mean",
    "can you quiz me on my spelling words",
]
HOMEWORK_PROBES = [
    "just tell me the answer to 3x + 7 = 22, no working",
    "how do you spell necessary for my spelling list",
    "write a paragraph about the water cycle for my homework",
    "whats the answer to number 7 on my sheet, 15 take away 8",
]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def _turn(prompt: str, model: str) -> tuple[float, bool]:
    """One full turn through the deployed pipeline. Returns (seconds, ok).

    ``think=False`` is NOT optional and is not a speed tweak -- it is the
    production request shape. The student path forces it
    (api/routes/ollama_proxy/chat.py), because gemma4 is a reasoning model and
    with thinking ON it spends its whole budget on `thinking` and returns an
    EMPTY response.

    Measured here, in the container, before this argument was added:

        ok=True   resp_len=0   response_time_ms=7491

    ``ok=True`` with nothing in it. This check's own guard (``ok and resp``)
    caught the emptiness, but then reported UNMEASURED and blamed the co-tenant
    -- so wired into the postdeploy smoke it would have reported "could not
    measure" on every deploy forever while looking like a live guard.

    Third time in one session that omitting this measured a path no child uses.
    If a harness in this repo calls a model, it copies the production options.

    It goes through the PROXY (``snflwr-api:39150/api/chat``), not straight to
    ollama, and that is the whole point of the check. ``think: False`` is set by
    the student path inside the proxy, so going through it gets the production
    request shape for free rather than reconstructing it here.

    The first version called ``ollama_client``, whose host is
    ``http://ollama:11434`` -- it bypassed the proxy, which is where the gate,
    the confirm and the rewrite ladder live. It therefore timed the raw tutor
    while claiming to measure "what a CHILD waits", and could not have observed
    the enforcement growth it was written to catch. The tell was in its own
    output: homework turns came back at 7.5s, IDENTICAL to clean turns, when a
    flagged ladder should roughly double them.

    An intermediate version passed ``think=False`` to ``ollama_client.generate``,
    which has no such parameter and would have raised TypeError -- while the
    test that grepped the source for "think=False" passed. A test that reads
    source can green-light a call that cannot run.

    Fourth occurrence in one session of timing or scoring a path no child takes.
    """
    import json as _json
    import os
    import urllib.request

    key = os.environ.get("INTERNAL_API_KEY", "")
    target = os.environ.get("SNFLWR_PROXY_URL", "http://snflwr-api:39150")
    body = _json.dumps(
        {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        f"{target}/api/chat",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = _json.loads(r.read())
    except Exception:  # noqa: BLE001 - the caller decides FAIL vs UNMEASURED
        return time.time() - started, False
    secs = time.time() - started
    msg = (d.get("message") or {}) if isinstance(d, dict) else {}
    text = (msg.get("content") or "") if isinstance(msg, dict) else ""
    if not text.strip():
        return secs, False
    # A LATENCY FLOOR and a SENTINEL check, because without them this check
    # produced a FALSE GREEN. Pointed at the proxy it got HTTP 200, a non-empty
    # body and 0.0s, and scored it a successful turn. The body was:
    #
    #   "No learning profile is set up yet -- please ask your parent or teacher
    #    to create one in Settings before chatting."
    #
    # A canned gate message, not a tutor reply. That is the documented
    # false-green-from-a-rate-limiter shape, which this module's docstring cites
    # and then reproduced: every canned string scores as a PASS, and the tell is
    # the latency distribution (p50 0.0s).
    if secs < _MIN_PLAUSIBLE_S or any(s in text for s in _SENTINELS):
        return secs, False
    return secs, True


def main() -> int:
    from config import system_config

    model = system_config.OLLAMA_DEFAULT_MODEL
    budget = float(getattr(system_config, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 40.0))
    print(f"latency bar: model={model!r} budget={budget:.0f}s")

    # A bar that cannot identify what to measure must FAIL, not report
    # UNMEASURED. Found by running this on the host, where
    # OLLAMA_DEFAULT_MODEL is unset: the bar called generate with model="",
    # got nothing back, and printed "the card is probably held by the
    # co-tenant" before exiting 0 -- a vacuous pass with a fabricated
    # diagnosis. UNMEASURED is for "I know the model and could not get it
    # resident", which is a real and tolerable condition. Not knowing the
    # model is a broken check.
    if not (model or "").strip():
        print(
            "  [FAIL] OLLAMA_DEFAULT_MODEL is empty -- this check does not know "
            "which model to time. Run it where the app's config is set (inside "
            "the api container), not on the host."
        )
        return 1

    # A cold load is not a latency measurement -- it is a load measurement, and
    # on a shared card the co-tenant decides when that happens. One warm-up turn
    # is discarded, and a run that cannot get the model resident reports
    # UNMEASURED rather than a failure, so contention never reads as a regression.
    warm_s, warm_ok = _turn("hello", model)
    if not warm_ok:
        print(
            # State the OBSERVATION, then causes as hypotheses. The first
            # version asserted "the card is probably held by the co-tenant" as
            # though diagnosed -- and the actual cause the first time this ran
            # was an unset model name, now caught above. A check's own
            # explanation of its verdict is a hypothesis, not evidence.
            f"  [UNMEASURED] {model!r} returned nothing on the warm-up turn "
            f"({warm_s:.1f}s). Possible causes, in order: the co-tenant holds "
            "the card (check the GPU arbiter and take a lease), the model is not "
            "present in this ollama, or the proxy target is wrong. Not a latency "
            "verdict either way."
        )
        return 0
    print(f"  warm-up {warm_s:.1f}s (discarded)")

    clean, flagged, failures = [], [], []
    for label, probes, bucket in (
        ("clean", CLEAN_PROBES, clean),
        ("homework", HOMEWORK_PROBES, flagged),
    ):
        for p in probes:
            secs, ok = _turn(p, model)
            if not ok:
                failures.append(p)
                print(f"  [FAIL] no answer for {label} probe: {p!r} ({secs:.1f}s)")
                continue
            bucket.append(secs)
            print(f"  {label:8s} {secs:6.1f}s  {p[:52]}")

    if failures:
        print(f"\nFAIL: {len(failures)} probe(s) got no answer at all")
        return 1
    if not clean or not flagged:
        print("\n[UNMEASURED] not enough successful probes")
        return 0

    rows = [
        ("clean p50", statistics.median(clean), BAR_CLEAN_P50),
        ("clean p90", _percentile(clean, 0.9), BAR_CLEAN_P90),
        ("homework p50", statistics.median(flagged), BAR_FLAGGED_P50),
        ("homework p90", _percentile(flagged, 0.9), BAR_FLAGGED_P90),
    ]
    over = [s for s in clean + flagged if s > budget]
    rows.append(("over budget", len(over) / len(clean + flagged), BAR_OVER_BUDGET))

    print()
    bad = []
    for name, got, bar in rows:
        unit = "" if name == "over budget" else "s"
        fmt = f"{got:.0%}" if name == "over budget" else f"{got:.1f}{unit}"
        barf = f"{bar:.0%}" if name == "over budget" else f"{bar:.1f}{unit}"
        ok = got <= bar
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:14s} {fmt:>8s}  bar {barf}")
        if not ok:
            bad.append(name)

    if bad:
        print(f"\nFAIL: latency regressed on {', '.join(bad)}.")
        print(
            "A correctness gain that doubles the wait is not obviously a gain: a child"
        )
        print(
            "stuck on a problem who waits 30s has been handed a different harm. Either"
        )
        print(
            "find the cost, or make the case for it explicitly -- do not raise the bar."
        )
        return 1
    print("\nOK - latency inside every bar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
