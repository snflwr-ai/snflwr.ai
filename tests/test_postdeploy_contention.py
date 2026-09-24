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
    # NOTE: "connection refused" and timeouts are NOT here. They are ambiguous
    # -- a confirm pointed at the wrong host produces them -- so they live in
    # AMBIGUOUS below and require GPU corroboration.
]

# Must still fail the deploy: these are what the check is FOR.
REAL_FAILURES = [
    ("wrong_persona", "confirm model returned nothing"),
    ("import_broken", "cannot import name 'confirm_reveal'"),
    ("bad_verdict", "unexpected verdict shape"),
    ("attr_error", "'NoneType' object has no attribute 'revealed'"),
]


def _smoke():
    """Import the smoke module so the tests use the REAL classifier.

    The first version parsed the signature tuple out of the source text. A peer
    review called that brittle and was right: a test that reimplements the thing
    it checks can agree with itself while the code does something else.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("pds", _SMOKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _classify(message: str, corroborates: str = "") -> str:
    mod = _smoke()
    mod._gpu_corroborates_contention = lambda: corroborates
    verdict, _why = mod.classify_confirm_failure(RuntimeError(message))
    return verdict


@pytest.mark.parametrize("name,message", CONTENTION, ids=[n for n, _ in CONTENTION])
def test_a_model_that_cannot_load_is_unmeasured_not_a_rollback(name, message):
    """A capacity failure is unambiguous: nothing a shipped bug does looks like it."""
    assert _classify(message) == "UNMEASURED", (
        f"{name}: a load/capacity failure would tell the operator to ROLL BACK "
        f"working code. Message: {message!r}"
    )


@pytest.mark.parametrize(
    "name,message", REAL_FAILURES, ids=[n for n, _ in REAL_FAILURES]
)
def test_an_unrecognised_error_still_fails_the_deploy(name, message):
    """The default stays FAIL."""
    assert _classify(message) == "FAIL", (
        f"{name}: an unrecognised error was downgraded to UNMEASURED. That "
        f"turns a false alarm into a false green, which is the worse failure."
    )


# ⚠️ The review's sharpest point. A confirm pointed at the wrong OLLAMA host, a
# renamed service or a dead port produces exactly these strings -- and that is
# the 0/20-recall-behind-a-green-deploy class this check exists for. The first
# version downgraded them unconditionally and would have hidden a shipped
# misconfiguration.
AMBIGUOUS = [
    ("refused", "Connection refused"),
    ("conn_error", "Connection error"),
    ("read_timeout", "Read timed out"),
    ("timeout", "operation timeout"),
]


@pytest.mark.parametrize("name,message", AMBIGUOUS, ids=[n for n, _ in AMBIGUOUS])
def test_connection_errors_FAIL_without_gpu_corroboration(name, message):
    """No GPU evidence -> treat as a shipped misconfiguration, not as load."""
    assert _classify(message, corroborates="") == "FAIL", (
        f"{name}: a connection/timeout error was excused with NO evidence the "
        f"card was busy. A confirm pointed at the wrong host looks exactly like "
        f"this, and that is the defect this script was written for."
    )


@pytest.mark.parametrize("name,message", AMBIGUOUS, ids=[n for n, _ in AMBIGUOUS])
def test_connection_errors_are_unmeasured_when_the_gpu_corroborates(name, message):
    """With independent evidence the card is busy, it is contention after all."""
    assert _classify(message, corroborates="the co-tenant holds the card") == "UNMEASURED"


def test_unmeasured_is_not_counted_as_a_pass():
    """Exit 3, not 0. The check DID NOT RUN; 'verified' would be a false green.

    deploy.sh reads exit 0 as "Shipped behaviour verified." The first version of
    this fix returned [] on UNMEASURED, so a deploy whose safety check never ran
    printed success -- exactly the failure the fix was meant to remove.
    """
    mod = _smoke()
    assert mod._UNMEASURED, "no UNMEASURED sentinel"
    src = _source()
    assert "return 3" in src, "UNMEASURED must exit 3, distinct from 0 and 1"
    deploy = (_SMOKE.parent.parent / "deploy.sh").read_text(encoding="utf-8")
    assert "SMOKE_RC" in deploy and "-eq 3" in deploy, (
        "deploy.sh must handle exit 3 distinctly, or the operator sees either "
        "'verified' or 'roll back' for a check that did not run"
    )
    assert "NOT confirmed safe" in deploy


def test_the_classifier_reason_names_what_actually_happened():
    """Assert on BEHAVIOUR, not on source text.

    The first draft grepped the smoke for "NOT a reason to roll back" and
    failed, because the phrase is split across two string literals. That is the
    same brittleness the review flagged: a test that reads source can fail on
    formatting and pass on a rewritten meaning.
    """
    mod = _smoke()
    mod._gpu_corroborates_contention = lambda: ""

    _v, why = mod.classify_confirm_failure(RuntimeError("cudaMalloc failed: out of memory"))
    assert "could not load" in why

    v, why = mod.classify_confirm_failure(RuntimeError("Connection refused"))
    assert v == "FAIL"
    assert "misconfiguration" in why, (
        "an uncorroborated connection error must SAY it is probably a shipped "
        "misconfiguration, so the operator does not go hunting the GPU"
    )

    mod._gpu_corroborates_contention = lambda: "the co-tenant holds the card"
    v, why = mod.classify_confirm_failure(RuntimeError("Connection refused"))
    assert v == "UNMEASURED" and "co-tenant" in why


def test_the_operator_is_told_what_to_do():
    """deploy.sh prints the remedy -- it is one line there, so this is safe."""
    deploy = (_SMOKE.parent.parent / "deploy.sh").read_text(encoding="utf-8")
    assert "Take a GPU lease" in deploy
    assert "postdeploy_smoke.py" in deploy
