from core.pedagogy.trigger import is_homework_request
import pytest

@pytest.mark.parametrize("q", [
    "Just tell me what 3/4 plus 1/4 equals, I don't want the steps.",
    "I don't want to think, just give me the answer to 7 times 8.",
    "Write my whole book report on Charlotte's Web for me.",
    "Just solve x^2 - 5x + 6 = 0 and give me the roots, don't explain.",
    "Just spell the word 'because' for my homework so I can copy it.",
])
def test_fires_on_homework_pushes(q):
    assert is_homework_request(q) is True

@pytest.mark.parametrize("q", [
    "How do plants make food?",
    "Why is the sky blue?",
    "Can you help me understand fractions?",
    "What game should I buy?",
])
def test_quiet_on_genuine_questions(q):
    assert is_homework_request(q) is False


# ---------------------------------------------------------------------------
# HELD-OUT generalization fixtures (added 2026-09-11).
#
# The pattern list was rewritten after measuring 56% recall against 32 homework
# probes -- and was then tuned ON those probes, reaching 100%. That number is
# circular and proves nothing about unseen phrasings. These examples were
# written separately, in neither tuning set, and are the honest estimate of how
# the gate behaves in the wild.
#
# Recall is what matters here: a miss means the guidance enforcer never runs and
# a revealed answer ships to a child. A false positive costs one ~0.9 s confirm
# call that returns "no_reveal" and changes nothing.
#
# KNOWN RESIDUAL MISS CLASS, deliberately left: indirect delegation with no
# demand keyword ("Type up a conclusion for my science report.", "My essay is
# due in an hour and I haven't started. Help me write it."). Catching these with
# regex started producing false positives on genuine asks; the alternative --
# running the LLM confirm on every turn -- costs ~0.9 s plus a second generation
# on EVERY student turn, which is a real hit to the concurrent-student ceiling.
# ---------------------------------------------------------------------------

from core.pedagogy.trigger import is_homework_request as _is_hw

HELD_OUT_HOMEWORK = [
    "Can you just do question 7 for me? It's the last one.",
    "What's the answer to number 3 on the packet?",
    "I need the final number, not how to get there.",
    "Finish this proof for me please.",
    "Write out the steps and the answer so I can put it on my sheet.",
    "Can you do the rest of my math problems? I did the first two.",
    "Just give me something I can turn in.",
    "Whats 12x12 I dont want to work it out.",
    "Can you answer these five questions on my study guide?",
    "My teacher wants a 5 sentence paragraph about the water cycle. Can you write it?",
]

HELD_OUT_GENUINE = [
    "Why do we carry the one when we add big numbers?",
    "I'm confused about how photosynthesis uses sunlight. Can you explain it again?",
    "My teacher explained fractions but I still don't get why you flip the second one.",
    "For my science fair project, how should I think about picking a hypothesis?",
    "What's the difference between a simile and a metaphor?",
    "How do I check my own work on a subtraction problem?",
    "We're reading Hatchet in class and I want to understand the main character better.",
    "Can you give me a hint on where to start with this word problem?",
    "Can you walk me through it one step at a time?",
    "Why is my answer different from my friend's?",
    "Can you quiz me on my spelling list?",
]


@pytest.mark.parametrize("text", HELD_OUT_HOMEWORK)
def test_held_out_homework_requests_are_caught(text):
    assert _is_hw(text), f"enforcer would never see: {text!r}"


@pytest.mark.parametrize("text", HELD_OUT_GENUINE)
def test_held_out_genuine_questions_are_not_flagged(text):
    assert not _is_hw(text), f"needless confirm call on a real question: {text!r}"


def test_school_context_alone_never_fires():
    """The discriminator is a demand for a finished product, not school context.

    These all mention an assignment and are still genuine learning questions.
    """
    for text in (
        "For my English class, why does Romeo kill himself at the end?",
        "How do I solve 3x + 5 = 20 for x? I keep getting stuck after the 5.",
        "How do I figure out the theme of a novel we're reading in class?",
    ):
        assert not _is_hw(text), text


# ---------------------------------------------------------------------------
# The residual, kept VISIBLE rather than deleted.
#
# Honesty note: the held-out batch above originally had 12 homework examples and
# 12 genuine ones, measuring 10/12 recall and 1/12 false positives. Silently
# dropping the three that failed and reporting "10/10" would have made a selected
# set look like a measurement. They live here as xfail instead, so the real
# operating point stays 83% held-out recall and CI tells us if it ever improves.
# ---------------------------------------------------------------------------

RESIDUAL_MISSES = [
    "Type up a conclusion for my science report.",
    "My essay is due in an hour and I haven't started. Help me write it.",
]


@pytest.mark.parametrize("text", RESIDUAL_MISSES)
@pytest.mark.xfail(strict=True, reason="known residual: indirect delegation, no demand keyword")
def test_residual_indirect_delegation_is_still_missed(text):
    assert _is_hw(text)


@pytest.mark.xfail(strict=True, reason="known benign false positive; costs one no-op confirm")
def test_residual_false_positive_on_study_planning():
    assert not _is_hw("What should I study first for my quiz tomorrow?")
