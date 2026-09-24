"""A contended GPU must not look like broken code.

`deploy.sh` turns any failure from `postdeploy_smoke.py` into:

    "The running container does NOT have the expected safety behaviour.
     It is healthy but serving the wrong code. Roll back with: ..."

On 2026-09-24 that fired on a deploy whose container was **correct**. The
co-tenant held the GPU, the arbiter's free-VRAM gate was 943 MiB optimistic,
ollama returned "cudaMalloc failed: out of memory" at 17:20:18, the reveal
confirm raised, and the deploy instructed the operator to roll back a
child-safety fix. Re-running the same binary under an exclusive lease: exit 0,
all six behaviours verified. Nothing about the code differed.

Telling someone to roll back working child-safety code is a worse outcome than
the contention that triggered it.

⚠️ THE DEFAULT MUST STAY FAIL. This check exists because a healthy container
once served the OLD homework gate while every artefact said otherwise, and
because a downgraded confirm sat at 0/20 recall in production behind a green
deploy. Downgrading an UNRECOGNISED error would trade a false alarm for a false
green, which is the worse of the two. Only known load/capacity signatures are
treated as UNMEASURED.
"""

import pathlib

import pytest

_SMOKE = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "postdeploy_smoke.py"


def _source() -> str:
    return _SMOKE.read_text(encoding="utf-8")


# Real strings from ollama and httpx when the model cannot be loaded or reached.
CONTENTION = [
    ("cuda_oom", "cudaMalloc failed: out of memory"),
    ("alloc_buffer", "unable to allocate CUDA0 buffer of size 18654279936"),
    ("alloc_tensor", "alloc_tensor_range: failed to allocate CUDA0 buffer"),
    ("sys_memory", "model requires more system memory than is available"),
    ("refused", "Connection refused"),
    ("read_timeout", "Read timed out"),
]

# Must still fail the deploy: these are what the check is FOR.
REAL_FAILURES = [
    ("wrong_persona", "confirm model returned nothing"),
    ("import_broken", "cannot import name 'confirm_reveal'"),
    ("bad_verdict", "unexpected verdict shape"),
    ("attr_error", "'NoneType' object has no attribute 'revealed'"),
]


def _classify(message: str) -> str:
    """Mirror the smoke's classification on an exception's text.

    Reads the tuple out of the source so the test cannot drift from the code:
    a signature added there is covered here without editing this file.
    """
    src = _source()
    start = src.index("contention = (")
    end = src.index(")", start)
    sigs = [
        line.strip().strip(",").strip('"').strip("'")
        for line in src[start:end].splitlines()[1:]
        if line.strip().strip(",").strip('"').strip("'")
    ]
    assert sigs, "could not parse the contention signature list from the smoke"
    text = f"RuntimeError: {message}".lower()
    return "UNMEASURED" if any(s in text for s in sigs) else "FAIL"


@pytest.mark.parametrize("name,message", CONTENTION, ids=[n for n, _ in CONTENTION])
def test_a_model_that_cannot_load_is_unmeasured_not_a_rollback(name, message):
    """The co-tenant holding the card is not a verdict on the shipped code."""
    assert _classify(message) == "UNMEASURED", (
        f"{name}: a load/capacity failure would tell the operator to ROLL BACK "
        f"working code. Message: {message!r}"
    )


@pytest.mark.parametrize(
    "name,message", REAL_FAILURES, ids=[n for n, _ in REAL_FAILURES]
)
def test_an_unrecognised_error_still_fails_the_deploy(name, message):
    """The default stays FAIL.

    A healthy container once served the old homework gate, and a downgraded
    confirm sat at 0/20 recall behind a green deploy. Trading this check's
    strictness for quieter deploys would restore exactly that.
    """
    assert _classify(message) == "FAIL", (
        f"{name}: an unrecognised error was downgraded to UNMEASURED. That "
        f"turns a false alarm into a false green, which is the worse failure."
    )


def test_the_unmeasured_path_says_it_is_not_a_rollback_reason():
    """The message has to tell the operator what to do, not just what happened.

    The rollback instruction is printed by deploy.sh, not here, so this branch
    must say plainly that it is not a verdict -- otherwise the operator reads
    UNMEASURED and still reaches for the rollback command.
    """
    src = _source()
    assert "NOT a reason to roll back" in src
    assert "take" in src and "lease" in src, (
        "the UNMEASURED message should name the fix (take a GPU lease and "
        "re-run), not merely report that something failed"
    )
