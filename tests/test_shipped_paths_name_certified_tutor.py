"""Every shipped path must name a tutor the quality floor will actually serve.

2026-09-20: the only certified backbone is `snflwr.ai-31b`
(core/serving_plan.py CERTIFIED_BACKBONES), and it was named by NO installer,
deploy script, example env file or Modelfile -- only by tests, the vLLM compose
file and the docs. What shipped instead:

  * `.env.example` / `.env.production.example`: `OLLAMA_DEFAULT_MODEL=snflwr.ai`
    -- which the floor refuses. Measured on a 23 GB card: configured `snflwr.ai`
    gives `tier=unsupported tutoring=False`, while an EMPTY value gives
    `snflwr.ai-31b tier=certified`. The template was worse than silence.
  * `README.md`: `gemma4:e4b`, a base model, refused for the same reason.
  * `deploy.sh`: built a wrapper hardcoded to `snflwr.ai` from whatever the
    hardware ladder picked, and maintained `OLLAMA_MODEL` -- a variable NOTHING
    reads -- while never writing `OLLAMA_DEFAULT_MODEL`, the one the serving plan
    reads.

These tests are derived from the registry rather than hand-listing names, for
the same reason the hardware ladder was consolidated after five hardcoded copies
drifted apart: a guard that repeats the fact it is guarding drifts with it.
"""

import re
from pathlib import Path

import pytest

from core.serving_plan import CERTIFIED_BACKBONES

ROOT = Path(__file__).resolve().parents[1]

CERTIFIED_MODELS = {e.model for e in CERTIFIED_BACKBONES}
CERTIFIED_BASES = {e.base for e in CERTIFIED_BACKBONES}

# Files an operator or installer actually consumes. Docs are covered separately;
# these are the ones that decide what a box runs.
SHIPPED_ENV_TEMPLATES = (".env.example", ".env.production.example")


def _assignments(text: str, var: str) -> list:
    """Uncommented `VAR=value` assignments, value stripped of quotes/whitespace."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith(f"{var}="):
            continue
        value = stripped.split("=", 1)[1]
        # Strip a trailing shell/env comment: README documents the variable with
        # an explanatory `# the certified tutor` after it, and a guard that reads
        # that as part of the model name fails on a correct file.
        value = value.split(" #", 1)[0].strip().strip("'\"")
        # Skip compose-style interpolation and empty values: an empty value is
        # SAFE here -- the plan falls back to the best certified fit.
        if not value or value.startswith("$"):
            continue
        out.append(value)
    return out


def test_the_registry_is_readable_and_names_a_base():
    """Guard the guard: an empty registry would pass every check below."""
    assert CERTIFIED_BACKBONES, "no certified backbones — the registry is empty"
    for entry in CERTIFIED_BACKBONES:
        assert entry.base, f"{entry.model} does not record what it is built from"
        assert entry.model != entry.base, (
            f"{entry.model} claims to be built from itself; the wrapper and its "
            "base must differ or `ollama create` builds nothing"
        )


@pytest.mark.parametrize("name", SHIPPED_ENV_TEMPLATES)
def test_env_template_configures_a_certified_tutor(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    values = _assignments(path.read_text(), "OLLAMA_DEFAULT_MODEL")
    assert values, (
        f"{name} does not set OLLAMA_DEFAULT_MODEL at all. Empty is acceptable "
        "(the plan picks the best certified fit); a missing line is not, because "
        "this file is what an operator copies."
    )
    for value in values:
        assert value in CERTIFIED_MODELS, (
            f"{name} sets OLLAMA_DEFAULT_MODEL={value}, which has no sealed "
            f"tutoring run. The floor will disable tutoring. Certified: "
            f"{sorted(CERTIFIED_MODELS)}"
        )


def test_readme_does_not_document_an_uncertified_tutor():
    text = (ROOT / "README.md").read_text()
    for value in _assignments(text, "OLLAMA_DEFAULT_MODEL"):
        assert value in CERTIFIED_MODELS, (
            f"README documents OLLAMA_DEFAULT_MODEL={value}, which the quality "
            "floor refuses — an operator following it gets a box that cannot tutor"
        )


def test_deploy_builds_the_certified_wrapper_not_a_hardcoded_name():
    """deploy.sh must take the wrapper name from the registry."""
    text = (ROOT / "deploy.sh").read_text()
    assert "scripts/certified_tutor.py" in text, (
        "deploy.sh no longer asks the certified registry what to build; it will "
        "drift from the serving plan again"
    )
    wrapped = re.findall(r'^WRAPPED_MODEL=(.+)$', text, re.M)
    assert wrapped, "deploy.sh does not set WRAPPED_MODEL"
    for value in wrapped:
        assert "CERT_MODEL" in value, (
            f"deploy.sh hardcodes the tutor name as {value.strip()}; it must come "
            "from the registry (CERT_MODEL) so what is BUILT is what is SERVED"
        )


def test_deploy_writes_the_variable_the_plan_reads():
    """The plan reads OLLAMA_DEFAULT_MODEL; deploy.sh used to write only OLLAMA_MODEL."""
    text = (ROOT / "deploy.sh").read_text()
    assert "OLLAMA_DEFAULT_MODEL=${CERT_MODEL}" in text, (
        "deploy.sh does not write OLLAMA_DEFAULT_MODEL from the registry into the "
        "generated env file"
    )
    assert re.search(r'OLLAMA_DEFAULT_MODEL=\$\{CERT_MODEL\}" >> "\$ENV_FILE"', text) or (
        "Adding OLLAMA_DEFAULT_MODEL" in text
    ), "deploy.sh does not reconcile an EXISTING env file to the certified tutor"


def test_certified_tutor_helper_keeps_stdout_clean():
    """Shell captures its stdout; one stray log line makes the value garbage."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "certified_tutor.py"),
         "--vram-gb", "64", "--format", "model"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() in CERTIFIED_MODELS, (
        f"stdout was {out.stdout!r} — a shell caller would assign that to the "
        "model name"
    )


def test_certified_tutor_helper_refuses_hardware_that_cannot_tutor():
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "certified_tutor.py"),
         "--vram-gb", "2", "--format", "tsv"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert out.returncode == 3, (
        "a box too small for any certified backbone must exit 3 so deploy.sh can "
        f"skip the multi-GB pull; got {out.returncode}"
    )
    assert "cannot tutor" in out.stdout


def test_certified_bases_are_real_ladder_variants():
    """The base must be something the install path can actually pull."""
    from resource_detection import GEMMA4_VARIANTS

    known = {variant[0] for variant in GEMMA4_VARIANTS}
    for base in CERTIFIED_BASES:
        assert base in known, (
            f"certified base {base} is not in GEMMA4_VARIANTS {sorted(known)}; "
            "deploy.sh would try to pull a tag the ladder does not know"
        )
