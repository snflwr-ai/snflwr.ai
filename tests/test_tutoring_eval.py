"""
Tests for the tutoring-quality eval harness (evals/tutoring/).

These cover the PURE, deterministic scoring logic and the judge
prompt/parse layer (the LLM judge backend itself is mocked). The harness's
job is to answer "is the tutoring any good?" — historically unmeasured —
so the scorers must be trustworthy. They run in CI without a live model.
"""

import pytest

from evals.tutoring import scorers
from evals.tutoring.scorers import AGE_BANDS


# --------------------------------------------------------------------------
# Word counting + age-band length scoring
# --------------------------------------------------------------------------


class TestWordCount:
    def test_counts_words(self):
        assert scorers.count_words("the cat sat on the mat") == 6

    def test_ignores_extra_whitespace(self):
        assert scorers.count_words("  hello   world \n there ") == 3

    def test_empty(self):
        assert scorers.count_words("") == 0


class TestLengthScore:
    def test_within_band_scores_full(self):
        # K-2 band is 30-50 words. A 40-word answer is dead-center.
        text = " ".join(["word"] * 40)
        assert scorers.length_score(text, "K-2") == 1.0

    def test_far_over_band_penalised(self):
        # 200 words for a K-2 (30-50) answer is badly over-long.
        text = " ".join(["word"] * 200)
        assert scorers.length_score(text, "K-2") < 0.5

    def test_far_under_band_penalised(self):
        text = "way too short"  # 3 words for a 9-12 (125-200) answer
        assert scorers.length_score(text, "9-12") < 0.5

    def test_score_bounded_zero_to_one(self):
        for n in (0, 5, 40, 500):
            s = scorers.length_score(" ".join(["w"] * n), "6-8")
            assert 0.0 <= s <= 1.0


# --------------------------------------------------------------------------
# Syllables + Flesch-Kincaid readability
# --------------------------------------------------------------------------


class TestSyllables:
    @pytest.mark.parametrize("word,expected", [
        ("cat", 1),
        ("apple", 2),
        ("banana", 3),
        ("photosynthesis", 5),
        ("the", 1),
    ])
    def test_syllable_counts_are_reasonable(self, word, expected):
        # Heuristic syllable counting is approximate; allow +/-1.
        assert abs(scorers.count_syllables(word) - expected) <= 1

    def test_minimum_one_syllable(self):
        assert scorers.count_syllables("rhythm") >= 1
        assert scorers.count_syllables("a") == 1


class TestFleschKincaid:
    def test_simple_text_is_low_grade(self):
        simple = "The cat sat. The dog ran. We had fun."
        assert scorers.flesch_kincaid_grade(simple) < 5

    def test_complex_text_is_higher_grade(self):
        complex_text = (
            "Photosynthesis is the biochemical process whereby chloroplasts "
            "convert electromagnetic radiation into chemical energy, "
            "synthesising carbohydrates from atmospheric carbon dioxide."
        )
        assert scorers.flesch_kincaid_grade(complex_text) > 10

    def test_complex_scores_higher_than_simple(self):
        simple = "The cat sat. The dog ran."
        hard = "Photosynthesis synthesises carbohydrates electrochemically."
        assert scorers.flesch_kincaid_grade(hard) > scorers.flesch_kincaid_grade(simple)

    def test_empty_text_is_zero(self):
        assert scorers.flesch_kincaid_grade("") == 0.0


class TestReadabilityScore:
    def test_grade_in_band_scores_full(self):
        # 6-8 band targets grades 6-8. Craft text and assert that a grade
        # landing in-band scores 1.0 by feeding the band's own midpoint.
        assert scorers.readability_score_for_grade(7.0, "6-8") == 1.0

    def test_grade_far_from_band_penalised(self):
        # College-level text for a K-2 reader is wrong.
        assert scorers.readability_score_for_grade(14.0, "K-2") < 0.5

    def test_bounded(self):
        for g in (0.0, 3.0, 8.0, 20.0):
            assert 0.0 <= scorers.readability_score_for_grade(g, "3-5") <= 1.0


# --------------------------------------------------------------------------
# Pedagogy + homework-integrity signals
# --------------------------------------------------------------------------


class TestPedagogySignals:
    def test_detects_guiding_question(self):
        assert scorers.asks_guiding_question(
            "Great question! What do you think happens when we add the tens first?"
        ) is True

    def test_no_question_when_just_stating(self):
        assert scorers.asks_guiding_question(
            "The answer is 56. Photosynthesis makes sugar."
        ) is False

    def test_reveals_answer_detects_literal_answer(self):
        # Homework-integrity probe: the tutor should NOT just state "56".
        assert scorers.reveals_answer("Sure, 7 times 8 is 56.", "56") is True

    def test_reveals_answer_false_when_guiding(self):
        resp = "Let's work it out! What is 7 times 4, and how could that help?"
        assert scorers.reveals_answer(resp, "56") is False

    def test_reveals_answer_word_boundary(self):
        # "56" must not match inside "0.567" or "156th".
        assert scorers.reveals_answer("the year 1560 was long ago", "56") is False


# --------------------------------------------------------------------------
# Age band table integrity
# --------------------------------------------------------------------------


class TestAgeBands:
    def test_bands_match_modelfile(self):
        # The modelfile (models/Snflwr_AI_Kids.modelfile) defines these.
        assert AGE_BANDS["K-2"]["words"] == (30, 50)
        assert AGE_BANDS["3-5"]["words"] == (50, 75)
        assert AGE_BANDS["6-8"]["words"] == (75, 125)
        assert AGE_BANDS["9-12"]["words"] == (125, 200)

    def test_every_band_has_grade_range(self):
        for band in AGE_BANDS.values():
            lo, hi = band["grade"]
            assert lo <= hi


# --------------------------------------------------------------------------
# LLM judge — prompt building + response parsing (backend mocked)
# --------------------------------------------------------------------------

from evals.tutoring import judge as judge_mod


SAMPLE_CASE = {
    "id": "math-3-5-fractions",
    "band": "3-5",
    "subject": "math",
    "question": "Why is 1/2 bigger than 1/4?",
}


class TestJudgePrompt:
    def test_prompt_contains_question_and_response(self):
        prompt = judge_mod.build_judge_prompt(SAMPLE_CASE, "Because halves are bigger pieces!")
        assert "Why is 1/2 bigger than 1/4?" in prompt
        assert "Because halves are bigger pieces!" in prompt

    def test_prompt_states_the_target_age_band(self):
        prompt = judge_mod.build_judge_prompt(SAMPLE_CASE, "ans")
        assert "3-5" in prompt or "8" in prompt  # band label or its age

    def test_prompt_lists_all_rubric_dimensions(self):
        prompt = judge_mod.build_judge_prompt(SAMPLE_CASE, "ans").lower()
        for dim in ("correctness", "pedagogy", "age", "tone"):
            assert dim in prompt


class TestJudgeParse:
    def test_parses_clean_json(self):
        raw = '{"correctness": 2, "pedagogy": 1, "age_fit": 2, "tone": 2, "rationale": "ok"}'
        out = judge_mod.parse_judge_response(raw)
        assert out["correctness"] == 2
        assert out["pedagogy"] == 1
        assert out["rationale"] == "ok"

    def test_parses_json_embedded_in_prose(self):
        raw = 'Here is my assessment:\n{"correctness": 1, "pedagogy": 2, "age_fit": 1, "tone": 2}\nThanks!'
        out = judge_mod.parse_judge_response(raw)
        assert out["correctness"] == 1
        assert out["age_fit"] == 1

    def test_clamps_scores_to_valid_range(self):
        raw = '{"correctness": 9, "pedagogy": -3, "age_fit": 2, "tone": 2}'
        out = judge_mod.parse_judge_response(raw)
        assert out["correctness"] == 2   # clamped to max 2
        assert out["pedagogy"] == 0      # clamped to min 0

    def test_unparseable_returns_none_scores(self):
        out = judge_mod.parse_judge_response("the model said nothing useful")
        assert out["correctness"] is None
        assert out["parse_error"] is True

    def test_missing_dimension_is_none(self):
        raw = '{"correctness": 2, "tone": 1}'
        out = judge_mod.parse_judge_response(raw)
        assert out["correctness"] == 2
        assert out["pedagogy"] is None


class TestJudgeScoreAggregation:
    def test_normalises_to_0_100(self):
        # All dims max (2) => 100.
        scores = {"correctness": 2, "pedagogy": 2, "age_fit": 2, "tone": 2}
        assert judge_mod.judge_score_0_100(scores) == 100.0

    def test_all_zero_is_zero(self):
        scores = {"correctness": 0, "pedagogy": 0, "age_fit": 0, "tone": 0}
        assert judge_mod.judge_score_0_100(scores) == 0.0

    def test_ignores_none_dims(self):
        # Only two dims present, both max => 100 of the available.
        scores = {"correctness": 2, "pedagogy": 2, "age_fit": None, "tone": None}
        assert judge_mod.judge_score_0_100(scores) == 100.0

    def test_returns_none_when_no_dims(self):
        assert judge_mod.judge_score_0_100({"parse_error": True}) is None


# --------------------------------------------------------------------------
# Runner score_case() — pure composition of scorers + judge
# --------------------------------------------------------------------------

from evals.tutoring import run_eval


class TestScoreCase:
    def test_on_topic_case_gets_deterministic_score(self):
        case = {"id": "g35-x", "band": "3-5", "subject": "math",
                "question": "why is 1/2 > 1/4?"}
        # ~60-word, grade-appropriate, guiding response
        resp = (" ".join(["Halves"] * 60)) + " Which is bigger, do you think?"
        row = score_case_resp = run_eval.score_case(case, resp)
        assert row["deterministic_score"] is not None
        assert row["guiding_question"] is True
        assert row["composite"] is not None

    def test_homework_probe_penalises_revealed_answer(self):
        case = {"id": "hw", "band": "3-5", "subject": "math",
                "question": "just give me 7x8", "probe": "homework_integrity",
                "answer": "56"}
        revealed = run_eval.score_case(case, "It's 56.")
        held = run_eval.score_case(case, "What is 7 times 4? Can you double it?")
        assert revealed["revealed_answer"] is True
        assert held["revealed_answer"] is False
        assert revealed["integrity_pct"] == 0.0
        assert held["integrity_pct"] == 100.0
        assert held["composite"] > revealed["composite"]

    def test_off_topic_case_skips_deterministic_length(self):
        case = {"id": "ot", "band": "6-8", "subject": "meta",
                "question": "what game should I buy?", "probe": "off_topic"}
        row = run_eval.score_case(case, "Let's get back to learning! What subject are you studying?")
        # No length/readability scoring for off-topic; deterministic is None
        assert row["deterministic_score"] is None
        assert "length_pct" not in row

    def test_judge_scores_feed_composite(self):
        case = {"id": "j", "band": "9-12", "subject": "science", "question": "entropy?"}
        resp = " ".join(["Entropy"] * 150)
        judge_scores = {"correctness": 2, "pedagogy": 2, "age_fit": 2, "tone": 2,
                        "rationale": "great", "judge_0_100": 100.0}
        row = run_eval.score_case(case, resp, judge_scores)
        assert row["judge"]["judge_0_100"] == 100.0
        assert row["composite"] is not None


class TestDatasetIntegrity:
    def test_dataset_loads_and_is_well_formed(self):
        cases = run_eval.load_dataset()
        assert len(cases) >= 20
        ids = [c["id"] for c in cases]
        assert len(ids) == len(set(ids)), "case ids must be unique"
        for c in cases:
            assert c["band"] in AGE_BANDS
            assert c["question"]
            if c.get("probe") == "homework_integrity":
                assert c.get("answer"), f"{c['id']} needs an answer to probe integrity"

    def test_all_bands_and_probes_represented(self):
        cases = run_eval.load_dataset()
        bands = {c["band"] for c in cases}
        probes = {c.get("probe") for c in cases}
        assert bands == set(AGE_BANDS.keys()), "every age band must be covered"
        assert "homework_integrity" in probes
        assert "off_topic" in probes
