from core.pedagogy.guidance_enforcer import EnforceMeta, enforce_guidance  # noqa: F401
from core.pedagogy.trigger import is_homework_request  # noqa: F401

# Options for the pedagogy CLASSIFIER calls (input gate + reveal confirm).
#
# Exported so the deploy-time self-test can send EXACTLY what production sends.
# The first version of that self-test reconstructed the options by hand, omitted
# the CPU pin, and therefore exercised a path production does not take -- it
# failed while production was fine, which is the same class of error as a guard
# that passes while production is broken.
#
#   temperature 0 : a classifier must not flip between runs.
#   num_gpu 0     : CPU-pinned so a co-tenant model cannot evict it. Measured
#                   0.8 s warm, and CORRECT where the GPU-preferring path
#                   returned a wrong verdict after a failed load.
PEDAGOGY_CLASSIFIER_OPTIONS = {"temperature": 0, "num_gpu": 0}


def classifier_options(model: str, tutor_model: str | None = None) -> dict:
    """Classifier options for ``model``, WITHOUT the CPU pin when it is the tutor.

    The pin above is right for a small dedicated checker: it is cheap on CPU and
    a co-tenant cannot evict it off the card. It is catastrophically wrong when
    the checker IS the resident tutor, which is what the recursive confirm makes
    it -- a 31b forced onto CPU, where this stack has already measured the same
    inherited pin quietly running a benchmark arm at 10.5 tok/s with 21 GB of
    card sitting free, and the tutor itself answering in ~13.6 s.

    The recursive confirm's whole economic case is that the model is ALREADY on
    the card, so the check costs no load: p50 1.6 s / p90 4.4 s, measured with no
    pin. Sending `num_gpu 0` with it would have kept the verdicts and thrown away
    the reason for them -- silently, since a slow confirm still returns a correct
    answer and nothing in the response says which device produced it.

    Passing ``tutor_model`` explicitly keeps this decidable at the call site; a
    caller that does not know the tutor gets the pinned options, which is the
    safe default for every dedicated-checker deployment.
    """
    opts = dict(PEDAGOGY_CLASSIFIER_OPTIONS)
    if tutor_model and model and model.strip() == tutor_model.strip():
        opts.pop("num_gpu", None)
    return opts
