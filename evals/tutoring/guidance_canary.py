#!/usr/bin/env python3
"""
Guidance-enforcement canary: measures the effect of the guidance-enforcement
post-processor ON vs OFF for homework_integrity probes.

Run this on the GPU box before enabling GUIDANCE_ENFORCEMENT_ENABLED in
production.  It requires a live Ollama model and is NOT run in CI.

Usage:
  python -m evals.tutoring.guidance_canary \\
      --base-url http://localhost:11434 \\
      --model snflwr.ai \\
      --out guidance_canary_report.md \\
      --json-out guidance_canary_pairs.json

Metrics emitted
---------------
  Per-probe table     : off_revealed, on_revealed, action, latency_ms
  Action distribution : how often each EnforceMeta.action occurred
                        (quantifies the confirm-call rate — a known watch-item)
  Deterministic rate  : reveal rate OFF vs ON (floor, see limiter note below)
  Added latency       : p50 / p95 on the turns where the enforcer re-prompted

LIMITER NOTE: scorers.reveals_answer is a digit/boundary token matcher and
UNDER-COUNTS word-form reveals (e.g. "fifty-six" is not flagged when answer="56").
The deterministic reveal rates are a conservative floor.  The JUDGED pedagogy
delta on the saved pairs (--json-out) is the real gate.

Success gate (from the plan)
----------------------------
  pedagogy ↑  (target ≥ 1.9/2 judged score)
  reveal rate ↓
  correctness / age_fit / tone / readability / length flat (± noise)
  non-homework probes byte-identical
  p95 added latency within budget (< GUIDANCE_ENFORCER_TIMEOUT_S)

The judged pedagogy delta is measured separately on the saved ON/OFF pairs;
this script provides the deterministic guardrails + latency data.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Support both `python -m evals.tutoring.guidance_canary` and direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring.run_eval import BAND_AGES, generate_via_ollama, load_dataset
from evals.tutoring import scorers

# ---------------------------------------------------------------------------
# Ollama helpers (sync wrappers; the enforcer callables are async via
# asyncio.to_thread so we don't block the event loop during I/O).
# ---------------------------------------------------------------------------


def _raw_chat(messages: list, base_url: str, model: str, timeout: int = 120) -> str:
    """POST to /api/chat with an arbitrary messages list; return assistant text."""
    import urllib.error
    import urllib.request

    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("message", {}).get("content", "")


# ---------------------------------------------------------------------------
# Async enforcer runner (one probe at a time)
# ---------------------------------------------------------------------------


async def _run_enforcer_async(
    question: str,
    ages: str,
    off_response: str,
    base_url: str,
    model: str,
):
    """
    Call enforce_guidance with ON forced and real Ollama closures.
    Returns (on_response, meta, total_latency_ms).

    Imports are deferred here so the module-level import path is clean even
    before config is mutated.
    """
    from core.pedagogy.guidance_enforcer import enforce_guidance

    age_prefix = f"[Student age range: {ages}]\n"

    async def regenerate(nudge: str) -> str:
        """Re-issue: original question -> off_response -> nudge (multi-turn)."""
        msgs = [
            {"role": "user", "content": age_prefix + question},
            {"role": "assistant", "content": off_response},
            {"role": "user", "content": nudge},
        ]
        return await asyncio.to_thread(_raw_chat, msgs, base_url, model)

    async def confirm_gen(prompt: str) -> str:
        """One-shot confirm call (raw prompt, no age prefix needed)."""
        msgs = [{"role": "user", "content": prompt}]
        return await asyncio.to_thread(_raw_chat, msgs, base_url, model)

    t0 = time.monotonic()
    on_response, meta = await enforce_guidance(
        question, off_response, regenerate, confirm_generate=confirm_gen
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    return on_response, meta, latency_ms


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def _percentile(data: list, p: float):
    """Return the p-th percentile of data (linear interpolation). None if empty."""
    if not data:
        return None
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = (n - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    if lo == hi:
        return sorted_data[lo]
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)


def _fmt_pct(v):
    return f"{v:.0%}" if v is not None else "n/a"


def _fmt_ms(v):
    return f"{v:.0f} ms" if v is not None else "n/a"


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------


def _build_markdown(rows: list, action_dist: dict, json_out: str) -> str:
    n = len(rows)
    off_reveals = sum(1 for r in rows if r["off_revealed"])
    on_reveals = sum(1 for r in rows if r["on_revealed"])
    off_rate = off_reveals / n if n else 0.0
    on_rate = on_reveals / n if n else 0.0
    delta = off_rate - on_rate

    reprompt_latencies = [
        r["latency_ms"] for r in rows if r["action"].startswith("reprompt_")
    ]
    p50 = _percentile(reprompt_latencies, 50)
    p95 = _percentile(reprompt_latencies, 95)

    lines = [
        "# Guidance-Enforcement Canary Report",
        "",
        "> **Limiter note:** `scorers.reveals_answer` is a digit/token-boundary matcher",
        "> that UNDER-COUNTS word-form reveals (e.g. 'fifty-six' is NOT flagged when",
        "> `answer='56'`).  The deterministic reveal rates below are a conservative floor.",
        "> The JUDGED pedagogy delta (measured separately on the saved ON/OFF pairs) is",
        "> the real gate and must pass before enabling `GUIDANCE_ENFORCEMENT_ENABLED=true`.",
        "",
        "## Per-probe results",
        "",
        "| ID | Band | OFF revealed | ON revealed | Action | Latency (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        off_sym = "**YES**" if r["off_revealed"] else "no"
        on_sym = "**YES**" if r["on_revealed"] else "no"
        lines.append(
            f"| {r['id']} | {r['band']} | {off_sym} | {on_sym}"
            f" | `{r['action']}` | {r['latency_ms']} |"
        )

    lines += [
        "",
        "## Deterministic reveal rates",
        "",
        "*(Under-counts word-form reveals — see limiter note above.)*",
        "",
        "| Arm | Reveals | Rate |",
        "|---|---|---|",
        f"| OFF (baseline) | {off_reveals} / {n} | {_fmt_pct(off_rate)} |",
        f"| ON  (enforcer) | {on_reveals} / {n} | {_fmt_pct(on_rate)} |",
        f"| Delta (OFF − ON) | — | {_fmt_pct(delta)} |",
        "",
        "## Action distribution",
        "",
        "*(Shows how often the enforcer reached each decision point.  The fraction",
        "that hit `pass_confirm` or `reprompt_*` quantifies the confirm-call rate —",
        "a known watch-item for latency budget.)*",
        "",
        "| Action | Count | Share |",
        "|---|---|---|",
    ]
    for action, count in sorted(action_dist.items(), key=lambda kv: -kv[1]):
        share = count / n if n else 0.0
        lines.append(f"| `{action}` | {count} | {_fmt_pct(share)} |")

    lines += [
        "",
        "## Added latency on turns where the enforcer re-prompted",
        "",
        f"*(N = {len(reprompt_latencies)} turn(s) with `reprompt_*` action;",
        "timing covers the full `enforce_guidance` call including confirm + re-prompt.)*",
        "",
        "| Percentile | Latency |",
        "|---|---|",
        f"| p50 | {_fmt_ms(p50)} |",
        f"| p95 | {_fmt_ms(p95)} |",
        "",
        "## Verdict",
        "",
        "**Success gate (from plan):**",
        "- homework pedagogy ↑ (target ≥ 1.9/2 judged score)",
        "- reveal rate ↓",
        "- correctness / age_fit / tone / readability / length flat (± noise)",
        "- non-homework probes byte-identical",
        "- p95 added latency within `GUIDANCE_ENFORCER_TIMEOUT_S` budget",
        "",
        "> **IMPORTANT:** The deterministic `reveals_answer` flag above is a floor,",
        "> not the true reveal gate.  The **JUDGED pedagogy delta** is the real gate",
        "> and is measured separately on the saved ON/OFF response pairs.",
        f"> Feed `{json_out}` to the eval judge to compare pedagogy scores ON vs OFF",
        "> before enabling `GUIDANCE_ENFORCEMENT_ENABLED=true` in production.",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m evals.tutoring.guidance_canary",
        description=(
            "Measure guidance-enforcement ON vs OFF for homework_integrity probes. "
            "Requires a live Ollama model; not run in CI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Metrics emitted\n"
            "  Per-probe table     : off_revealed, on_revealed, action, latency_ms\n"
            "  Action distribution : how often each EnforceMeta.action occurred\n"
            "  Reveal rate         : deterministic OFF vs ON (conservative floor)\n"
            "  Added latency       : p50 / p95 on turns where enforcer re-prompted\n"
            "\n"
            "The judged pedagogy delta (measured separately on --json-out pairs) is\n"
            "the real gate.  This script provides the deterministic + latency data.\n"
        ),
    )
    ap.add_argument(
        "--model",
        default="snflwr.ai",
        help="Ollama model tag to generate with (default: %(default)s)",
    )
    ap.add_argument(
        "--base-url",
        required=True,
        help="Ollama base URL, e.g. http://localhost:11434",
    )
    ap.add_argument(
        "--out",
        default="guidance_canary_report.md",
        help="Path for the markdown report (default: %(default)s)",
    )
    ap.add_argument(
        "--json-out",
        default="guidance_canary_pairs.json",
        help=(
            "Path for ON/OFF response pairs JSON — feed this to the eval judge "
            "to measure the pedagogy delta (default: %(default)s)"
        ),
    )
    args = ap.parse_args()

    # Force the enforcer ON for this measurement run.  This is a harness: we're
    # measuring it, not trusting the env flag.
    from config import system_config

    system_config.GUIDANCE_ENFORCEMENT_ENABLED = True

    # Load only homework_integrity probes.
    all_cases = load_dataset()
    probes = [c for c in all_cases if c.get("probe") == "homework_integrity"]
    print(f"Loaded {len(probes)} homework_integrity probes from dataset.")
    if not probes:
        print("ERROR: no homework_integrity probes found in dataset.", file=sys.stderr)
        sys.exit(1)

    rows: list[dict] = []
    pairs: list[dict] = []

    for i, probe in enumerate(probes, 1):
        probe_id = probe["id"]
        band = probe["band"]
        question = probe["question"]
        answer = probe.get("answer", "")
        ages = BAND_AGES[band]

        print(f"[{i}/{len(probes)}] {probe_id}")

        # --- OFF arm: baseline generation ---
        print(f"  generating OFF response...")
        off_response = generate_via_ollama(question, ages, args.base_url, args.model)
        off_revealed = scorers.reveals_answer(off_response, answer)

        # --- ON arm: enforcer ---
        print(f"  running enforcer (ON)...")
        on_response, meta, latency_ms = asyncio.run(
            _run_enforcer_async(question, ages, off_response, args.base_url, args.model)
        )
        on_revealed = scorers.reveals_answer(on_response, answer)

        print(
            f"  action={meta.action}  off_revealed={off_revealed}"
            f"  on_revealed={on_revealed}  latency={latency_ms}ms"
        )

        rows.append(
            {
                "id": probe_id,
                "band": band,
                "question": question,
                "answer": answer,
                "off_revealed": off_revealed,
                "on_revealed": on_revealed,
                "action": meta.action,
                "latency_ms": latency_ms,
            }
        )
        # Save pairs for judged follow-up (no model output PII filtering needed
        # here — this is operator tooling, not stored student data).
        pairs.append(
            {
                "id": probe_id,
                "question": question,
                "off_response": off_response,
                "on_response": on_response,
                "action": meta.action,
            }
        )

    # --- Aggregate stats ---
    n = len(rows)
    action_dist: dict[str, int] = {}
    for r in rows:
        action_dist[r["action"]] = action_dist.get(r["action"], 0) + 1

    off_reveals = sum(1 for r in rows if r["off_revealed"])
    on_reveals = sum(1 for r in rows if r["on_revealed"])
    off_rate = off_reveals / n if n else 0.0
    on_rate = on_reveals / n if n else 0.0

    reprompt_latencies = [
        r["latency_ms"] for r in rows if r["action"].startswith("reprompt_")
    ]
    p50 = _percentile(reprompt_latencies, 50)
    p95 = _percentile(reprompt_latencies, 95)

    # --- Persist outputs ---
    Path(args.json_out).write_text(json.dumps(pairs, indent=2))

    report_md = _build_markdown(rows, action_dist, args.json_out)
    Path(args.out).write_text(report_md)

    # --- Console verdict block ---
    confirm_turns = sum(
        v
        for k, v in action_dist.items()
        if k not in ("disabled", "not_homework", "pass_gate")
    )
    print()
    print("=" * 62)
    print("  GUIDANCE CANARY — RESULTS")
    print("=" * 62)
    print(f"  Probes scored        : {n}")
    print(
        f"  OFF reveal rate      : {_fmt_pct(off_rate)}  (deterministic — under-counts)"
    )
    print(
        f"  ON  reveal rate      : {_fmt_pct(on_rate)}  (deterministic — under-counts)"
    )
    print(f"  Reveal-rate delta    : {_fmt_pct(off_rate - on_rate)}  (OFF minus ON)")
    print(f"  Confirm-call turns   : {confirm_turns} / {n}")
    print(f"  Action distribution  : {action_dist}")
    if p50 is not None:
        print(
            f"  Added latency        : p50={_fmt_ms(p50)}  p95={_fmt_ms(p95)}"
            f"  (N={len(reprompt_latencies)} re-prompt turns)"
        )
    else:
        print("  Added latency        : n/a (no re-prompt turns)")
    print()
    print("  SUCCESS GATE (from plan):")
    print("    pedagogy ↑  (target ≥ 1.9/2)  — measure on saved pairs via judge")
    print("    reveal rate ↓   correctness / age_fit / tone flat")
    print("    p95 latency within GUIDANCE_ENFORCER_TIMEOUT_S budget")
    print()
    print("  NOTE: deterministic reveal rate UNDER-COUNTS word-form reveals.")
    print("  The JUDGED pedagogy delta (not this flag) is the real gate.")
    print("  Run the eval judge on the saved ON/OFF pairs before enabling.")
    print(f"\n  Reports: {args.out}  |  {args.json_out}")
    print("=" * 62)


if __name__ == "__main__":
    main()
