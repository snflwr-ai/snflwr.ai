#!/usr/bin/env python3
"""Production-faithful BACKBONE bake-off: which base model should snflwr.ai's
Modelfile be built FROM?

`bakeoff.py` queries RAW base models with no system prompt, so it only measures
raw model priors — every model "fails" homework-integrity because nothing tells
it to be a Socratic tutor. That's a methodology artifact, not a backbone signal.

This script instead ASSEMBLES each candidate into a production-shaped tutor:

    FROM <candidate>
    SYSTEM <the real Snflwr_AI_Kids.modelfile system prompt>
    PARAMETER ...   <the production generation params>
    # native chat template per model (we deliberately drop the qwen-specific
    # TEMPLATE override so a non-qwen backbone like gemma uses its own tokens)

It bakes those assembled models off on the same dataset/scorers/judge, and ALSO
records latency (tokens/s) and resident VRAM, so the winner is chosen on quality
*and* operational fit on a single ~23 GB GPU.

Usage (stronger-judge re-run — gemma4:31b vs e4b, judged by Claude):
  ANTHROPIC_API_KEY=... python -m evals.tutoring.backbone_bakeoff \
      --models gemma4:e4b,gemma4:31b --baseline gemma4:e4b \
      --judge-backend anthropic \
      --base-url http://172.22.0.4:11434 --container snflwr-ollama

  # Or the cheap local judge (weaker): --judge-backend ollama --judge gemma4:e4b
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring import run_eval
from evals.tutoring import judge as judge_mod
from evals.tutoring import judge_backends
from evals.tutoring import bakeoff

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELFILE_SRC = REPO_ROOT / "models" / "Snflwr_AI_Kids.modelfile"
GPU_TOTAL_GB = 23.0  # single card on this box


# --------------------------------------------------------------------------
# Assemble production-shaped models from each candidate base
# --------------------------------------------------------------------------


def modelfile_body() -> str:
    """The production Modelfile minus its FROM line and minus the qwen-specific
    TEMPLATE block — i.e. the SYSTEM prompt + PARAMETERs we carry onto every
    candidate, letting each model keep its native chat template."""
    text = MODELFILE_SRC.read_text()
    # Drop the TEMPLATE """ ... """ block (non-greedy, across newlines).
    text = re.sub(r'(?ms)^TEMPLATE\s+""".*?"""\s*', "", text)
    # Drop the FROM line; we prepend our own.
    text = re.sub(r"(?m)^FROM .*\n", "", text)
    return text.strip() + "\n"


def assembled_name(base: str) -> str:
    return "snflwr-bk-" + re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def build_assembled(base: str, container: str) -> str:
    """Create `snflwr-bk-<base>` in Ollama = FROM <base> + production body.
    Returns the assembled model name. Raises on failure."""
    name = assembled_name(base)
    content = f"FROM {base}\n\n{modelfile_body()}"
    tmp = Path(f"/tmp/{name}.modelfile")
    tmp.write_text(content)
    subprocess.run(
        ["docker", "cp", str(tmp), f"{container}:/tmp/{name}.modelfile"],
        check=True,
        capture_output=True,
    )
    proc = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "ollama",
            "create",
            name,
            "-f",
            f"/tmp/{name}.modelfile",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ollama create {name} failed:\n{proc.stderr}")
    return name


# --------------------------------------------------------------------------
# Generation with latency + VRAM capture
# --------------------------------------------------------------------------


def generate_with_metrics(
    question: str,
    ages: str,
    base_url: str,
    model: str,
    timeout: int = 180,
    retries: int = 2,
):
    """Like run_eval.generate_via_ollama but also returns Ollama's timing
    counters. Returns (content, metrics) where metrics has tokens_per_sec and
    total_s (None if the counters are absent).

    Retries transient 5xx like run_eval does — on a VRAM-constrained box Ollama
    can briefly 500 while (un)loading a model. A model that 500s on EVERY retry
    (e.g. a 23 GB model that segfaults loading with num_ctx 8192) raises, and
    main() records it as non-viable rather than letting it kill the whole run."""
    import time
    import urllib.error

    user = f"[Student age range: {ages}]\n{question}"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": user}],
            "stream": False,
            "think": False,
        }
    ).encode()
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code < 500 or attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    else:  # pragma: no cover
        raise last_err
    content = data.get("message", {}).get("content", "")
    eval_count = data.get("eval_count")
    eval_dur = data.get("eval_duration")  # ns
    total_dur = data.get("total_duration")  # ns
    tps = None
    if eval_count and eval_dur:
        tps = eval_count / (eval_dur / 1e9)
    metrics = {
        "tokens_per_sec": tps,
        "total_s": (total_dur / 1e9) if total_dur else None,
        "eval_count": eval_count,
    }
    return content, metrics


def resident_vram_gb(base_url: str, model: str):
    """Resident VRAM (GB) for `model` per Ollama /api/ps, or None."""
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/api/ps", timeout=15
        ) as resp:
            data = json.loads(resp.read())
        for m in data.get("models", []):
            if (
                m.get("name", "").split(":")[0] == model.split(":")[0]
                or m.get("name") == model
            ):
                return round(m.get("size_vram", 0) / 1e9, 1)
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------
# Run one assembled model (two-phase, OOM-safe) with perf capture
# --------------------------------------------------------------------------


def run_backbone(
    base: str, assembled: str, cases: list, *, base_url: str, judge_gen, timeout: int
) -> dict:
    # Phase 1: generate every response (only the candidate loaded) + metrics.
    responses, tps_samples, total_samples = {}, [], []
    vram = None
    for case in cases:
        content, metr = generate_with_metrics(
            case["question"],
            run_eval.BAND_AGES[case["band"]],
            base_url,
            assembled,
            timeout,
        )
        responses[case["id"]] = content
        if metr["tokens_per_sec"]:
            tps_samples.append(metr["tokens_per_sec"])
        if metr["total_s"]:
            total_samples.append(metr["total_s"])
        if vram is None:
            vram = resident_vram_gb(base_url, assembled)
    bakeoff.unload_model(base_url, assembled)

    # Phase 2: judge + score (only the judge loaded).
    rows = []
    for case in cases:
        response = responses.get(case["id"])
        if not response:
            print(f"  (skip {case['id']}: empty response)", file=sys.stderr)
            continue
        judge_scores = (
            judge_mod.judge_case(case, response, judge_gen) if judge_gen else None
        )
        rows.append(run_eval.score_case(case, response, judge_scores))

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    perf = {
        "tokens_per_sec": _avg(tps_samples),
        "avg_total_s": _avg(total_samples),
        "vram_gb": vram,
        "vram_headroom_gb": round(GPU_TOTAL_GB - vram, 1) if vram else None,
    }
    # Label rows/summary by the *base* model so reports read naturally.
    return {
        "model": base,
        "assembled": assembled,
        "rows": rows,
        "summary": run_eval.summarise(rows),
        "perf": perf,
    }


def _probe_score(rows, probe):
    vals = [
        r.get("composite")
        for r in rows
        if r.get("probe") == probe and r.get("composite") is not None
    ]
    return round(sum(vals) / len(vals), 1) if vals else None


def build_scorecard(results: list, cmp: dict) -> str:
    lines = ["", "## Backbone scorecard (quality · latency · VRAM)", ""]
    lines.append(
        "| Model | Overall | Homework-integ. | Off-topic | K-2 band | "
        "tok/s | Avg resp (s) | VRAM (GB) | Headroom (GB) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    order = [r["model"] for r in cmp["ranking"]]
    by_model = {r["model"]: r for r in results}
    for m in order:
        r = by_model.get(m)
        if not r:
            continue
        s = r["summary"]
        p = r["perf"]
        hw = _probe_score(r["rows"], "homework_integrity")
        ot = _probe_score(r["rows"], "off_topic")
        k2 = s.get("by_band", {}).get("K-2")

        def f(v):
            return "—" if v is None else v

        lines.append(
            f"| `{m}` | {f(s.get('overall'))} | {f(hw)} | {f(ot)} | {f(k2)} | "
            f"{f(p['tokens_per_sec'])} | {f(p['avg_total_s'])} | {f(p['vram_gb'])} | "
            f"{f(p['vram_headroom_gb'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="snflwr.ai production-faithful backbone bake-off"
    )
    ap.add_argument("--models", default="gemma4:e4b,gemma4:31b")
    ap.add_argument("--baseline", default="gemma4:e4b")
    ap.add_argument(
        "--judge",
        default=None,
        help="judge model id; for --judge-backend ollama an Ollama tag, "
        "for anthropic a Claude model (defaults to "
        f"{judge_backends.DEFAULT_ANTHROPIC_JUDGE}). Omit to skip judging.",
    )
    ap.add_argument(
        "--judge-backend",
        default="ollama",
        choices=["ollama", "anthropic"],
        help="ollama = local model (free, weaker); anthropic = Claude "
        "(stronger, neutral — needs ANTHROPIC_API_KEY + `pip install anthropic`)",
    )
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--container", default="snflwr-ollama")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--out", default="backbone_bakeoff_report.md")
    ap.add_argument("--json-out", default="backbone_bakeoff_report.json")
    args = ap.parse_args()

    bases = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = run_eval.load_dataset()

    judge_gen = None
    if args.judge_backend == "anthropic":
        judge_model = args.judge or judge_backends.DEFAULT_ANTHROPIC_JUDGE
        print(f"Judge: Claude (stronger, neutral) — {judge_model}", file=sys.stderr)
        judge_gen = judge_backends.build_judge(
            "anthropic", model=judge_model, base_url=args.base_url
        )
    elif args.judge:
        print(f"Judge: local Ollama — {args.judge}", file=sys.stderr)
        judge_gen = judge_backends.build_judge(
            "ollama", model=args.judge, base_url=args.base_url
        )

    # Build all assembled models up front so a build failure aborts before any
    # long generation runs.
    assembled = {}
    for base in bases:
        print(f"Assembling {base} -> {assembled_name(base)} ...", file=sys.stderr)
        assembled[base] = build_assembled(base, args.container)

    results, disqualified = [], []
    for base in bases:
        print(f"Running backbone: {base} (as {assembled[base]}) ...", file=sys.stderr)
        try:
            results.append(
                run_backbone(
                    base,
                    assembled[base],
                    cases,
                    base_url=args.base_url,
                    judge_gen=judge_gen,
                    timeout=args.timeout,
                )
            )
        except (
            Exception
        ) as e:  # noqa: BLE001 — a non-viable backbone is data, not a crash
            reason = str(e).splitlines()[0][:200]
            print(
                f"  ⚠️  {base} DISQUALIFIED (non-viable on this GPU): {reason}",
                file=sys.stderr,
            )
            disqualified.append({"model": base, "reason": reason})
            bakeoff.unload_model(args.base_url, assembled[base])
        if args.judge:
            bakeoff.unload_model(args.base_url, args.judge)

    cmp = bakeoff.compare(results, baseline=args.baseline)
    report = bakeoff.build_comparison_report(cmp) + build_scorecard(results, cmp)
    if disqualified:
        report += "\n## Disqualified (non-viable on this GPU)\n\n"
        report += "| Model | Reason |\n|---|---|\n"
        for d in disqualified:
            report += f"| `{d['model']}` | {d['reason']} |\n"
    Path(args.out).write_text(report)
    Path(args.json_out).write_text(
        json.dumps(
            {"comparison": cmp, "results": results, "disqualified": disqualified},
            indent=2,
        )
    )

    print("\n" + cmp.get("recommendation", "no result"))
    if disqualified:
        print("Disqualified: " + ", ".join(d["model"] for d in disqualified))
    print(f"Reports: {args.out}, {args.json_out}")


if __name__ == "__main__":
    main()
