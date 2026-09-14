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
