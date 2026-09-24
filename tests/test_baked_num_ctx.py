"""The baked context window must be able to hold a real turn.

`models/Snflwr_AI_Kids.modelfile` shipped `PARAMETER num_ctx 8192`, described in
its own comment as a value "only used when invoking Ollama directly outside the
API". That framing made it look harmless. It was not.

Measured 2026-09-24 against the deployed tutor: a request whose student text is
the single character "x" evaluates **8,297 prompt tokens**, because this system
prompt is ~34,650 characters of K-12 safety and pedagogy instruction. 8,297 does
not fit in 8,192.

⚠️ And it does not refuse. Ollama context-shifts -- its own log reports
`n_keep = 4` -- so the front of the system prompt is dropped and the turn is
served anyway. The failure mode of the old default was therefore: a tutor
answering a child with its safety instructions silently truncated, reporting
nothing. That is worse than an error, and no existing check looked at it.

These tests pin the two halves of the fix:
  * the committed fallback is large enough to be safe on its own;
  * the build-time substitution takes its value from the certified CEILING,
    not the sealed `num_ctx` -- those differ (24576 vs 16384 today), and baking
    the sealed value would ship a Modelfile that disagrees with the deployment
    it is part of.
"""

import pathlib
import re

import pytest

from core.serving_plan import CERTIFIED_BACKBONES, baked_num_ctx_for

REPO = pathlib.Path(__file__).resolve().parents[1]
MODELFILE = REPO / "models" / "Snflwr_AI_Kids.modelfile"

# Measured, not estimated: prompt_eval_count for a one-character student turn
# on snflwr.ai-31b, 2026-09-24. Recorded here so the floor has a source.
MEASURED_MINIMAL_TURN_TOKENS = 8297
# The Modelfile's own num_predict. A window that fits the prompt but not the
# reply truncates the answer instead of the instructions -- still a defect.
BAKED_NUM_PREDICT = 4096
REQUIRED_FLOOR = MEASURED_MINIMAL_TURN_TOKENS + BAKED_NUM_PREDICT


def _baked_value(text: str) -> int:
    m = re.search(r"^PARAMETER num_ctx\s+(\d+)\s*$", text, re.MULTILINE)
    assert m, "models/Snflwr_AI_Kids.modelfile has no PARAMETER num_ctx line"
    return int(m.group(1))


def test_the_committed_modelfile_holds_a_minimal_turn():
    """8192 could not, and failed by truncating the safety prompt in silence."""
    baked = _baked_value(MODELFILE.read_text(encoding="utf-8"))
    assert baked >= MEASURED_MINIMAL_TURN_TOKENS, (
        f"PARAMETER num_ctx {baked} cannot hold a minimal turn "
        f"({MEASURED_MINIMAL_TURN_TOKENS} prompt tokens measured on the "
        f"deployed tutor). Ollama will context-shift rather than refuse, so "
        f"the system prompt gets truncated and the turn is served anyway."
    )


def test_the_committed_modelfile_holds_a_full_response_too():
    baked = _baked_value(MODELFILE.read_text(encoding="utf-8"))
    assert baked >= REQUIRED_FLOOR, (
        f"PARAMETER num_ctx {baked} leaves no room for a reply: the prompt "
        f"alone is {MEASURED_MINIMAL_TURN_TOKENS} tokens and num_predict is "
        f"{BAKED_NUM_PREDICT}, so the floor is {REQUIRED_FLOOR}."
    )


def test_exactly_one_num_ctx_parameter_is_declared():
    """Two PARAMETER num_ctx lines would make precedence the deciding factor."""
    text = MODELFILE.read_text(encoding="utf-8")
    found = re.findall(r"^PARAMETER num_ctx\s+\d+\s*$", text, re.MULTILINE)
    assert len(found) == 1, (
        f"expected exactly one PARAMETER num_ctx line, found {len(found)}: "
        f"{found}. The build scripts REPLACE this line with sed; a second one "
        f"would leave which value wins up to Ollama's parsing order."
    )


class TestBakedNumCtxForModel:
    def test_the_certified_backbone_gets_its_validated_ceiling(self):
        entry = CERTIFIED_BACKBONES[0]
        assert baked_num_ctx_for(entry.model) == entry.validated_ceiling

    def test_it_is_the_ceiling_and_not_the_sealed_num_ctx(self):
        """The distinction this function exists for.

        `num_ctx` is what a sealed grading run measured; `validated_ceiling` is
        what a validation run showed the box can serve and what the API sends.
        While they differ, baking the sealed value ships a Modelfile that
        contradicts its own deployment.
        """
        entry = CERTIFIED_BACKBONES[0]
        if entry.validated_ceiling == entry.num_ctx:
            pytest.skip("ceiling and sealed value currently agree")
        assert baked_num_ctx_for(entry.model) != entry.num_ctx

    def test_an_unknown_model_returns_zero_so_callers_fall_back(self):
        assert baked_num_ctx_for("not-a-real-model") == 0

    def test_the_certified_ceiling_clears_the_measured_floor(self):
        entry = CERTIFIED_BACKBONES[0]
        assert baked_num_ctx_for(entry.model) >= REQUIRED_FLOOR, (
            "the certified ceiling itself cannot hold prompt + response"
        )


class TestBuildScriptsSubstitute:
    """Both install paths must bake the value, or one of them ships 24576 to a
    box that cannot serve it -- or worse, ships the fallback to a box that can."""

    @pytest.mark.parametrize("script", ["deploy.sh", "start_snflwr.sh"])
    def test_the_script_replaces_the_num_ctx_line(self, script):
        text = (REPO / script).read_text(encoding="utf-8")
        assert "baked_num_ctx_for" in text, (
            f"{script} does not consult the certificate for num_ctx"
        )
        assert re.search(r"sed -i .*PARAMETER num_ctx", text), (
            f"{script} does not REPLACE the PARAMETER num_ctx line. Appending "
            f"a second one leaves precedence undefined."
        )

    @pytest.mark.parametrize("script", ["deploy.sh", "start_snflwr.sh"])
    def test_the_script_falls_back_for_an_uncertified_base(self, script):
        text = (REPO / script).read_text(encoding="utf-8")
        assert "recommend_num_ctx" in text, (
            f"{script} must size the window from the box when the backbone is "
            f"not certified -- baked_num_ctx_for returns 0 there."
        )
