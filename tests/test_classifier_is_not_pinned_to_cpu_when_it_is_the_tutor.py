"""The CPU pin is right for a dedicated checker and wrong for the resident tutor.

`PEDAGOGY_CLASSIFIER_OPTIONS` carries `num_gpu: 0`, measured and correct for a
small checker: on a card holding one big model the GPU-preferring path lost the
race against the co-tenant and returned a WRONG verdict after a failed load,
while the CPU pin answered correctly in 0.8 s warm.

The recursive confirm inverts that. It runs the check on the tutor's own weights
precisely because they are ALREADY on the card, which is where p50 1.6 s / p90
4.4 s comes from -- measured with no pin. Sending `num_gpu 0` with it would keep
the verdicts and throw away the only reason to use them, and it would do so in
SILENCE: a slow confirm still returns a correct verdict, and nothing in the
response says which device produced it.

This stack has already paid for that lesson once. A `FROM`-based eval arm
inherited a base model's `num_gpu 0` and benchmarked a whole backbone on CPU at
10.5 tok/s with 21 GB of card free, against a competitor running on the GPU.
"""

from core.pedagogy import PEDAGOGY_CLASSIFIER_OPTIONS, classifier_options

TUTOR = "snflwr.ai-31b"


def test_a_dedicated_checker_keeps_the_cpu_pin():
    opts = classifier_options("gemma4:e4b", TUTOR)
    assert opts["num_gpu"] == 0
    assert opts["temperature"] == 0


def test_the_resident_tutor_is_not_pinned_to_cpu():
    opts = classifier_options(TUTOR, TUTOR)
    assert "num_gpu" not in opts, (
        "the confirm would run the 31b tutor on CPU while its own copy sits on "
        "the card -- this is the inherited-pin failure, in production"
    )
    assert opts["temperature"] == 0, "a safety classifier must still be deterministic"


def test_whitespace_does_not_defeat_the_comparison():
    assert "num_gpu" not in classifier_options(f" {TUTOR} ", TUTOR)


def test_an_unknown_tutor_gets_the_safe_pinned_default():
    """A caller that cannot say what the tutor is must not silently unpin.

    Pinned is the safe default: it costs a little latency for a small checker and
    protects it from eviction. Unpinned-by-default would hand every dedicated
    checker the eviction race it was pinned to avoid.
    """
    assert classifier_options("gemma4:e4b", None)["num_gpu"] == 0
    assert classifier_options("gemma4:e4b")["num_gpu"] == 0


def test_the_shared_default_is_not_mutated():
    """The options dict is exported so the deploy self-test can send exactly what
    production sends. A caller that popped a key off the shared dict would
    silently unpin every later call."""
    before = dict(PEDAGOGY_CLASSIFIER_OPTIONS)
    classifier_options(TUTOR, TUTOR)
    assert PEDAGOGY_CLASSIFIER_OPTIONS == before


def test_the_chat_route_passes_the_tutor_model_with_the_persona_override():
    """Both consequences hang off the same question -- "is the confirm the
    tutor?" -- so they must be passed together. The persona override without the
    unpin gives a correct verdict at 4x the latency; the unpin without the
    persona override gives 0/27 unparseable verdicts.
    """
    import inspect

    from api.routes.ollama_proxy import chat

    src = inspect.getsource(chat)
    # The confirm call site must hand both the system override and the tutor
    # model through; grep the shape rather than the exact formatting.
    assert "tutor_model=model" in src, (
        "the confirm call site no longer tells _pedagogy_oneshot what the tutor "
        "is, so classifier_options cannot drop the pin"
    )
    assert "classifier_options(model, tutor_model)" in src, (
        "the options are no longer derived from the model being called"
    )
