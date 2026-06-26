#!/usr/bin/env python3
"""Phase 3 of the Claude-Code-as-judge bake-off path.

Reads the generated tasks (judge_dump.py) plus the judge's verdicts and combines
them into the same composite = mean(deterministic, judge_0_100) the live harness
uses, then aggregates overall / by-band / by-subject / per-probe and ranks each
model against the baseline. Verdicts file is {key: {correctness, pedagogy,
age_fit, tone[, rationale]}} keyed by "<model>|<case_id>".
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring import judge as judge_mod


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def main():
    ap = argparse.ArgumentParser(description="Aggregate Claude-Code judge verdicts")
    ap.add_argument("--tasks", default="judge_tasks.jsonl")
    ap.add_argument("--verdicts", default="judge_verdicts.json")
    ap.add_argument("--baseline", default="gemma4:e4b")
    ap.add_argument("--out", default="judge_rerun_report.md")
    ap.add_argument("--json-out", default="judge_rerun_report.json")
    args = ap.parse_args()

    tasks = [
        json.loads(line)
        for line in Path(args.tasks).read_text().splitlines()
        if line.strip()
    ]
    verdicts = json.loads(Path(args.verdicts).read_text())

    rows = []
    for t in tasks:
        v = verdicts.get(t["key"])
        if v is None:
            print(f"WARNING: no verdict for {t['key']}", file=sys.stderr)
            continue
        judge_0_100 = judge_mod.judge_score_0_100(v)
        composite = _mean([t["deterministic_score"], judge_0_100])
        rows.append(
            {**t, "judge": v, "judge_0_100": judge_0_100, "composite": composite}
        )

    models = sorted({r["model"] for r in rows})

    def per(group_key):
        table = defaultdict(dict)
        for r in rows:
            table[r[group_key]].setdefault(r["model"], []).append(r["composite"])
        out = {}
        for g, by_model in table.items():
            out[g] = {m: _mean(v) for m, v in by_model.items()}
        return out

    overall = {
        m: _mean([r["composite"] for r in rows if r["model"] == m]) for m in models
    }
    base = overall.get(args.baseline)
    ranking = sorted(
        (
            {
                "model": m,
                "overall": overall[m],
                "delta_vs_baseline": (
                    round(overall[m] - base, 1)
                    if base is not None and overall[m] is not None
                    else None
                ),
                "is_baseline": m == args.baseline,
            }
            for m in models
        ),
        key=lambda d: (d["overall"] is not None, d["overall"]),
        reverse=True,
    )

    by_band = per("band")
    by_subject = per("subject")

    # Per-probe spotlight: the contested homework-integrity cases.
    probe_rows = [r for r in rows if r.get("probe")]
    report = {
        "judge": "claude-code (claude-opus-4-8, in-context)",
        "baseline": args.baseline,
        "overall": overall,
        "ranking": ranking,
        "by_band": by_band,
        "by_subject": by_subject,
        "n_cases": len({r["case_id"] for r in rows}),
        "per_case": [
            {
                k: r[k]
                for k in (
                    "model",
                    "case_id",
                    "band",
                    "subject",
                    "probe",
                    "deterministic_score",
                    "judge_0_100",
                    "composite",
                )
            }
            for r in rows
        ],
    }
    Path(args.json_out).write_text(json.dumps(report, indent=2))

    # Markdown summary.
    lines = [
        f"# Claude-Code judge re-run (judge: claude-opus-4-8, {report['n_cases']} cases)\n"
    ]
    lines.append(f"Baseline: `{args.baseline}`\n")
    lines.append("## Ranking (overall composite)\n")
    lines.append("| Model | Overall | Δ vs baseline |")
    lines.append("|---|---|---|")
    for r in ranking:
        flag = " (baseline)" if r["is_baseline"] else ""
        lines.append(
            f"| `{r['model']}`{flag} | {r['overall']} | {r['delta_vs_baseline']} |"
        )
    for title, tbl in (("By band", by_band), ("By subject", by_subject)):
        lines.append(f"\n## {title}\n")
        cols = " | ".join(f"`{m}`" for m in models)
        lines.append(f"| {title.split()[1].capitalize()} | {cols} | Winner |")
        lines.append("|" + "---|" * (len(models) + 2))
        for g in sorted(tbl):
            vals = tbl[g]
            winner = max(
                (m for m in models if vals.get(m) is not None),
                key=lambda m: vals[m],
                default="-",
            )
            cells = " | ".join(str(vals.get(m)) for m in models)
            lines.append(f"| {g} | {cells} | `{winner}` |")
    if probe_rows:
        lines.append(
            "\n## Homework-integrity / off-topic probes (the contested area)\n"
        )
        lines.append("| Case | Probe | Model | Composite |")
        lines.append("|---|---|---|---|")
        for r in sorted(probe_rows, key=lambda r: (r["case_id"], r["model"])):
            lines.append(
                f"| {r['case_id']} | {r['probe']} | `{r['model']}` | {r['composite']} |"
            )
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"Wrote {args.out} and {args.json_out}", file=sys.stderr)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
