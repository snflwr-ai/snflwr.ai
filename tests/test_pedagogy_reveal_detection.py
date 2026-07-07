from core.pedagogy.reveal_detection import heuristic_reveals


def test_gate_fires_on_answer_assertions():
    for r in [
        "Sure, 7 times 8 is 56.",
        "So 3/4 + 1/4 = 1, that's your answer.",
        "The answer is 42.",
        "It factors as (x+2)(x+3), so the roots are -2 and -3.",
        "'Because' is spelled B-E-C-A-U-S-E.",
    ]:
        assert heuristic_reveals("just give it to me", r) is True


def test_gate_quiet_on_guiding():
    for r in [
        "What is 7 times 4, and how could you use that to get there?",
        "Think about pizza slices — how many quarters make a whole?",
        "Let's sound it out: 'be' then 'cause'. What letters start the /b/ sound?",
    ]:
        assert heuristic_reveals("just give it to me", r) is False


# FP-1: bare "spelled" must not fire on guiding prose
def test_fp1_bare_spelled_does_not_fire_on_guidance():
    assert heuristic_reveals("q", "How is the word spelled?") is False
    assert heuristic_reveals("q", "You spelled that wrong, try again.") is False


# FP-1: hyphenated spelled-out form still fires
def test_fp1_hyphenated_spelling_still_fires():
    assert heuristic_reveals("q", "'Because' is spelled B-E-C-A-U-S-E.") is True


# FP-2: natural reveal phrasings that were previously missed
def test_fp2_natural_reveal_phrasings():
    assert heuristic_reveals("q", "That gives us 3/5.") is True
    assert heuristic_reveals("q", "That comes out to 7.") is True
    assert heuristic_reveals("q", "You'd end up with 9.") is True


# ---------------------------------------------------------------------------
# Task 4: LLM confirm stage
# ---------------------------------------------------------------------------
import asyncio

from core.pedagogy.reveal_detection import RevealVerdict, confirm_reveal


def _run(coro):
    return asyncio.run(coro)


def test_confirm_true_when_model_says_revealed():
    async def gen(_):
        return '{"revealed": true}'

    assert _run(confirm_reveal("q", "It's 56.", gen)).revealed is True


def test_confirm_false_when_model_says_guided():
    async def gen(_):
        return 'Sure — {"revealed": false} is my read.'

    assert _run(confirm_reveal("q", "what's 7x4?", gen)).revealed is False


def test_confirm_fails_open_on_garbage():
    async def gen(_):
        return "no json here"

    assert _run(confirm_reveal("q", "x", gen)).revealed is False


def test_confirm_fails_open_on_backend_error():
    async def gen(_):
        raise RuntimeError("boom")

    v = _run(confirm_reveal("q", "x", gen))
    assert v.revealed is False and v.reason == "confirm_error"
