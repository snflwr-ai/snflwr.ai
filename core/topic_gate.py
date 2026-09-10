"""Structural topic gate — makes the tutor *unable* to answer off-topic, not
merely instructed to decline.

WHY
---
S9051B's permitted uses each carry the proviso that the covered AI companion be

    "UNABLE TO RESPOND ON TOPICS OUTSIDE OF THE SPECIFIED PURPOSE"

snflwr.ai currently *declines* off-topic questions: the Modelfile instructs a
redirect and ``safety_monitor._detect_persistent_off_topic`` raises an alert
after repeated attempts. Both are behavioural. The safety pipeline itself gates
on HARM categories (violence, self-harm, sexual, drugs, weapons, PII, hate,
bypass), so a benign-but-off-topic question — "what movie should I watch" —
passes straight through to the model. Under that clause, declining and being
unable are different products.

This module is the structural half.

DO NOT ENABLE THIS YET — MEASURED 2026-09-09
--------------------------------------------
Conversation context helped a great deal and is still not enough.

  no context   50.0% of real schoolwork refused
  context      18.8%                            <- current state
  correct-block rate 100% in both arms — nothing off-topic leaked either way

Measured on ``topic_gate_holdout2.yaml``, written and committed BEFORE the
context change so the implementation could not shape the test, with the
no-context arm run as a control on the identical cases. Without that control
this looked like a failed fix: the first held-out file
(``topic_gate_holdout.yaml``) still reports 16.7% afterwards, but its cases carry
no history at all, so the change cannot apply to them. Comparing against it alone
would have produced a confident and wrong conclusion.

18.8% is still not shippable. The three remaining refusals share a shape:

    "which one comes first, the one on top or the bottom"
    "do i have to write all that down"
    "can you give me a different one"

None is a question about a SUBJECT. They are questions about doing the WORK —
procedural and meta. The classifier prompt asks whether the turn "is a request
for help with a school subject", and by that criterion these honestly are not,
even though refusing them makes a terrible tutor. The residual is a criterion
problem, not a context problem.

BOTH held-out sets are now spent: the first diagnosed the context fix, the second
measured it. Whoever attempts the criterion fix needs a THIRD, and should write
it before touching the prompt.

OFF BY DEFAULT
--------------
``TOPIC_GATE_ENABLED`` defaults to false, matching the guidance-enforcer
precedent. Enabling it is a product decision, not a technical one: it trades
tutoring breadth for a narrower, provable purpose, and it will refuse some
legitimately academic questions that are oddly phrased. Measure the false-refusal
rate with ``evals/tutoring/topic_gate_canary.py`` before switching it on.

ORDERING IS CHILD-SAFETY-CRITICAL
---------------------------------
This gate MUST run after ``safety_pipeline.check_input``, never before. "I want
to hurt myself" is off-topic to any academic classifier. If this gate ran first,
a child in crisis would receive a generic topic refusal and the crisis path —
the 988 safe response, the incident record, the parent alert — would never
execute. That is a safety regression hiding inside a compliance feature, and it
would fail silently. The call site in ``api/routes/ollama_proxy/chat.py`` places
it after the input check for exactly this reason.

FAIL-CLOSED IS FREE HERE
------------------------
Failing closed on a topic filter would normally be a hard trade: a classifier
outage bricks tutoring. But this classifier runs on the same Ollama that serves
the tutor model. If it is unreachable there is no tutoring session to preserve,
so blocking costs nothing that was not already lost, and it keeps the statutory
posture honest rather than quietly opening a hole during an outage.

NOTE ON THE EXISTING MONITOR
----------------------------
``safety_monitor._detect_persistent_off_topic`` is NOT wired to this gate. It
reads its own in-memory ``_conversation_history`` and matches
``safety_config.REDIRECT_TOPICS`` keywords — an independent mechanism. Emitting
``Category.TOPIC_REDIRECT`` here keeps the incident taxonomy consistent, but it
does not feed that monitor. Wiring the two together is a separate change.

NOT LEGAL ADVICE. S9051B as passed names no tutoring permitted-use; this gate
targets the hypothetical-exemption posture and defence in depth, not a
guaranteed statutory safe harbour.
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, List, Optional

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

Classifier = Callable[[str], Awaitable[str]]


def _c(pattern: str) -> "re.Pattern[str]":
    """Case-insensitive compile — student turns arrive in any casing."""
    return re.compile(pattern, re.IGNORECASE)


# The classifier is asked for one token. Anything else is an unknown, and an
# unknown fails closed.
_YES = {"yes", "yes.", "y"}
_NO = {"no", "no.", "n"}

# Context sizing. Enough turns to establish what a conversation is about, capped
# so a long session cannot blow the classifier's latency budget or bury the turn
# being judged underneath its own history.
_HISTORY_TURNS = 6
_HISTORY_CHARS = 240


def _format_history(history: Optional[List[str]]) -> str:
    """Recent turns, oldest first, trimmed for length and count.

    Truncating each entry matters as much as capping the count: one pasted essay
    would otherwise crowd every other turn out of the window.
    """
    if not history:
        return ""
    recent = [str(turn) for turn in history[-_HISTORY_TURNS:] if str(turn).strip()]
    if not recent:
        return ""
    lines = []
    for turn in recent:
        text = turn.strip().replace("\n", " ")
        if len(text) > _HISTORY_CHARS:
            text = text[:_HISTORY_CHARS] + "..."
        lines.append(f"- {text}")
    return "\n".join(lines)


def build_prompt(question: str, history: Optional[List[str]] = None) -> str:
    """Ask whether a student turn is schoolwork, in a form that yields YES/NO.

    The hard-topics clause is load-bearing. History and literature are full of
    war, death, slavery and suicide; a naive "is this appropriate for school"
    prompt refuses Romeo and Juliet and guts the humanities. Whether harmful
    content is present is the safety pipeline's call, made before this gate runs
    — this gate decides only whether the SUBJECT is schoolwork.

    CONTEXT. Measured on a held-out set, classifying a turn in isolation refused
    16.7% of real schoolwork: "why did they do that though", "can you say it
    again but easier", "is 3 right". None of those carry a subject alone, and to
    a context-free classifier they look like the chit-chat the prompt excludes.
    Recent turns are supplied so a follow-up can be read as what it is.

    The instruction to judge the CURRENT turn is the counterweight. Without it,
    context invites the opposite failure — an off-topic pivot inheriting the
    verdict of the schoolwork that preceded it.
    """
    context = _format_history(history)
    context_block = (
        f"CONVERSATION SO FAR (oldest first, for context only):\n{context}\n\n"
        if context
        else ""
    )
    judged = (
        "Judge ONLY the student's CURRENT message below. Earlier turns are "
        'context for understanding it — a follow-up like "why though" or '
        '"say it again easier" continues the subject above and IS in scope. '
        "But if the current message changes the subject to something out of "
        "scope, answer NO even when the conversation before it was schoolwork.\n\n"
        if context
        else ""
    )
    return (
        context_block
        + judged
        + (
            "You are a topic classifier for a K-12 school tutoring tool. "
            "Decide whether the student's message is a request for help with a "
            "school subject.\n\n"
            "IN SCOPE: math, science, technology, engineering, reading, literature, "
            "writing, grammar, history, social studies, civics, geography, the arts, "
            "world languages, study skills, and questions about assigned work.\n"
            "Hard subject matter is IN SCOPE when it is academic: war, slavery, the "
            "Holocaust, a character's death in a novel, historical atrocities, and "
            "political debates are all legitimate history and literature topics.\n\n"
            "OUT OF SCOPE: personal life, friendships, dating, family matters, "
            "shopping or product recommendations, entertainment choices, games, "
            "sports scores, chit-chat, and questions about the assistant itself.\n\n"
            "Answer with exactly one word, YES if it is a school subject request, "
            "NO if it is not. No punctuation, no explanation.\n\n"
            f"Student's CURRENT message: {question}\n"
            "Answer:"
        )
    )


def _parse_verdict(raw: str) -> Optional[bool]:
    """True = schoolwork, False = not, None = unparseable (caller fails closed)."""
    token = (raw or "").strip().lower().split()
    if not token:
        return None
    first = token[0].strip(".,!:;\"'")
    if first in {t.strip(".") for t in _YES}:
        return True
    if first in {t.strip(".") for t in _NO}:
        return False
    return None


def block_message(age: Optional[int]) -> str:
    """The refusal the student sees.

    Written to pass ``evals.tutoring.compliance_scorers`` — a refusal that said
    "I'm here for you!" would commit a §1801 violation while enforcing §1800.
    No claimed role, no claimed feeling, no flattery, no emoji; it states what
    the tool is for and returns the student to their work.
    """
    if age is not None and age <= 10:
        return (
            "This tool only helps with school subjects, so I can't answer that "
            "one. What are you working on for school?"
        )
    return (
        "This is a schoolwork tool, so that one is outside what it can answer. "
        "A parent or another trusted adult is the right person to ask. "
        "What subject are you working on?"
    )


# ---------------------------------------------------------------------------
# Deterministic fast path
#
# Measured on this box 2026-09-09: gemma4:e4b classifies correctly (algebra YES,
# Romeo and Juliet YES, movie-night NO) but takes 4.9-20.6s per call. A 0.6b model
# answers in ~0.3s and scored 0/8 — it emits `</think>` reasoning tokens rather
# than a bare verdict, and clamping num_predict truncates it before any answer.
# Neither is viable as an unconditional blocking pre-check on a child's turn:
# adding 5-20s before every message makes the tutor unusable.
#
# So obvious schoolwork resolves here at zero latency, and only ambiguous turns
# pay for a model call. Since real tutoring turns are overwhelmingly the obvious
# kind, the slow path is reached rarely.
#
# THE FAST PATH MAY ONLY ALLOW, NEVER BLOCK. A deterministic blocklist would
# refuse genuine schoolwork on an unlucky keyword — precisely the failure that
# makes the tutor worse than the statute requires. Anything not recognized here
# falls through to the classifier, which is the component allowed to say no.
# ---------------------------------------------------------------------------

_SUBJECT_TERMS = (
    # math
    r"factor|equation|fraction|decimal|multiply|divide|subtract|add|algebra|"
    r"geometry|calculus|derivative|integral|percent|ratio|numerator|denominator|"
    r"triangle|angle|quadratic|solve for|word problem|"
    # science
    r"photosynthesis|mitosis|molecule|atom|entropy|gravity|velocity|ecosystem|"
    r"evolution|chemical|periodic table|cell|organism|water cycle|experiment|"
    # english / literature
    r"thesis|essay|paragraph|main idea|metaphor|simile|verb|noun|adjective|"
    r"spelling|grammar|sentence|author|novel|poem|shakespeare|romeo|character|"
    r"summarize|moral of|theme of|rhyme|rhymes with|syllable|phonics|"
    r"opposite of|synonym|antonym|definition of|what does .{1,20} mean|"
    # history / civics / geography
    r"history|civil war|world war|revolution|constitution|amendment|congress|"
    r"branches of government|president|treaty|empire|civilization|colon(y|ies)|"
    r"pilgrims|democracy|election|capital of|continent|"
    # arts / languages / study
    r"renaissance|symphony|painting|sculpture|conjugate|vocabulary|homework|"
    r"assignment|study for|my (test|quiz|exam|class|teacher)"
)

_SCHOOLWORK_RE = _c(_SUBJECT_TERMS)

# "how do I solve", "what is the main idea", "explain ..." — schoolwork framings.
_TASK_RE = _c(
    r"\b(how (do|would) (i|you) (solve|calculate|compute|factor|find|prove|write)"
    r"|explain (the |how |why )?\w+"
    r"|what (is|are|was|were) the (main|causes?|effects?|theme|moral|difference)"
    r"|help me (write|solve|understand|study))"
)

# Bare arithmetic: "7 x 8", "3x + 2 = 14", "12 / 4"
_MATH_RE = _c(r"\d\s*[-+*/x×÷^=]\s*\d|\b\d+\s*[-+*/x×÷]\s*\w|\bx\s*\^\s*\d")


def is_obvious_schoolwork(question: str) -> bool:
    """True when a turn is recognizably schoolwork without asking a model.

    Only ever returns True (allow) or False (undecided — ask the classifier).
    It never asserts that something IS off-topic; that judgement needs the model.
    """
    text = (question or "").strip()
    if not text:
        return False
    return bool(
        _SCHOOLWORK_RE.search(text) or _TASK_RE.search(text) or _MATH_RE.search(text)
    )


async def _default_classifier(prompt: str) -> str:
    """Classify with the already-loaded tutor model — no extra VRAM.

    Deliberately reuses the tutor model rather than loading a second one: the
    box runs the tutor and llama-guard concurrently and has no headroom for a
    third. Kept tiny (``num_predict`` 4) because one token is all that is read.
    """
    import httpx

    payload = {
        "model": system_config.TOPIC_GATE_MODEL or system_config.OLLAMA_DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 4},
    }
    timeout = system_config.TOPIC_GATE_TIMEOUT_S
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{system_config.OLLAMA_PROXY_TARGET.rstrip('/')}/api/chat", json=payload
        )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")


async def off_topic_block_reason(
    question: str,
    *,
    age: Optional[int] = None,
    history: Optional[List[str]] = None,
    classify: Optional[Classifier] = None,
) -> Optional[str]:
    """Return a refusal message when *question* is not schoolwork, else ``None``.

    Returns ``None`` (allow) when the gate is disabled or the question is blank.
    Any classifier failure — error, timeout, or an unparseable answer — blocks.

    MUST be called after ``safety_pipeline.check_input``; see the module
    docstring for why the order is a child-safety property and not a preference.
    """
    if not system_config.TOPIC_GATE_ENABLED:
        return None
    if not (question or "").strip():
        # Blank input belongs to input validation. Abstaining avoids burning a
        # model call and avoids a confusing refusal on an empty turn.
        return None

    if is_obvious_schoolwork(question):
        # Zero-latency allow. See the fast-path note above for why this exists
        # and why it may only allow.
        return None

    classifier = classify or _default_classifier
    try:
        raw = await asyncio.wait_for(
            classifier(build_prompt(question, history)),
            timeout=system_config.TOPIC_GATE_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning(
            "Topic gate classifier unavailable (%s) — blocking. Fail-closed: the "
            "classifier shares Ollama with the tutor, so this turn was already "
            "not going to be answered.",
            exc,
        )
        return block_message(age)

    verdict = _parse_verdict(raw)
    if verdict is True:
        return None
    if verdict is None:
        logger.warning(
            "Topic gate could not parse the classifier verdict %r — blocking.", raw
        )
    return block_message(age)
