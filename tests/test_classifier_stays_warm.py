"""The pedagogy classifiers must stay resident between turns.

The input gate runs on EVERY child turn. Its cold load measured **29.9s against
a 6s timeout**, while steady state is p50 0.43s. So one idle gap times the gate
out, and `enforce_guidance` silently falls back to the REGEX path -- which is
25 points weaker on framed demands:

                       LLM gate (e4b)   regex fallback
  dodge caught             18/20 = 90%      13/20 = 65%

(blind holdout, 2026-09-21, ~/snflwr-artefacts/2026-09-21-stuckchild-holdout/)

A "dodge" is a real demand for the assigned deliverable with a refusal or
learning frame bolted on -- "i wanna lern. wats the ansers on my paper". On the
fallback path 35% of them get the child's homework done for them.

When this was written the gate model was NOT resident, so the next child's turn
would have taken the weak path. The exposure was live, not theoretical.

Cheap because the classifier is CPU-pinned (`num_gpu 0`): it costs RAM, not the
contended card, so it cannot evict the tutor.
"""

import inspect
import re

import pytest


@pytest.fixture(scope="module")
def oneshot_src() -> str:
    from api.routes.ollama_proxy import chat

    return inspect.getsource(chat._pedagogy_oneshot)


def test_the_classifier_call_sets_keep_alive(oneshot_src):
    assert "keep_alive" in oneshot_src, (
        "the pedagogy classifier no longer stays warm; a cold load (29.9s vs a "
        "6s timeout) drops the turn onto the 65%-dodge regex fallback"
    )


def test_keep_alive_is_a_named_constant_not_a_literal(oneshot_src):
    """So the value is findable and changeable in one place, and carries its
    reasoning rather than sitting inline as a bare string."""
    from api.routes.ollama_proxy import chat

    assert hasattr(chat, "_CLASSIFIER_KEEP_ALIVE")
    assert "_CLASSIFIER_KEEP_ALIVE" in oneshot_src


def test_it_is_not_FOREVER(oneshot_src):
    """`keep_alive: forever` is how the co-tenant on this box ended up pinning
    17 GB of card indefinitely and starving the tutor. A bounded hold gets the
    benefit without the failure mode."""
    from api.routes.ollama_proxy import chat

    v = str(chat._CLASSIFIER_KEEP_ALIVE).strip().lower()
    assert v not in {"forever", "-1", "-1s"}, (
        "an unbounded keep_alive on a classifier is how this box already "
        "starved its own tutor once"
    )
    assert re.fullmatch(r"\d+(s|m|h)", v), f"unexpected keep_alive format: {v!r}"


def test_the_classifier_is_still_CPU_pinned_so_warmth_is_cheap(oneshot_src):
    """The whole cost argument depends on this.

    Holding a GPU-resident checker warm would compete with the 19 GB tutor on a
    23 GB card -- a much worse trade than the cold load it prevents. The pin is
    what makes keep_alive free, so the two belong in one test.
    """
    from core.pedagogy import classifier_options

    gate = classifier_options("gemma4:e4b", "snflwr.ai-31b")
    assert gate.get("num_gpu") == 0, (
        "the gate is no longer CPU-pinned; keeping it warm now costs VRAM and "
        "can evict the tutor"
    )
    # ...and the recursive confirm on the tutor must still NOT be pinned
    confirm = classifier_options("snflwr.ai-31b", "snflwr.ai-31b")
    assert "num_gpu" not in confirm
