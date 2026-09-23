"""A shipped install path must produce a box that actually tutors.

Two defects from the 2026-09-19 architecture audit, both fixed 2026-09-23 and
pinned here:

1. `start_snflwr.sh` asked the certified registry for the tutor name, stored it
   in TUTOR_MODEL -- and then built and exported a hardcoded `snflwr.ai`. No
   registry entry matches that name, so `core/serving_plan.py` returned
   tier=unsupported tutoring=False and a freshly installed box served no
   tutoring at all. The one working host had been set up by hand.

2. `api/routes/chat.py` decided whether to inject a system message by comparing
   `model_name == "snflwr.ai"`, which is False for the served `snflwr.ai-31b`.
   Ollama treats an injected system message as a REPLACEMENT for the Modelfile
   SYSTEM, so the tuned persona -- safety protocols included -- was discarded on
   the one model it was written for.

Both are the same shape: a hardcoded model name drifting from the registry that
owns it. Same family as the guarded-upgrade fix (#300).
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = (ROOT / "start_snflwr.sh").read_text()


def _certified_models() -> set:
    """Model names the serving plan will actually tutor with."""
    from core.serving_plan import CERTIFIED_BACKBONES

    return {b.model for b in CERTIFIED_BACKBONES}


def test_installer_exports_the_name_it_asked_the_registry_for():
    """OLLAMA_DEFAULT_MODEL must be TUTOR_MODEL, never a literal."""
    exports = re.findall(r'^export OLLAMA_DEFAULT_MODEL=(.+)$', INSTALLER, re.M)
    assert exports, "installer no longer exports OLLAMA_DEFAULT_MODEL"
    assert '"$TUTOR_MODEL"' in exports, (
        "the primary export must use the registry-derived $TUTOR_MODEL; "
        f"found {exports}"
    )
    assert '"snflwr.ai"' not in exports, (
        "a hardcoded wrapper name is what disabled tutoring on every fresh box"
    )


def test_installer_builds_the_wrapper_under_that_same_name():
    """Building `snflwr.ai` while exporting `snflwr.ai-31b` serves nothing."""
    assert 'ollama create "$TUTOR_MODEL"' in INSTALLER, (
        "the wrapper must be built under the name that gets exported"
    )
    assert not re.search(r"^\s*ollama create snflwr\.ai\b", INSTALLER, re.M)


def test_installer_carries_the_certified_context_through():
    """The sealed grade belongs to an (engine, model, num_ctx) triple."""
    assert "TUTOR_NUM_CTX" in INSTALLER, "registry num_ctx (field 3) is dropped"
    assert "INFERENCE_NUM_CTX" in INSTALLER


def test_the_registry_answer_is_a_model_the_plan_will_tutor_with():
    """End to end: what the installer would export must pass the floor."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "scripts/certified_tutor.py", "--vram-gb", "23",
         "--format", "tsv"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr[-300:]
    model, base, num_ctx = out.stdout.strip().split("\t")[:3]
    assert model in _certified_models(), (
        f"installer would export {model!r}, which the serving plan does not "
        f"certify (it knows {sorted(_certified_models())})"
    )
    assert int(num_ctx) > 0


@pytest.mark.parametrize("model", sorted(_certified_models()) + ["snflwr.ai"])
def test_a_baked_system_model_keeps_its_persona(model):
    """Every tutor wrapper must be recognised as carrying a baked SYSTEM.

    A miss here does not fail loudly -- it silently replaces the tutor's system
    prompt, including its safety rules, with a generic K-12 framing.
    """
    from api.routes.chat import _BAKED_SYSTEM_MODELS

    assert model in _BAKED_SYSTEM_MODELS


def test_a_base_model_is_not_treated_as_having_a_baked_system():
    """The set must not be so broad that plain base models skip the framing."""
    from api.routes.chat import _BAKED_SYSTEM_MODELS

    for model in ("gemma4:31b", "gemma4:e4b", "llama-guard3:8b", ""):
        assert model not in _BAKED_SYSTEM_MODELS
