#!/usr/bin/env python3
"""Phase 1 of the Claude-Code-as-judge bake-off path.

The strongest, most neutral judge available here is the Claude model running this
session — but it can't be called as a Python `generate()` callable from inside the
harness. So we split the pipeline: this script does the GPU-bound work
(assemble production-shaped models -> generate every response -> compute the
deterministic scores -> build each judge prompt) and writes the ready-to-judge
tasks to a JSONL file. The judge (Claude Code) then reads that file, scores each
task on the rubric, and writes verdicts; `judge_aggregate.py` combines them into
the report. Generation happens ONCE, so the responses the judge grades are
exactly the ones that get aggregated.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring import backbone_bakeoff as bk
from evals.tutoring import bakeoff, run_eval
from evals.tutoring import judge as judge_mod


def main():
    ap = argparse.ArgumentParser(description="Generate ready-to-judge bake-off tasks")
    ap.add_argument("--models", default="gemma4:e4b,gemma4:31b")
    ap.add_argument("--base-url", default="http://172.22.0.4:11434")
    ap.add_argument("--container", default="snflwr-ollama")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--out", default="judge_tasks.jsonl")
    args = ap.parse_args()

    bases = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = run_eval.load_dataset()

    # Assemble all candidates up front so a build failure aborts before any
    # long generation runs.
    assembled = {}
    for base in bases:
        print(f"Assembling {base} -> {bk.assembled_name(base)} ...", file=sys.stderr)
        assembled[base] = bk.build_assembled(base, args.container)

    rows = []
    for base in bases:
        a = assembled[base]
        print(f"Generating responses: {base} (as {a}) ...", file=sys.stderr)
        for case in cases:
            content, _metrics = bk.generate_with_metrics(
                case["question"],
                run_eval.BAND_AGES[case["band"]],
                args.base_url,
                a,
                args.timeout,
            )
            det = run_eval.score_case(case, content)  # deterministic-only scoring
            rows.append(
                {
                    "key": f"{base}|{case['id']}",
                    "model": base,
                    "case_id": case["id"],
                    "band": case["band"],
                    "subject": case["subject"],
                    "probe": case.get("probe"),
                    "deterministic_score": det.get("deterministic_score"),
                    "response": content,
                    "judge_prompt": judge_mod.build_judge_prompt(case, content),
                }
            )
        # Unload before the next (possibly large) model so a 23 GB card isn't
        # asked to hold two backbones at once.
        bakeoff.unload_model(args.base_url, a)

    Path(args.out).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"Wrote {len(rows)} judge tasks to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
