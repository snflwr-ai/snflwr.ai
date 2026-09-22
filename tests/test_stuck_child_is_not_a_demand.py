"""A child who is stuck mid-problem must not be treated as demanding the answer.

When `is_homework_request` fires, the child gets a deliberately withholding reply
AND waits far longer (measured p50 16.8s / p90 31.6s flagged, versus 7.7s / 14.4s
clean). So a false alarm on a genuinely stuck child is a real harm, not a
cosmetic one.

Provenance, stated because it matters: this work started from a figure I had been
quoting -- "a 24% false-alarm rate on children who are stuck mid-problem" -- which
turned out to have NO SOURCE. It existed only in a docstring I wrote myself. See
~/snflwr-artefacts/RETRACTION-24pct-false-alarms.md.

Measured properly on 60 wild-shaped stuck-child turns, generated blind to this
module: 4/60 = 6.7% false alarms, with the demand arm at 17/20 = 85% (so the
detector was firing, not dead). Constructed controls had put specificity at
96-100%; the gap between those two numbers is why wild-shaped probes exist. The
prereg and probes are in ~/snflwr-artefacts/2026-09-21-stuckchild/.

The four failures had three mechanisms, and each fixture below is one of them.
"""

import pytest

from core.pedagogy.trigger import is_homework_request


class TestTheChildAsksForLessThanTheAnswer:
    """Mechanism 1 and 2: an explicit refusal, ignored.

    `_LEARNING_INTENT` already contained "give me a hint" -- but it is consulted
    AFTER the hard demand patterns, which return True first. The veto written for
    exactly this case was unreachable, so a child saying "i dont want the answer"
    in the same sentence got stonewalled anyway.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # fired on "just give me", despite the refusal in the same breath
            "can u just give me a hint for the next part, i dont want the answer "
            "i wanna figure it out myself",
            # fired because the answer-demand pattern's `(\w+ ){0,3}` gap spans
            # "clue not the", reading "give me a clue not the answer" as
            # "give me a ... answer" -- the negation swallowed by the wildcard
            "give me a clue not the answer. its the one about if the rock floats",
            "dont tell me the answer just a hint for #14 plz",
            "can i get a nudge in the right direction, not the whole thing",
        ],
    )
    def test_a_refused_answer_plus_a_lesser_want_is_not_a_demand(self, text):
        assert not is_homework_request(text), (
            "the child refused the deliverable and named a lesser thing they "
            "want; firing here stonewalls a child who is trying to learn"
        )


class TestTheDemandVerbBelongsToSomeoneElse:
    """Mechanism 3: a demand-shaped verb whose subject is not the tutor.

    Two of the four measured false alarms. Neither turn asks the tutor for
    anything -- one states the child's own obligation, the other describes what
    the other children did.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # _CONTEXT("my lab") + _BARE_DEMAND("write"), from "i'm supposed to
            # write this up" -- the child's OWN obligation
            "my lab data says the mass went UP after we burned it which cant be "
            "right can it. im supposed to write this up tonight",
            # soft pattern matched "finish the lab worksheet" -- what the OTHER
            # children did
            "everyone else already finished the lab worksheet and im still on "
            "part 1 and i feel so dumb",
            "i have to solve 20 of these by friday and im stuck on the 3rd one",
        ],
    )
    def test_an_obligation_clause_is_not_a_request(self, text):
        assert not is_homework_request(text)


class TestNeitherVetoIsABypass:
    """The anti-dodge guard. This is the half that matters most.

    An answer-refusal veto is an obvious attack surface: bolt "i dont want the
    answer" onto a real demand and walk through. This project has already met the
    trick as "a dodge wearing a verification frame" (`_PRODUCE_FOR_ME`); a
    refusal frame is the same move, and writing a documented bypass into a
    children's product would be worse than the 6.7% it fixes.

    Both vetoes therefore require that NO full-deliverable imperative survives in
    the turn -- and for the obligation veto, that check runs on the text with the
    obligation clause REMOVED, which is the ordering I got wrong first time.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # refusal frame bolted onto a full deliverable
            "just write my paragraph, i dont want the answer",
            "i dont want the answer, whats the answer to number 7",
            "give me a hint. actually just do my homework for me",
            "not the answer, just a hint, ok fine do my worksheet for me",
            # obligation frame bolted onto a real imperative -- the frame is
            # genuine, the demand is still there
            "i have to write a paragraph about the water cycle, just write it for me",
            "im supposed to solve number 7 tonight, solve it for me",
            "everyone else finished already so just do my worksheet",
            "i need to write my essay tonight so write it for me",
            "i gotta do #14, just tell me the answer",
            "i wanna figure it out myself but first do question 12 for me",
        ],
    )
    def test_a_frame_bolted_onto_a_real_demand_still_fires(self, text):
        assert is_homework_request(text), (
            "a refusal or obligation frame must not launder a demand for the "
            "assigned deliverable -- the child still walks away with the work done"
        )


class TestTheVetoesDidNotCostRecall:
    """Trading real catches for false alarms is not a fix.

    A missed demand means a child gets their homework done for them. The prereg
    bar was that demand recall must not drop AT ALL, and it did not (17/20 before
    and after).
    """

    @pytest.mark.parametrize(
        "text",
        [
            "whats the answer to number 7 on my sheet, 15 take away 8",
            "write a paragraph about the water cycle for my homework",
            "do question 12 of my math homework for me",
            "just tell me the answer to 3x + 7 = 22, no working",
            "solve 3x+7=22, its problem 4 on the worksheet",
        ],
    )
    def test_plain_demands_still_fire(self, text):
        assert is_homework_request(text)


class TestStudyAidsStayClean:
    """Rule 7, ruled by the owner 2026-09-21: study aids are NOT assigned work.

    A child revising for tomorrow's test is entitled to the answer, so
    withholding on these is a DEFECT, not caution. Guarded here because the
    vetoes above must not be the only thing keeping them clean.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "how do you spell separate, its on my spelling test tomorrow",
            "define opportunity cost for my study guide",
            "prime vs composite for my test tomorrow",
            "which is bigger, 41 or 14?",
            "quiz me on my spelling words",
        ],
    )
    def test_a_study_aid_request_is_not_homework(self, text):
        assert not is_homework_request(text)
