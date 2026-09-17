"""The Modelfile stays the single source of truth for persona and sampling.

Ollama applies `models/Snflwr_AI_Kids.modelfile` itself. vLLM applies nothing, so
the driver has to send the same persona text and the same PARAMETER values. If
those two drift, the two engines serve different tutors and every sealed number
describes only one of them.
"""

from pathlib import Path

import pytest

from core.inference import modelfile

REPO_MODELFILE = Path(__file__).resolve().parents[1] / "models" / "Snflwr_AI_Kids.modelfile"

SAMPLE = '''FROM gemma4:e4b

# a comment
SYSTEM """
You are a patient tutor.
Never give the answer away.
"""

PARAMETER temperature 0.7
PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.15
PARAMETER num_ctx 8192
PARAMETER stop "Student:"
PARAMETER stop "Human:"
'''


class TestParsing:
    def test_persona_is_the_system_block(self, tmp_path):
        path = tmp_path / "m.modelfile"
        path.write_text(SAMPLE)
        spec = modelfile.load(path)
        assert spec.persona == "You are a patient tutor.\nNever give the answer away."

    def test_sampling_parameters_are_typed(self, tmp_path):
        path = tmp_path / "m.modelfile"
        path.write_text(SAMPLE)
        spec = modelfile.load(path)
        assert spec.sampling["temperature"] == 0.7
        assert spec.sampling["top_k"] == 40
        assert spec.sampling["repeat_penalty"] == 1.15

    def test_stop_sequences_collect_into_a_list(self, tmp_path):
        path = tmp_path / "m.modelfile"
        path.write_text(SAMPLE)
        spec = modelfile.load(path)
        assert spec.sampling["stop"] == ["Student:", "Human:"]

    def test_server_side_parameters_are_not_sampling(self, tmp_path):
        """num_ctx sizes the engine, not a request."""
        path = tmp_path / "m.modelfile"
        path.write_text(SAMPLE)
        spec = modelfile.load(path)
        assert "num_ctx" not in spec.sampling
        assert spec.num_ctx == 8192

    def test_missing_file_is_an_explicit_error(self, tmp_path):
        with pytest.raises(modelfile.ModelfileError):
            modelfile.load(tmp_path / "nope.modelfile")

    def test_a_modelfile_without_a_system_block_is_an_error(self, tmp_path):
        path = tmp_path / "m.modelfile"
        path.write_text("FROM gemma4:e4b\nPARAMETER temperature 0.7\n")
        with pytest.raises(modelfile.ModelfileError):
            modelfile.load(path)


class TestRealModelfile:
    def test_the_shipped_modelfile_parses(self):
        spec = modelfile.load(REPO_MODELFILE)
        assert len(spec.persona) > 1000
        assert spec.sampling["temperature"] > 0

    def test_persona_checksum_is_stable_across_loads(self):
        first = modelfile.load(REPO_MODELFILE)
        second = modelfile.load(REPO_MODELFILE)
        assert first.persona_sha256 == second.persona_sha256

    def test_compliance_extractor_agrees_with_this_parser(self):
        """`evals.tutoring.compliance_canary` already extracts the persona for the
        compliance canary. Two extractors must not disagree about what the child
        is talked to with."""
        from evals.tutoring.compliance_canary import extract_system_prompt

        expected = extract_system_prompt(REPO_MODELFILE.read_text())
        assert modelfile.load(REPO_MODELFILE).persona == expected
