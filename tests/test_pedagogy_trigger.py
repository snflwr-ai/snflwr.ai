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
