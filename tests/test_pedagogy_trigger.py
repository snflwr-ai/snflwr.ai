from core.pedagogy.trigger import is_homework_request
import pytest


@pytest.mark.parametrize(
    "q",
    [
        "Just tell me what 3/4 plus 1/4 equals, I don't want the steps.",
        "I don't want to think, just give me the answer to 7 times 8.",
        "Write my whole book report on Charlotte's Web for me.",
        "Just solve x^2 - 5x + 6 = 0 and give me the roots, don't explain.",
        "Just spell the word 'because' for my homework so I can copy it.",
    ],
)
def test_fires_on_homework_pushes(q):
    assert is_homework_request(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "How do plants make food?",
        "Why is the sky blue?",
        "Can you help me understand fractions?",
        "What game should I buy?",
    ],
)
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
    # Was a strict-xfail residual until the normalization pass caught it.
    "My essay is due in an hour and I haven't started. Help me write it.",
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
# set look like a measurement. They live here as xfail instead, so CI tells us if
# the operating point ever improves.
#
# CORRECTION 2026-09-12: that 83% was not the operating point. Both this set and
# the tuning set were written by someone who had just read the patterns. Measured
# against 76 probes whose phrasings came from a different model family given only
# a task description, the SAME code scored 47.2% (25/53) -- and 1 of 10 on K-2
# phrasings, where child spelling ("anser", "problm") reached nothing. A
# held-out set is only held out from the CODE; it was never held out from the
# AUTHOR. See the normalization layer in trigger.py for what that missed.
# ---------------------------------------------------------------------------

RESIDUAL_MISSES = [
    "Type up a conclusion for my science report.",
]


@pytest.mark.parametrize("text", RESIDUAL_MISSES)
@pytest.mark.xfail(
    strict=True, reason="known residual: indirect delegation, no demand keyword"
)
def test_residual_indirect_delegation_is_still_missed(text):
    assert _is_hw(text)


@pytest.mark.xfail(
    strict=True, reason="known benign false positive; costs one no-op confirm"
)
def test_residual_false_positive_on_study_planning():
    assert not _is_hw("What should I study first for my quiz tomorrow?")


# ---------------------------------------------------------------------------
# Register normalization (added 2026-09-12).
#
# The gate scored 47.2% against probes whose phrasings came from a different
# model family, and 1 of 10 on kindergarten-through-grade-2 register. Every miss
# was obvious to a human reader; they failed because a six-year-old writes
# "anser" and a teenager writes "can u". These tests pin the CLASSES, not the
# individual phrasings that exposed them.
# ---------------------------------------------------------------------------

CHILD_SPELLING_DEMANDS = [
    "can u tell me the anser to the 3rd math problm on my sheet",
    "tell me the numbr that goes in the blank for the addtion problem",
    "what is the cirt ansr to the sciense qstn about the sun",
    "can u say the word i am supose to wite for homwork",
    "i cant find the ansr for the blue book page 10 qstn 1",
    "i dont know the word for my writng hw pls give it to me",
]


@pytest.mark.parametrize("text", CHILD_SPELLING_DEMANDS)
def test_child_spelling_and_texting_register_is_caught(text):
    assert _is_hw(text), text


def test_normalization_does_not_corrupt_real_words():
    """The fuzzy repair must never rewrite a correctly spelled English word.

    "work" sits within the match threshold of "word" and "these" of "thesis";
    left unguarded, the repair silently destroyed the very phrases the patterns
    were written against.
    """
    from core.pedagogy.trigger import normalize

    for word in ("work", "these", "base", "core", "became", "assigned"):
        assert word in normalize(f"i want to {word} it out"), word
    # ...while still repairing what a child actually types.
    assert "answer" in normalize("whats the anser")
    assert "you" in normalize("can u help")


STATED_LEARNING_INTENT = [
    "My teacher assigned us to write a paragraph about my family, but I don't "
    "understand why we conjugate 'comer' into 'comi'.",
    "I am doing a biology worksheet on photosynthesis and I am stuck on the "
    "Calvin Cycle. I want to understand the step-by-step logic.",
    "Can you give me a hint on where to start with this word problem?",
    "Can you quiz me on my spelling list?",
]


@pytest.mark.parametrize("text", STATED_LEARNING_INTENT)
def test_assignment_vocabulary_with_learning_intent_is_not_flagged(text):
    """Describing an assignment is not demanding it.

    A student saying what they were told to write uses the same words as one
    telling the tutor to write it; a stated intent to understand separates them.
    """
    assert not _is_hw(text), text


# ---------------------------------------------------------------------------
# Work stated in full (added 2026-09-12).
#
# On probes where the student CAN be answered -- they write out the equation,
# they name the book -- recall was 71.9%, and every miss was the same shape: an
# assigned question typed out verbatim in neutral academic language, carrying no
# demand marker and no assignment vocabulary. Those misses produced 4 of the 5
# reveals that reached a student.
# ---------------------------------------------------------------------------

STATED_WORK_DEMANDS = [
    "What is the perimeter of a rectangle with a length of 12 inches and a width of 5 inches?",
    "Calculate the area of a circle with a radius of 7 cm. Use 3.14 for pi.",
    "What is the derivative of f(x) = 3x^4 - 2x^3 + 5x - 7?",
    "I need help calculating the force. Here is the problem: A 15 kg object accelerates "
    "at 3 m/s^2. What is the net force acting on the object in Newtons?",
    "I need a detailed explanation of this biology concept: 'Describe the process of photosynthesis. Include the inputs, outputs, and the specific organelle where it takes place in plant cells.' Please provide a concise but complete answer suitable for a 10th grade biology exam.",
]


@pytest.mark.parametrize("text", STATED_WORK_DEMANDS)
def test_work_stated_in_full_plus_a_result_request_fires(text):
    assert _is_hw(text), text


OWN_ATTEMPT_SHOWN = [
    "I tried to find the derivative of x^3 using the power rule. I dropped the exponent "
    "and subtracted 1, so I got 3x^2. Is that the correct way to apply the rule?",
    "I'm stuck on this fraction problem: 1/2 + 1/3. I added the tops to get 2 and the "
    "bottoms to get 5, so I got 2/5. Why do I need a common denominator?",
    "I balanced this chemistry equation: 2H2 + O2 -> 2H2O. I think I did it right "
    "because I counted the atoms on both sides.",
    "I solved this word problem: A train travels 60 miles in 2 hours. How far does it go "
    "in 5 hours? I divided 60 by 2 to get 30, then multiplied by 5. Is my logic sound?",
]


@pytest.mark.parametrize("text", OWN_ATTEMPT_SHOWN)
def test_student_who_shows_their_attempt_is_not_flagged(text):
    """Stating a problem and showing your working is asking to be CHECKED.

    Every false positive the stated-work rule introduced was this shape. The
    student already did the work; there is nothing left to hand over.
    """
    assert not _is_hw(text), text


def test_method_question_about_a_stated_problem_is_not_flagged():
    """ "How do I solve 3x + 5 = 20?" asks for the method.

    Contrast "What is the perimeter of a rectangle with length 12 and width 5?",
    which asks for the product. That distinction is what the rule turns on, and
    getting it backwards fires on every genuine learner who shows their problem.
    """
    assert not _is_hw(
        "How do I solve 3x + 5 = 20 for x? I keep getting stuck after the 5."
    )
    assert not _is_hw(
        "How would I approach 2y + 5 = 17? I don't want the answer, just the steps."
    )
