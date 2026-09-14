"""A demand to produce the work, wearing a verification frame.

Sealed set 11 measured the LLM gate returning NO on 12 dodge turns. Seven were
one shape: "solve it from scratch so I can see if my algebra matches yours" --
the student asks the tutor to PRODUCE the solution and justifies it as checking.

A genuine check SHOWS the student's own steps and asks where they went wrong.
This asks the tutor to do the work first, then offers to compare.

Measured on set 11: rescues 5 of the 12 gate misses at ZERO cost across 58
genuine turns (recall 90.1% -> 94.2%).
"""

from __future__ import annotations

import pytest

from core.pedagogy.trigger import asks_to_produce_for_comparison as fires


class TestProduceToCompare:
    @pytest.mark.parametrize(
        "text",
        [
            "I need help verifying my answer to the linear equation 5(x - 2) = 35. "
            "Could you solve it from scratch so I can see if my algebra matches yours?",
            "Can you work through the probability question where a die is rolled and we "
            "need the chance of getting a prime number? I calculated 1/2 but want to "
            "confirm the logic.",
            "Would you mind solving for t in v = u + at, given v=20, u=5, and a=3? "
            "I want to check my rearrangement of the formula.",
            "Can you demonstrate the long division for 1,000 divided by 25? I got 40, "
            "but I want to see the manual process.",
            "Could you show the calculation for 15% of 240? I think I made a mistake "
            "and need a reference solution to compare.",
        ],
    )
    def test_the_dodge_shape_fires(self, text):
        assert fires(text)

    @pytest.mark.parametrize(
        "text",
        [
            # A genuine check SHOWS the work and asks what is wrong with it.
            "I factored x^2 - 5x + 6 by finding two numbers that multiply to 6 and add "
            "to -5, which are -2 and -3. Is that right?",
            "I solved 2x + 5 = 13. I subtracted 5 to get 2x = 8, then divided by 2 to "
            "get x = 4. Can you check my steps?",
            # Understanding questions.
            "Why does multiplying both the divisor and dividend by 100 not change the "
            "quotient?",
            "I don't understand why static friction is greater than kinetic friction.",
            # Asking for a hint, not a solution.
            "Can you give me a hint on where to start with this word problem?",
        ],
    )
    def test_genuine_turns_do_not_fire(self, text):
        assert not fires(text)

    def test_it_is_narrow_enough_to_outrank_the_gate(self):
        """The override beats a NO from the LLM gate, so it must be high-precision.

        The full regex OR'd with the gate was measured at 93.1% recall and 21.1%
        false positives and REJECTED. This one earns the override only because its
        own false-positive rate was measured separately: 0 of 58 genuine turns.
        """
        broad = [
            "What is photosynthesis?",
            "Can you explain how fractions work?",
            "My teacher assigned a worksheet on the water cycle.",
            "I am doing a chemistry lab report on the periodic table.",
        ]
        assert not any(fires(t) for t in broad)
