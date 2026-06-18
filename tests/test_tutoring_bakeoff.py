"""
Tests for the N-way model bake-off (evals/tutoring/bakeoff.py).

These cover the PURE comparison + report logic (ranking, deltas, per-band/
per-subject winners, pain-point movers, the ship recommendation) and the
model-running path with an injected response function — so the whole suite
runs in CI with no live Ollama model.
"""

from evals.tutoring import bakeoff
from evals.tutoring import run_eval


# A model "result" is {model, rows, summary}. Build them from rows so the
# summary is computed by the real run_eval.summarise().
def _result(model, rows):
    return {"model": model, "rows": rows, "summary": run_eval.summarise(rows)}


def _row(cid, band, subject, composite, probe=None):
    return {"id": cid, "band": band, "subject": subject, "probe": probe,
            "composite": composite}


# Two cases per model: one 6-8 math (a known pain band), one homework probe.
def _roster():
    base = _result("qwen3.5:9b", [
        _row("c1", "6-8", "math", 60.0),
        _row("c2", "3-5", "math", 50.0, probe="homework_integrity"),
    ])
    strong = _result("qwen3.5:35b-a3b", [
        _row("c1", "6-8", "math", 80.0),
        _row("c2", "3-5", "math", 90.0, probe="homework_integrity"),
    ])
    edge = _result("gemma4:e4b", [
        _row("c1", "6-8", "math", 55.0),
        _row("c2", "3-5", "math", 52.0, probe="homework_integrity"),
    ])
    return [base, strong, edge]


class TestRankingAndDeltas:
    def test_ranks_by_overall_and_marks_baseline(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        # Strong model (avg 85) ranks above baseline (avg 55) and edge (avg 53.5).
        assert cmp["ranking"][0]["model"] == "qwen3.5:35b-a3b"
        assert cmp["winner"] == "qwen3.5:35b-a3b"
        assert any(r["is_baseline"] and r["model"] == "qwen3.5:9b" for r in cmp["ranking"])

    def test_delta_is_relative_to_baseline(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        deltas = {r["model"]: r["delta_vs_baseline"] for r in cmp["ranking"]}
        assert deltas["qwen3.5:9b"] == 0.0
        assert deltas["qwen3.5:35b-a3b"] == 30.0   # 85 - 55
        assert deltas["gemma4:e4b"] == -1.5        # 53.5 - 55

    def test_baseline_defaults_to_first_model_when_unspecified(self):
        cmp = bakeoff.compare(_roster())
        assert cmp["baseline"] == "qwen3.5:9b"

    def test_unknown_baseline_falls_back_to_first(self):
        cmp = bakeoff.compare(_roster(), baseline="nope:latest")
        assert cmp["baseline"] == "qwen3.5:9b"


class TestDimensionWinners:
    def test_per_band_winner_is_highest_scorer(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        assert cmp["by_band"]["winners"]["6-8"] == "qwen3.5:35b-a3b"
        assert cmp["by_band"]["winners"]["3-5"] == "qwen3.5:35b-a3b"

    def test_per_subject_table_has_all_models(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        assert set(cmp["by_subject"]["table"]["math"].keys()) == {
            "qwen3.5:9b", "qwen3.5:35b-a3b", "gemma4:e4b"}


class TestMovers:
    def test_flags_homework_and_younger_band_as_pain_points(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        pains = {(m["id"], m["challenger"]) for m in cmp["movers"] if m["pain_point"]}
        # Both cases (6-8 band, and 3-5 homework_integrity) are pain points.
        assert ("c1", "qwen3.5:35b-a3b") in pains
        assert ("c2", "qwen3.5:35b-a3b") in pains

    def test_mover_delta_sign_matches_improvement(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        m = next(x for x in cmp["movers"]
                 if x["id"] == "c2" and x["challenger"] == "qwen3.5:35b-a3b")
        assert m["delta"] == 40.0  # 90 - 50, a big homework-integrity gain
        loss = next(x for x in cmp["movers"]
                    if x["id"] == "c1" and x["challenger"] == "gemma4:e4b")
        assert loss["delta"] == -5.0


class TestRecommendation:
    def test_recommends_switch_when_margin_cleared(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        assert "Switch to" in cmp["recommendation"]
        assert "qwen3.5:35b-a3b" in cmp["recommendation"]

    def test_keeps_baseline_when_it_wins(self):
        rows = [_result("qwen3.5:9b", [_row("c1", "6-8", "math", 90.0)]),
                _result("gemma4:e4b", [_row("c1", "6-8", "math", 50.0)])]
        cmp = bakeoff.compare(rows, baseline="qwen3.5:9b")
        assert "Keep" in cmp["recommendation"]
        assert cmp["winner"] == "qwen3.5:9b"

    def test_lean_when_margin_too_small(self):
        rows = [_result("qwen3.5:9b", [_row("c1", "6-8", "math", 80.0)]),
                _result("big:model", [_row("c1", "6-8", "math", 81.0)])]  # +1 < margin
        cmp = bakeoff.compare(rows, baseline="qwen3.5:9b")
        assert "Lean" in cmp["recommendation"]
        assert "judge noise" in cmp["recommendation"]


class TestReport:
    def test_report_contains_recommendation_and_tables(self):
        cmp = bakeoff.compare(_roster(), baseline="qwen3.5:9b")
        md = bakeoff.build_comparison_report(cmp)
        assert "# Tutoring-Quality Model Bake-off" in md
        assert "Recommendation:" in md
        assert "## Overall" in md
        assert "## By age band" in md
        assert "qwen3.5:35b-a3b" in md

    def test_empty_results_is_graceful(self):
        cmp = bakeoff.compare([])
        assert cmp["ranking"] == []
        md = bakeoff.build_comparison_report(cmp)
        assert "No models produced results" in md


class TestRunModelInjected:
    def test_run_model_uses_injected_responses_and_scores(self):
        cases = [{"id": "g68-math", "band": "6-8", "subject": "math",
                  "question": "what is 12 x 11?"}]

        def fake_response(model, case):
            return " ".join(["Twelve"] * 100) + " What do you think the first step is?"

        result = bakeoff.run_model("fake:model", cases, base_url="http://unused",
                                   response_for=fake_response)
        assert result["model"] == "fake:model"
        assert len(result["rows"]) == 1
        assert result["summary"]["overall"] is not None

    def test_run_model_skips_none_responses(self):
        cases = [{"id": "x", "band": "K-2", "subject": "math", "question": "2+2?"}]
        result = bakeoff.run_model("m", cases, base_url="http://unused",
                                   response_for=lambda model, case: None)
        assert result["rows"] == []

    def test_two_phase_generates_all_before_judging(self):
        # All generation must happen (and the gen model be unloaded) BEFORE any
        # judging — that ordering is what keeps a single GPU from co-loading.
        cases = [{"id": "a", "band": "K-2", "subject": "math", "question": "q1"},
                 {"id": "b", "band": "K-2", "subject": "math", "question": "q2"}]
        events = []

        def fake_response(model, case):
            events.append(("gen", case["id"]))
            return " ".join(["word"] * 40)

        def fake_unload(model):
            events.append(("unload", model))

        def fake_judge(prompt):
            events.append(("judge", None))
            return '{"correctness": 2, "pedagogy": 2, "age_fit": 2, "tone": 2}'

        bakeoff.run_model("m", cases, base_url="http://unused",
                          response_for=fake_response, judge_gen=fake_judge, unload=fake_unload)
        kinds = [e[0] for e in events]
        # Both gens, then the unload, then the judges — never gen after judge.
        assert kinds == ["gen", "gen", "unload", "judge", "judge"]
