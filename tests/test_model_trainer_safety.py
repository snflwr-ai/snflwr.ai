"""
Tests for safety model self-learning system.
Verifies edge case logging, confidence thresholds,
human override detection, and training statistics.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import sqlite3

import sys

import pytest

from safety.model_trainer import SafetyModelTrainer

_model_trainer_mod = sys.modules["safety.model_trainer"]


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_query = MagicMock(return_value=[])
    db.execute_write = MagicMock(return_value=None)
    return db


@pytest.fixture
def trainer(mock_db, tmp_path):
    with patch.object(_model_trainer_mod, "db_manager", mock_db), \
         patch.object(_model_trainer_mod, "ollama_client", MagicMock()):
        t = SafetyModelTrainer.__new__(SafetyModelTrainer)
        t.db = mock_db
        t.ollama = MagicMock()
        t.EDGE_CASES_PATH = tmp_path / "edge_cases.jsonl"
        t.EDGE_CASES_PATH.touch()
        t.MODELFILE_PATH = None
        t.LOW_CONFIDENCE_THRESHOLD = 0.7
        t.HUMAN_OVERRIDE_THRESHOLD = 0.5
        t.SAFETY_MODEL_NAME = "llama-guard3:1b"
        t.BASE_MODEL = "llama-guard3:1b"
    return t


# ---------------------------------------------------------------------------
# should_log_as_edge_case — threshold logic
# ---------------------------------------------------------------------------


class TestShouldLogAsEdgeCase:
    def test_always_logs_human_override(self, trainer):
        classification = {"confidence": 0.99, "is_safe": True}
        assert trainer.should_log_as_edge_case(classification, human_override=True) is True

    def test_logs_low_confidence(self, trainer):
        classification = {"confidence": 0.5, "is_safe": True}
        assert trainer.should_log_as_edge_case(classification) is True

    def test_logs_borderline(self, trainer):
        classification = {"confidence": 0.50, "is_safe": True}
        assert trainer.should_log_as_edge_case(classification) is True

    def test_does_not_log_high_confidence(self, trainer):
        classification = {"confidence": 0.95, "is_safe": True}
        assert trainer.should_log_as_edge_case(classification) is False

    def test_logs_at_threshold_boundary(self, trainer):
        # 0.48 is in borderline range
        assert trainer.should_log_as_edge_case({"confidence": 0.48}) is True
        # 0.52 is in borderline range
        assert trainer.should_log_as_edge_case({"confidence": 0.52}) is True
        # 0.53 is above borderline but below LOW_CONFIDENCE_THRESHOLD
        assert trainer.should_log_as_edge_case({"confidence": 0.53}) is True

    def test_missing_confidence_treated_as_zero(self, trainer):
        # No confidence key → defaults to 0, which is < LOW_CONFIDENCE_THRESHOLD
        assert trainer.should_log_as_edge_case({}) is True


# ---------------------------------------------------------------------------
# log_edge_case — file + database logging
# ---------------------------------------------------------------------------


class TestLogEdgeCase:
    def test_logs_to_jsonl_file(self, trainer):
        classification = {"confidence": 0.4, "is_safe": False}
        result = trainer.log_edge_case(
            input_text="test input",
            classification=classification,
            actual_outcome="blocked",
            human_override=False,
            context="age:10",
        )
        assert result is True
        content = trainer.EDGE_CASES_PATH.read_text()
        parsed = json.loads(content.strip())
        assert parsed["input"] == "test input"
        assert parsed["classification"]["confidence"] == 0.4
        assert parsed["actual_outcome"] == "blocked"
        assert parsed["context"] == "age:10"

    def test_truncates_long_input(self, trainer):
        long_input = "x" * 1000
        trainer.log_edge_case(long_input, {"confidence": 0.3})
        content = trainer.EDGE_CASES_PATH.read_text()
        parsed = json.loads(content.strip())
        assert len(parsed["input"]) == 500

    def test_logs_to_database(self, trainer, mock_db):
        trainer.log_edge_case("test", {"confidence": 0.3})
        # Should call execute_query twice: CREATE TABLE + INSERT
        assert mock_db.execute_query.call_count == 2

    def test_handles_file_error(self, trainer):
        trainer.EDGE_CASES_PATH = Path("/nonexistent/dir/edge.jsonl")
        result = trainer.log_edge_case("test", {"confidence": 0.3})
        assert result is False

    def test_handles_db_error(self, trainer, mock_db):
        mock_db.execute_query.side_effect = sqlite3.OperationalError("no table")
        # Should still succeed (file write works, db error is caught)
        result = trainer.log_edge_case("test", {"confidence": 0.3})
        assert result is True  # file write succeeded


# ---------------------------------------------------------------------------
# get_edge_cases_for_review
# ---------------------------------------------------------------------------


class TestGetEdgeCasesForReview:
    def test_returns_most_recent_first(self, trainer):
        for i in range(5):
            edge = {"timestamp": f"2026-01-0{i+1}T00:00:00", "input": f"case{i}"}
            trainer.EDGE_CASES_PATH.write_text(
                trainer.EDGE_CASES_PATH.read_text() + json.dumps(edge) + "\n"
            )
        cases = trainer.get_edge_cases_for_review(limit=3)
        assert len(cases) == 3
        assert cases[0]["input"] == "case4"  # most recent

    def test_empty_file(self, trainer):
        cases = trainer.get_edge_cases_for_review()
        assert cases == []

    def test_handles_file_error(self, trainer):
        trainer.EDGE_CASES_PATH = Path("/nonexistent/edge.jsonl")
        cases = trainer.get_edge_cases_for_review()
        assert cases == []


# ---------------------------------------------------------------------------
# regenerate_modelfile_with_examples
# ---------------------------------------------------------------------------


class TestRegenerateModelfile:
    def test_no_modelfile_returns_error(self, trainer):
        trainer.MODELFILE_PATH = None
        ok, err = trainer.regenerate_modelfile_with_examples([])
        assert not ok
        assert "not supported" in err.lower()

    def test_missing_modelfile_returns_error(self, trainer, tmp_path):
        trainer.MODELFILE_PATH = tmp_path / "nonexistent.modelfile"
        ok, err = trainer.regenerate_modelfile_with_examples([])
        assert not ok
        assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# retrain_safety_model
# ---------------------------------------------------------------------------


class TestRetrainModel:
    def test_no_modelfile_returns_error(self, trainer):
        trainer.MODELFILE_PATH = None
        ok, err = trainer.retrain_safety_model()
        assert not ok
        assert "not supported" in err.lower()


# ---------------------------------------------------------------------------
# _format_examples_from_cases
# ---------------------------------------------------------------------------


class TestFormatExamples:
    def test_formats_reviewed_cases(self, trainer):
        cases = [
            {
                "input": "is this safe?",
                "human_reviewed": True,
                "correct_classification": {"is_safe": True, "confidence": 0.95},
            },
            {
                "input": "not reviewed",
                "human_reviewed": False,
                "correct_classification": {"is_safe": True},
            },
        ]
        examples = trainer._format_examples_from_cases(cases)
        assert len(examples) == 1
        assert "is this safe?" in examples[0]

    def test_skips_cases_without_correction(self, trainer):
        cases = [
            {"input": "test", "human_reviewed": True, "correct_classification": {}},
        ]
        examples = trainer._format_examples_from_cases(cases)
        assert len(examples) == 0


# ---------------------------------------------------------------------------
# _insert_new_examples
# ---------------------------------------------------------------------------


class TestInsertNewExamples:
    def test_inserts_before_must_respond(self, trainer):
        content = """SYSTEM: ...
FEW-SHOT EXAMPLES:
Example 1...

You MUST respond with ONLY valid JSON
"""
        examples = ['Input: "test"\nOutput: {"safe": true}']
        result = trainer._insert_new_examples(content, examples)
        assert "test" in result
        # Original content preserved
        assert "You MUST respond with ONLY valid JSON" in result

    def test_no_examples_section_returns_unchanged(self, trainer):
        content = "no examples section here"
        result = trainer._insert_new_examples(content, ["example"])
        assert result == content

    def test_no_must_respond_returns_unchanged(self, trainer):
        content = "FEW-SHOT EXAMPLES:\nsome examples"
        result = trainer._insert_new_examples(content, ["example"])
        assert result == content


# ---------------------------------------------------------------------------
# get_training_statistics
# ---------------------------------------------------------------------------


class TestTrainingStatistics:
    def test_returns_stats(self, trainer, mock_db):
        # Write some edge cases to file
        for i in range(3):
            trainer.EDGE_CASES_PATH.write_text(
                trainer.EDGE_CASES_PATH.read_text() + json.dumps({"case": i}) + "\n"
            )
        mock_db.execute_query.return_value = [
            {"total": 3, "overrides": 1, "reviewed": 2}
        ]
        stats = trainer.get_training_statistics()
        assert stats["total_edge_cases"] == 3
        assert stats["human_overrides"] == 1
        assert stats["cases_reviewed"] == 2
        assert stats["cases_pending_review"] == 1

    def test_handles_empty_db(self, trainer, mock_db):
        mock_db.execute_query.return_value = []
        stats = trainer.get_training_statistics()
        assert stats["total_edge_cases"] == 0
        assert stats["human_overrides"] == 0

    def test_handles_db_error(self, trainer, mock_db):
        mock_db.execute_query.side_effect = sqlite3.OperationalError("no table")
        stats = trainer.get_training_statistics()
        assert stats["total_edge_cases"] == 0


# ---------------------------------------------------------------------------
# _log_to_database
# ---------------------------------------------------------------------------


class TestLogToDatabase:
    def test_creates_table_and_inserts(self, trainer, mock_db):
        edge_case = {
            "timestamp": "2026-01-01T00:00:00",
            "input": "test text",
            "classification": {"confidence": 0.3},
            "actual_outcome": "blocked",
            "human_override": False,
            "context": "age:8",
        }
        trainer._log_to_database(edge_case)
        # First call: CREATE TABLE, second: INSERT
        assert mock_db.execute_query.call_count == 2
        create_sql = mock_db.execute_query.call_args_list[0][0][0]
        assert "CREATE TABLE IF NOT EXISTS safety_edge_cases" in create_sql

    def test_handles_db_error_silently(self, trainer, mock_db):
        mock_db.execute_query.side_effect = sqlite3.OperationalError("error")
        edge_case = {
            "timestamp": "t", "input": "i", "classification": {},
            "actual_outcome": None, "human_override": False, "context": None,
        }
        # Should not raise
        trainer._log_to_database(edge_case)
