#!/usr/bin/env python3
"""
Tutoring-quality eval runner for snflwr.ai.

Answers the question the safety tests don't: *is the tutoring any good?* It
runs each dataset case through the model (or scores pre-recorded responses),
applies the deterministic scorers + an optional LLM judge, and emits a JSON +
markdown report with per-band / per-subject breakdowns and a pass threshold.

Usage:
  # Score against a live Ollama model:
  python -m evals.tutoring.run_eval --model snflwr.ai --base-url http://localhost:11434

  # Score pre-recorded responses (no model needed; CI/offline):
  python -m evals.tutoring.run_eval --responses responses.jsonl

  # Add the LLM judge (local Ollama judge model by default):
  python -m evals.tutoring.run_eval --responses responses.jsonl --judge llama-guard3:8b

`responses.jsonl` is one JSON object per line: {"id": "<case-id>", "response": "<text>"}.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Allow running both as a module and as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring import scorers
from evals.tutoring import judge as judge_mod

DATASET = Path(__file__).with_name("dataset.yaml")

# Deterministic length/readability scoring is meaningful for on-topic tutoring
# turns; for off-topic/meta cases the "right" answer is a short redirect, so we
# score those via the judge only.
_DETERMINISTIC_SUBJECTS = {"math", "science", "reading", "writing"}

PASS_THRESHOLD = 70.0  # overall composite >= this => PASS


def load_dataset(path: Path = DATASET) -> list:
    with open(path) as f:
        return yaml.safe_load(f)["cases"]


def _mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def score_case(case: dict, response: str, judge_scores: dict | None = None) -> dict:
    """Pure scoring of one (case, response). Returns a result row."""
    band = case["band"]
    probe = case.get("probe")
    words = scorers.count_words(response)
    fk = scorers.flesch_kincaid_grade(response)
    guiding = scorers.asks_guiding_question(response)

    row = {
        "id": case["id"],
        "band": band,
        "subject": case["subject"],
        "probe": probe,
        "words": words,
        "fk_grade": fk,
        "guiding_question": guiding,
    }

    deterministic = []
    if case["subject"] in _DETERMINISTIC_SUBJECTS and probe != "off_topic":
        length = round(scorers.length_score(response, band) * 100, 1)
        read = round(scorers.readability_score(response, band) * 100, 1)
        row["length_pct"] = length
        row["readability_pct"] = read
        deterministic += [length, read]

    if probe == "homework_integrity":
        revealed = scorers.reveals_answer(response, case.get("answer", ""))
        row["revealed_answer"] = revealed
        # Integrity: full marks for guiding without handing over the answer.
        integrity = 0.0 if revealed else 100.0
        row["integrity_pct"] = integrity
        deterministic.append(integrity)

    row["deterministic_score"] = _mean(deterministic)

    judge_val = None
    if judge_scores is not None:
        row["judge"] = {k: judge_scores.get(k) for k in
                        (*judge_mod.RUBRIC_DIMENSIONS, "rationale", "judge_0_100")}
        judge_val = judge_scores.get("judge_0_100")

    row["composite"] = _mean([row["deterministic_score"], judge_val])
    return row


def generate_via_ollama(question: str, ages: str, base_url: str, model: str, timeout: int = 120) -> str:
    """Query an Ollama chat model. The age hint mirrors what the production
    proxy injects so age_fit is testable."""
    import urllib.request

    user = f"[Student age range: {ages}]\n{question}"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": user}],
        "stream": False,
        "think": False,
    }).encode()
    req = urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url.rstrip('/')}/api/chat",
            data=payload, headers={"Content-Type": "application/json"},
        ),
        timeout=timeout,
    )
    with req as resp:
        data = json.loads(resp.read())
    return data.get("message", {}).get("content", "")


def build_markdown_report(rows: list, summary: dict) -> str:
    lines = ["# Tutoring-Quality Eval Report", ""]
    verdict = "✅ PASS" if summary["overall"] is not None and summary["overall"] >= PASS_THRESHOLD else "⚠️ BELOW THRESHOLD"
    lines.append(f"**Overall composite: {summary['overall']} / 100** — {verdict} (threshold {PASS_THRESHOLD})")
    lines.append(f"Cases scored: {summary['n']}")
    lines.append("")
    lines.append("## By age band")
    lines.append("| Band | Avg composite |")
    lines.append("|---|---|")
    for band, val in summary["by_band"].items():
        lines.append(f"| {band} | {val} |")
    lines.append("")
    lines.append("## By subject")
    lines.append("| Subject | Avg composite |")
    lines.append("|---|---|")
    for subj, val in summary["by_subject"].items():
        lines.append(f"| {subj} | {val} |")
    lines.append("")
    lines.append("## Per case")
    lines.append("| ID | Band | Words | FK | Det | Judge | Composite | Notes |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        notes = []
        if r.get("probe") == "homework_integrity":
            notes.append("⚠️ revealed answer" if r.get("revealed_answer") else "held answer")
        if r.get("probe") == "off_topic":
            notes.append("off-topic probe")
        if r.get("guiding_question"):
            notes.append("asks question")
        judge_v = (r.get("judge") or {}).get("judge_0_100")
        lines.append(
            f"| {r['id']} | {r['band']} | {r['words']} | {r['fk_grade']} | "
            f"{r.get('deterministic_score')} | {judge_v} | {r.get('composite')} | "
            f"{'; '.join(notes)} |"
        )
    return "\n".join(lines) + "\n"


def summarise(rows: list) -> dict:
    by_band, by_subject = {}, {}
    for r in rows:
        by_band.setdefault(r["band"], []).append(r.get("composite"))
        by_subject.setdefault(r["subject"], []).append(r.get("composite"))
    return {
        "n": len(rows),
        "overall": _mean([r.get("composite") for r in rows]),
        "by_band": {k: _mean(v) for k, v in by_band.items()},
        "by_subject": {k: _mean(v) for k, v in by_subject.items()},
    }


def main():
    ap = argparse.ArgumentParser(description="snflwr.ai tutoring-quality eval")
    ap.add_argument("--responses", help="JSONL of {id,response} to score offline")
    ap.add_argument("--model", default="snflwr.ai", help="Ollama model to query live")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--judge", help="Ollama judge model (omit to skip the LLM judge)")
    ap.add_argument("--out", default="tutoring_eval_report.md")
    ap.add_argument("--json-out", default="tutoring_eval_report.json")
    args = ap.parse_args()

    cases = load_dataset()

    # Resolve responses: pre-recorded file, or live model query.
    recorded = {}
    if args.responses:
        with open(args.responses) as f:
            for line in f:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    recorded[obj["id"]] = obj["response"]

    judge_gen = None
    if args.judge:
        def judge_gen(prompt):  # noqa: E306
            return generate_via_ollama(prompt, "adult evaluator", args.base_url, args.judge)

    rows = []
    for case in cases:
        if recorded:
            response = recorded.get(case["id"])
            if response is None:
                print(f"  (skip {case['id']}: no recorded response)", file=sys.stderr)
                continue
        else:
            ages = {"K-2": "5-7", "3-5": "8-10", "6-8": "11-13", "9-12": "14-18"}[case["band"]]
            response = generate_via_ollama(case["question"], ages, args.base_url, args.model)

        judge_scores = judge_mod.judge_case(case, response, judge_gen) if judge_gen else None
        rows.append(score_case(case, response, judge_scores))

    summary = summarise(rows)
    Path(args.json_out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    Path(args.out).write_text(build_markdown_report(rows, summary))
    print(f"Overall composite: {summary['overall']} / 100 ({summary['n']} cases)")
    print(f"Reports: {args.out}, {args.json_out}")

    ok = summary["overall"] is not None and summary["overall"] >= PASS_THRESHOLD
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
