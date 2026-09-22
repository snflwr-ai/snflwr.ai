"""The guarded upgrade must operate on the model production actually serves.

`scripts/guarded_upgrade.sh` hardcoded the wrapper name `snflwr.ai` in seven
places -- the build, the rollback snapshot, the restore, and the post-upgrade
smoke that checks the tutor still generates. Production serves `snflwr.ai-31b`
(`OLLAMA_DEFAULT_MODEL` in snflwr-api).

So `./deploy.sh --upgrade model` would:
  * snapshot a model nobody serves,
  * rebuild that model,
  * smoke-test that model,
  * roll back that model,
and report success -- while the tutor children actually talk to sat untouched.
A guarded path guarding a decoy. Found 2026-09-21 while deploying a tutor prompt
change, which is why that deploy had to be done by hand.

This is the same class as the GPU watchdog that restart-looped for five weeks
behind a green repo, and as the reveal confirm measured with `think: False`
while the student path ran with thinking on: a check that names the wrong
artifact passes for the wrong reason.

These tests guard the SHAPE of the fix. They cannot run the upgrade (that needs
a live stack), so they assert the properties that rot silently.
"""

import pathlib
import re

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "guarded_upgrade.sh"
)


@pytest.fixture(scope="module")
def src() -> str:
    return SCRIPT.read_text()


@pytest.fixture(scope="module")
def code(src: str) -> str:
    """Script with comment lines removed.

    The header explains the defect and therefore contains the very literal the
    tests forbid. Guards that grep source must read code.
    """
    return "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )


def test_no_ollama_command_names_a_hardcoded_wrapper_model(code: str):
    """The operative commands must take the resolved name, not a literal.

    Checks `ollama create|cp|run` invocations specifically -- those are the ones
    that built, snapshotted, restored and smoke-tested the decoy.
    """
    offenders = []
    for line in code.splitlines():
        if "ollama create" in line or "ollama cp" in line or "ollama run" in line:
            # the literal wrapper name, not as part of snflwr.ai-31b / a var
            if re.search(r"\bsnflwr\.ai(?![-\w:])", line):
                offenders.append(line.strip())
    assert not offenders, (
        "these commands hardcode the wrapper model instead of using the "
        "resolved served model:\n  " + "\n  ".join(offenders)
    )


def test_it_asks_the_RUNNING_api_which_model_is_served(code: str):
    """The API is the authority: the proxy pins every student request to
    OLLAMA_DEFAULT_MODEL. Reading the env file alone would describe a stopped
    stack, and this project has repeatedly found the file and the process
    disagreeing."""
    assert "printenv OLLAMA_DEFAULT_MODEL" in code, (
        "the script no longer asks the running API which model it serves"
    )


def test_an_unresolvable_model_ABORTS_rather_than_defaulting(code: str):
    """The defect was a silent no-op reported as success.

    If the resolved model does not exist in ollama, every docker command below
    would quietly do nothing. Falling back to a default here would recreate the
    bug with extra steps, so it has to be a hard failure.
    """
    assert "does not exist in snflwr-ollama" in code
    assert "Refusing to run" in code
    # and the resolution is checked at the top level, with an exit
    assert "resolve_served_model" in code
    assert re.search(r"if ! resolve_served_model", code), (
        "resolution failure must stop the run, not warn and continue"
    )


def test_resolution_happens_OUTSIDE_a_subshell(src: str):
    """`MODEL_BACKUP_TAG` is set during resolution.

    If resolution only ever ran as `$(served_model)`, that assignment would
    happen in a subshell and never reach the caller -- the snapshot would be
    written to `:preupgrade-bak` with an empty model name, and the rollback
    would restore nothing. That was a bug in the first draft of this fix.
    """
    assert re.search(r"^if ! resolve_served_model", src, re.M), (
        "resolve_served_model must be called directly at the top level"
    )
    body = src[src.index("resolve_served_model() {"):]
    body = body[: body.index("\n}")]
    assert "MODEL_BACKUP_TAG=" in body, "resolution must set the rollback tag"


def test_the_resolver_writes_ONLY_the_model_name_to_stdout(src: str):
    """It is consumed as `$(served_model)`.

    An `info` line inside it would be captured as part of the model name and
    every downstream docker command would take a mangled argument. Progress
    belongs on stderr; `err` already goes there.
    """
    body = src[src.index("served_model() {"):]
    body = body[: body.index("\n}")]
    code_lines = [
        ln for ln in body.splitlines() if not ln.strip().startswith("#")
    ]
    assert not any(
        re.match(r"\s*(info|warn)\s", ln) for ln in code_lines
    ), "served_model must not print progress to stdout; it is captured"


def test_the_rollback_tag_is_derived_from_the_served_model(code: str):
    """A fixed backup tag would snapshot the right model to a name the restore
    of a different deployment could collide with, and -- worse -- would keep
    working if the served model changed, hiding the mismatch again."""
    assert 'MODEL_BACKUP_TAG="${SERVED_MODEL}' in code, (
        "the rollback tag must be derived from the resolved served model"
    )


def test_the_model_rebuild_still_sets_num_gpu(code: str):
    """gemma4:e4b ships `PARAMETER num_gpu 0` and `FROM` inherits it.

    deploy.sh computes and appends num_gpu for exactly this reason. A guarded
    rebuild that skipped it would silently move the tutor to CPU -- ~20x slower,
    no error -- i.e. the rollback path would itself be the regression.
    """
    build = code[code.index("model_build()"):]
    build = build[: build.index("\n}")]
    assert "num_gpu" in build, (
        "model_build does not set num_gpu; a rebuild can silently land on CPU"
    )
    assert "recommend_num_gpu" in build, (
        "num_gpu must be COMPUTED from the hardware, never hardcoded -- forcing "
        "layers onto a card too small stops the runner starting"
    )
