"""Tests for the structural topic gate.

WHY IT EXISTS
-------------
S9051B's permitted uses each carry the proviso that the system be

    "UNABLE TO RESPOND ON TOPICS OUTSIDE OF THE SPECIFIED PURPOSE"

Today snflwr.ai *declines* off-topic questions — the Modelfile instructs a
redirect — but nothing makes it *unable* to answer one. The safety pipeline
gates on HARM (violence, self-harm, sexual, drugs, weapons, PII, hate), so a
benign-but-off-topic question passes straight through to the model. Declining
and being unable are different products under that clause.

This gate is the structural half. It ships OFF by default, matching the
guidance-enforcer precedent: turning it on is a product decision, because it
trades tutoring breadth for a narrower, provable purpose.

TWO DESIGN POINTS THE TESTS PIN DOWN
------------------------------------
1. It must run AFTER the safety pipeline's check_input, never before. "I want to
   hurt myself" is off-topic to any academic classifier; if the topic gate fired
   first, the child would get a generic topic refusal and the crisis path — 988
   response, incident record, parent alert — would never run. That is a safety
   regression hiding inside a compliance feature, and it would fail silently.

2. It fails CLOSED when enabled. That is normally a hard trade for a topic
   filter, since a classifier outage would brick tutoring — except the
   classifier runs on the same Ollama that serves the tutor. If it is
   unreachable there is no tutoring to protect, so failing closed costs nothing
   and keeps the statutory posture honest.
"""

import asyncio

import pytest
from evals.tutoring import compliance_scorers as cs

from core import topic_gate


def _run(coro):
    return asyncio.run(coro)


def _classifier(verdict):
    """A stub classifier returning a fixed raw model reply."""

    async def _c(prompt: str) -> str:
        return verdict

    return _c


def _exploding_classifier(exc):
    async def _c(prompt: str) -> str:
        raise exc

    return _c


# ---------------------------------------------------------------------------
# default posture
# ---------------------------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    """Shipping on would silently narrow every deployment. Off is the default."""
    from config import system_config

    assert system_config.TOPIC_GATE_ENABLED is False


def test_disabled_gate_allows_anything(monkeypatch):
    monkeypatch.setattr(topic_gate.system_config, "TOPIC_GATE_ENABLED", False)
    result = _run(
        topic_gate.off_topic_block_reason(
            "what movie should I watch tonight", classify=_classifier("NO")
        )
    )
    assert result is None


# ---------------------------------------------------------------------------
# enabled: the actual gate
# ---------------------------------------------------------------------------


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(topic_gate.system_config, "TOPIC_GATE_ENABLED", True)


def test_allows_schoolwork(enabled):
    result = _run(
        topic_gate.off_topic_block_reason(
            "how do I factor x^2 + 5x + 6", classify=_classifier("YES")
        )
    )
    assert result is None


def test_blocks_off_topic(enabled):
    result = _run(
        topic_gate.off_topic_block_reason(
            "what movie should I watch tonight", classify=_classifier("NO")
        )
    )
    assert result is not None
    assert isinstance(result, str) and result.strip()


# An AMBIGUOUS turn — deliberately carries no subject keyword, no schoolwork
# framing and no arithmetic, so the deterministic fast path abstains and the
# classifier is genuinely consulted. Using recognizable schoolwork here would
# short-circuit before the classifier and make these tests pass vacuously.
AMBIGUOUS = "can you tell me about that thing we discussed"


def test_classifier_error_blocks(enabled):
    """Fail closed on an UNRESOLVED turn. The classifier shares Ollama with the
    tutor, so an outage means there is no tutoring session to preserve."""
    result = _run(
        topic_gate.off_topic_block_reason(
            AMBIGUOUS, classify=_exploding_classifier(RuntimeError("ollama down"))
        )
    )
    assert result is not None


def test_classifier_timeout_blocks(enabled):
    result = _run(
        topic_gate.off_topic_block_reason(
            AMBIGUOUS, classify=_exploding_classifier(asyncio.TimeoutError())
        )
    )
    assert result is not None


def test_unparseable_verdict_blocks(enabled):
    """A model answering in prose rather than YES/NO is an unknown, and an
    unknown fails closed."""
    result = _run(
        topic_gate.off_topic_block_reason(
            AMBIGUOUS, classify=_classifier("Well, that depends on what you mean by...")
        )
    )
    assert result is not None


def test_obvious_schoolwork_survives_a_classifier_outage(enabled):
    """The fast path narrows what fail-closed means, deliberately.

    Recognizable schoolwork is allowed WITHOUT consulting the classifier, so an
    Ollama outage cannot refuse a child's algebra question. The tradeoff is
    explicit: an off-topic turn that happens to contain a subject keyword is
    allowed during an outage too. Blocking real homework because a classifier
    blipped is the worse failure for a tutoring product."""
    result = _run(
        topic_gate.off_topic_block_reason(
            "how do I factor x^2 + 5x + 6",
            classify=_exploding_classifier(RuntimeError("ollama down")),
        )
    )
    assert result is None


def test_empty_question_is_not_the_topic_gates_problem(enabled):
    """Blank input is input-validation's job; the gate abstains rather than
    burning a model call and emitting a confusing refusal."""
    assert _run(topic_gate.off_topic_block_reason("   ", classify=_classifier("NO"))) is None


# ---------------------------------------------------------------------------
# verdict parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("YES", True),
        ("yes", True),
        ("Yes.", True),
        ("  YES  \n", True),
        ("NO", False),
        ("no", False),
        ("No.", False),
        ("", None),
        ("maybe", None),
        ("I think so", None),
    ],
)
def test_parse_verdict(raw, expected):
    assert topic_gate._parse_verdict(raw) is expected


# ---------------------------------------------------------------------------
# the refusal copy must not itself violate the statute
# ---------------------------------------------------------------------------


def test_block_message_is_compliant():
    """A refusal saying "I'm here for you!" would commit a §1801 violation while
    enforcing §1800. The gate's own copy gets the same screen as the tutor's."""
    for age in (6, 9, 12, 16, None):
        message = topic_gate.block_message(age)
        found = cs.scan(message)
        assert found == {}, f"refusal copy violates §1801 for age {age}: {found}"


def test_block_message_is_short_and_redirects_to_work():
    for age in (6, 16):
        message = topic_gate.block_message(age)
        assert len(message.split()) <= 40, "a refusal should be brief"
        assert "?" in message, "should invite the student back to schoolwork"


def test_block_message_adapts_to_age():
    assert topic_gate.block_message(6) != topic_gate.block_message(16)


def test_block_message_never_claims_a_role_or_feeling():
    """Belt and braces on the highest-risk phrasings."""
    for age in (6, 9, 12, 16, None):
        lowered = topic_gate.block_message(age).lower()
        for banned in ("i'm your", "i am your", "here for you", "i love", "i feel"):
            assert banned not in lowered


# ---------------------------------------------------------------------------
# prompt construction
# ---------------------------------------------------------------------------


def test_prompt_demands_a_bare_verdict():
    prompt = topic_gate.build_prompt("why is the sky blue")
    assert "YES" in prompt and "NO" in prompt
    assert "why is the sky blue" in prompt


def test_prompt_treats_hard_academic_topics_as_in_scope():
    """History and literature are full of violence and death. The classifier
    must not be told to refuse them — that is the safety pipeline's call, not
    the topic gate's, and conflating them would gut the humanities."""
    prompt = topic_gate.build_prompt("why does Romeo kill himself").lower()
    assert "history" in prompt or "literature" in prompt


# ---------------------------------------------------------------------------
# Deterministic fast path
#
# Measured 2026-09-09 on this box: gemma4:e4b classifies correctly (algebra YES,
# Romeo and Juliet YES, movie-night NO) but takes 4.9-20.6s. qwen3:0.6b answers
# in ~0.3s but scored 0/8 — it emits `</think>` reasoning tokens and never a bare
# verdict. Neither is usable as an unconditional blocking pre-check on a child's
# turn, so obvious schoolwork resolves locally at zero latency and only ambiguous
# turns pay for a model call.
#
# The fast path may only ALLOW, never block. A deterministic blocklist would
# refuse real schoolwork on a keyword, which is the failure mode that makes the
# tutor worse than the law requires.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "how do I factor x^2 + 5x + 6",
        "what is 7 x 8",
        "explain photosynthesis",
        "what were the causes of World War 1",
        "help me write a thesis statement for my essay",
        "why does Romeo kill himself in the play",
        "what is the main idea of this paragraph",
        "how do I solve 3x + 2 = 14",
        "what are the three branches of government",
    ],
)
def test_fast_path_recognizes_obvious_schoolwork(question):
    assert topic_gate.is_obvious_schoolwork(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "what movie should I watch tonight",
        "do you think I should text him back",
        "what should I get my mom for her birthday",
        "tell me a joke",
        "who won the game last night",
    ],
)
def test_fast_path_abstains_on_off_topic(question):
    """Abstain, not block — the model decides. The fast path never refuses."""
    assert topic_gate.is_obvious_schoolwork(question) is False


def test_obvious_schoolwork_skips_the_model_entirely(enabled):
    """The latency win only exists if the classifier is genuinely not called."""
    calls = []

    async def _tracking(prompt):
        calls.append(prompt)
        return "NO"

    result = _run(
        topic_gate.off_topic_block_reason("what is 7 x 8", classify=_tracking)
    )
    assert result is None
    assert calls == [], "obvious schoolwork must not reach the classifier"


def test_ambiguous_turn_still_consults_the_model(enabled):
    calls = []

    async def _tracking(prompt):
        calls.append(prompt)
        return "NO"

    result = _run(
        topic_gate.off_topic_block_reason("what movie should I watch", classify=_tracking)
    )
    assert result is not None
    assert len(calls) == 1, "an ambiguous turn must be classified"
