#!/usr/bin/env python3
"""
Three-way (N-way) model bake-off for the snflwr.ai tutoring-quality eval.

The single-model runner (`run_eval.py`) answers "is *this* model's tutoring any
good?". This answers the next question: "which model should we ship?" — by
running the *same* dataset, scorers, and judge across several candidate models
and laying the results side by side.

It exists to settle the model-family question raised by the June 2026 research:
the incumbent `qwen3.5:9b` over-writes for younger kids and leaks homework
answers, and leaderboards don't predict tutoring quality (problem-solving vs.
pedagogy correlate only ~0.42). So we don't switch on faith — we bake off the
incumbent against the upgrade candidates and let the age-band / homework-
integrity / off-topic scores decide.

Usage:
  # Default bake-off: incumbent vs the two upgrade candidates, with the judge.
  python -m evals.tutoring.bakeoff --judge qwen3.5:9b

  # Custom roster + baseline:
  python -m evals.tutoring.bakeoff \
      --models qwen3.5:9b,qwen3.5:35b-a3b,gemma4:e4b \
      --baseline qwen3.5:9b --judge qwen3.5:9b
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running both as a module and as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.tutoring import run_eval
from evals.tutoring import judge as judge_mod

# The default roster: incumbent first (it is also the default baseline), then
# the two upgrade candidates surfaced by the model research.
DEFAULT_MODELS = ["qwen3.5:9b", "qwen3.5:35b-a3b", "gemma4:e4b"]

# A challenger must beat the baseline by at least this many composite points to
# count as a real, ship-worthy improvement rather than judge noise.
MEANINGFUL_MARGIN = 2.0


# --------------------------------------------------------------------------
# Pure comparison logic (no model calls — unit-tested directly)
# --------------------------------------------------------------------------


def _rows_by_id(rows: list) -> dict:
    return {r["id"]: r for r in rows}


def _best(pairs):
    """(model, value) pairs -> model with the max non-None value, or None."""
    scored = [(m, v) for m, v in pairs if v is not None]
    return max(scored, key=lambda mv: mv[1])[0] if scored else None


def compare(results: list, baseline: str | None = None) -> dict:
    """Turn per-model {model, rows, summary} dicts into a comparison.

    Returns rankings, per-band/per-subject winners, the biggest per-case movers
    vs. the baseline, and a ship recommendation.
    """
    results = [r for r in results if r.get("rows") is not None]
    if not results:
        return {"models": [], "ranking": [], "recommendation": "No models produced results."}

    models = [r["model"] for r in results]
    if baseline is None or baseline not in models:
        baseline = models[0]

    summ = {r["model"]: r["summary"] for r in results}
    rows_by_model = {r["model"]: _rows_by_id(r["rows"]) for r in results}
    base_overall = summ[baseline]["overall"]

    # Overall ranking with delta vs. baseline.
    ranking = []
    for m in models:
        ov = summ[m]["overall"]
        delta = round(ov - base_overall, 1) if (ov is not None and base_overall is not None) else None
        ranking.append({"model": m, "overall": ov, "delta_vs_baseline": delta,
                        "is_baseline": m == baseline})
    ranking.sort(key=lambda x: (x["overall"] is not None, x["overall"] or 0), reverse=True)

    # Per-band and per-subject: model -> score, plus the winner of each.
    def _dimension(key):
        keys = sorted({k for m in models for k in summ[m].get(key, {})})
        table, winners = {}, {}
        for dim in keys:
            per = {m: summ[m].get(key, {}).get(dim) for m in models}
            table[dim] = per
            winners[dim] = _best(per.items())
        return {"table": table, "winners": winners}

    by_band = _dimension("by_band")
    by_subject = _dimension("by_subject")

    # Biggest per-case movers vs. baseline, with the known pain points flagged.
    movers = []
    base_rows = rows_by_model[baseline]
    challengers = [m for m in models if m != baseline]
    for cid, brow in base_rows.items():
        for m in challengers:
            crow = rows_by_model[m].get(cid)
            if not crow:
                continue
            bc, cc = brow.get("composite"), crow.get("composite")
            if bc is None or cc is None:
                continue
            movers.append({
                "id": cid, "band": brow["band"], "subject": brow["subject"],
                "probe": brow.get("probe"), "challenger": m,
                "baseline_composite": bc, "challenger_composite": cc,
                "delta": round(cc - bc, 1),
                # Pain points the research told us to watch.
                "pain_point": brow.get("probe") in ("homework_integrity", "off_topic")
                              or brow["band"] in ("6-8", "3-5"),
            })
    movers.sort(key=lambda x: x["delta"])  # most-regressed first; reverse for gains

    # Recommendation: best challenger vs. baseline by a meaningful margin.
    best = ranking[0]
    if best["model"] == baseline or best["delta_vs_baseline"] is None:
        rec = (f"Keep **{baseline}** — no challenger beats it on overall composite.")
    elif best["delta_vs_baseline"] >= MEANINGFUL_MARGIN:
        rec = (f"Switch to **{best['model']}** — it beats the baseline "
               f"{baseline} by {best['delta_vs_baseline']:+} composite points "
               f"(>= {MEANINGFUL_MARGIN} margin).")
    else:
        rec = (f"Lean **{best['model']}** but it only edges {baseline} by "
               f"{best['delta_vs_baseline']:+} points (< {MEANINGFUL_MARGIN} margin) "
               f"— inside judge noise; confirm on the pain-point cases before switching.")

    return {
        "models": models, "baseline": baseline, "ranking": ranking,
        "by_band": by_band, "by_subject": by_subject, "movers": movers,
        "recommendation": rec, "winner": best["model"],
    }


def _fmt(v):
    return "—" if v is None else f"{v}"


def build_comparison_report(cmp: dict) -> str:
    if not cmp.get("ranking"):
        return "# Model Bake-off\n\nNo models produced results.\n"

    models = cmp["models"]
    lines = ["# Tutoring-Quality Model Bake-off", ""]
    lines.append(f"**Recommendation:** {cmp['recommendation']}")
    lines.append("")
    lines.append(f"Baseline: `{cmp['baseline']}` · Winner: `{cmp['winner']}`")
    lines.append("")

    lines.append("## Overall")
    lines.append("| Rank | Model | Overall | Δ vs baseline |")
    lines.append("|---|---|---|---|")
    for i, r in enumerate(cmp["ranking"], 1):
        tag = " (baseline)" if r["is_baseline"] else ""
        d = r["delta_vs_baseline"]
        d_s = "—" if d is None else f"{d:+}"
        lines.append(f"| {i} | `{r['model']}`{tag} | {_fmt(r['overall'])} | {d_s} |")
    lines.append("")

    def _dim_table(title, dim):
        out = [f"## By {title}", "| " + title.capitalize() + " | " +
               " | ".join(f"`{m}`" for m in models) + " | Winner |",
               "|" + "---|" * (len(models) + 2)]
        for k, per in dim["table"].items():
            cells = " | ".join(_fmt(per.get(m)) for m in models)
            out.append(f"| {k} | {cells} | `{dim['winners'].get(k)}` |")
        out.append("")
        return out

    lines += _dim_table("age band", cmp["by_band"])
    lines += _dim_table("subject", cmp["by_subject"])

    # Pain-point movers: where challengers most help/hurt on the cases we care about.
    pain = [m for m in cmp["movers"] if m["pain_point"]]
    if pain:
        gains = sorted([m for m in pain if m["delta"] > 0], key=lambda x: -x["delta"])[:8]
        losses = sorted([m for m in pain if m["delta"] < 0], key=lambda x: x["delta"])[:8]
        lines.append("## Pain-point cases (younger bands · homework integrity · off-topic)")
        lines.append("| Case | Band | Probe | Challenger | Baseline | Challenger | Δ |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in losses + gains:
            lines.append(
                f"| {m['id']} | {m['band']} | {m.get('probe') or '—'} | `{m['challenger']}` | "
                f"{m['baseline_composite']} | {m['challenger_composite']} | {m['delta']:+} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Live model running (thin wrappers over run_eval)
# --------------------------------------------------------------------------


def available_models(base_url: str, timeout: int = 10) -> set:
    """Names of models the Ollama server has pulled (best-effort)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read())
        return {m["name"] for m in data.get("models", [])}
    except Exception as e:  # noqa: BLE001 — availability check is best-effort
        print(f"  (could not list models at {base_url}: {e})", file=sys.stderr)
        return set()


def unload_model(base_url: str, model: str, timeout: int = 30) -> None:
    """Ask Ollama to evict a model from VRAM (keep_alive=0). Best-effort.

    Critical on a single constrained GPU: a big generation model (e.g. a 23 GB
    MoE) and the judge model cannot both be resident, so we unload between
    phases instead of letting Ollama OOM trying to co-load them."""
    import urllib.request
    try:
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/generate",
            data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except Exception as e:  # noqa: BLE001 — unload is best-effort
        print(f"  (unload {model} failed, continuing: {e})", file=sys.stderr)


def run_model(model: str, cases: list, *, base_url: str, judge_gen=None,
              timeout: int = 120, response_for=None, unload=None) -> dict:
    """Generate + score every case for one model, in TWO PHASES so only one
    model is ever resident in VRAM:

      1. generate every response (only `model` loaded), then `unload(model)`
      2. judge every response (only the judge model loaded) and score

    This avoids the gen-model/judge-model co-load that OOMs a single GPU, and
    cuts model swaps from ~2*N to ~1 per model. `response_for(model, case)` and
    `unload(model)` are injectable for testing; they default to live Ollama.
    """
    if response_for is None:
        def response_for(m, case):  # noqa: E306
            return run_eval.generate_via_ollama(
                case["question"], run_eval.BAND_AGES[case["band"]], base_url, m, timeout)

    # Phase 1: generate all responses with only the generation model loaded.
    responses = {}
    for case in cases:
        responses[case["id"]] = response_for(model, case)
    if unload is not None:
        unload(model)  # free the gen model's VRAM before the judge loads

    # Phase 2: judge + score with only the judge model loaded.
    rows = []
    for case in cases:
        response = responses.get(case["id"])
        if response is None:
            print(f"  (skip {case['id']}: no response)", file=sys.stderr)
            continue
        judge_scores = judge_mod.judge_case(case, response, judge_gen) if judge_gen else None
        rows.append(run_eval.score_case(case, response, judge_scores))
    return {"model": model, "rows": rows, "summary": run_eval.summarise(rows)}


def main():
    ap = argparse.ArgumentParser(description="snflwr.ai tutoring-quality model bake-off")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated Ollama model tags to bake off")
    ap.add_argument("--baseline", default=None,
                    help="model to measure deltas against (default: first/incumbent)")
    ap.add_argument("--judge", help="Ollama judge model (omit to skip the LLM judge)")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--out", default="tutoring_bakeoff_report.md")
    ap.add_argument("--json-out", default="tutoring_bakeoff_report.json")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = run_eval.load_dataset()

    judge_gen = None
    if args.judge:
        def judge_gen(prompt):  # noqa: E306
            return run_eval.generate_via_ollama(prompt, "adult evaluator", args.base_url, args.judge)

    present = available_models(args.base_url)
    runnable = []
    for m in models:
        if present and m not in present:
            print(f"  ⚠️  skipping {m}: not pulled. Run:  ollama pull {m}", file=sys.stderr)
            continue
        runnable.append(m)

    def unload(m):
        unload_model(args.base_url, m)

    results = []
    for m in runnable:
        print(f"Running bake-off model: {m} ...", file=sys.stderr)
        results.append(run_model(m, cases, base_url=args.base_url, judge_gen=judge_gen,
                                 unload=unload))
        # Evict the judge too, so the next model's generation phase has the
        # whole GPU to itself.
        if args.judge:
            unload(args.judge)

    cmp = compare(results, baseline=args.baseline)
    Path(args.json_out).write_text(json.dumps({"comparison": cmp, "results": results}, indent=2))
    Path(args.out).write_text(build_comparison_report(cmp))

    print("\n" + cmp.get("recommendation", "no result"))
    print(f"Reports: {args.out}, {args.json_out}")


if __name__ == "__main__":
    main()
