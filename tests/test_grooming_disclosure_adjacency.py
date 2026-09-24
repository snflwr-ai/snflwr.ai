"""A child reporting grooming must reach an adult, whatever words they use.

`disclosure_detector` NEVER blocks — it marks a turn for escalation so a parent
is told. Its own docstring records why it exists: "disclosures escalated to an
adult 1/12 <- nobody was ever told".

⚠️ Found 2026-09-24 by a peer session reviewing an unrelated keyword change:
`predatory_contact` existed and did not fire on the phrasings children actually
use. Two near-misses, both caused by RIGID ADJACENCY in the WHAT half:

  "a man online asked me to keep a secret from my parents"
      -> `keep (?:it|this) (?:a )?secret` required "it" or "this".
         "keep A secret" is the natural phrasing and matched nothing.

  "a guy online keeps sending me explicit messages"
      -> `send (?:me|him|her)? (?:a|some)? (?:photo|pic|picture|video|selfie)`
         required the noun IMMEDIATELY after the pronoun, so any adjective
         defeated it -- and "messages"/"texts"/"snaps" were not nouns it knew.

Neither escalated. Nobody was told.

WHAT IS DELIBERATELY NOT FIXED: the WHO half still requires a named stranger
("a man", "someone online", "an older guy"), so "HE asked me to send him
explicit pictures" does not escalate. That conservatism is the design's, and the
code says why: a bare verb is not a signal, because "send me a picture of a cell
diagram for my biology homework" and "my teacher asked me to show my work" both
matched an OR-of-verbs version and were flagged predatory. Loosening WHO to bare
pronouns would flag a child talking about their dad. The pattern layer blocks
that phrasing separately (see the sexual-content cases below), so the two layers
cover different halves of it.
"""

import pytest

from safety.disclosure_detector import detect_disclosure
from safety.pipeline import _stage_normalize, _strip_invisible
from safety.pipeline.pattern_matcher import _PatternMatcher

# A named stranger AND a private ask. Both halves present, so these escalate.
MUST_ESCALATE = [
    ("keep_a_secret", "a man online asked me to keep a secret from my parents"),
    ("sending_messages", "a guy online keeps sending me explicit messages"),
    ("sent_texts", "someone online sent me explicit texts on snapchat"),
    ("not_to_tell", "an older guy told me not to tell my mom"),
    ("send_nudes", "a stranger online wants me to send him nudes"),
]

# ⚠️ The other direction, and the one that matters more. Escalating these
# trains a parent to ignore the alerts, which is how a real disclosure gets
# missed. Every one is drawn from the false positives the code's own comments
# record.
MUST_NOT_ESCALATE = [
    ("cell_diagram", "send me a picture of a cell diagram for my biology homework"),
    ("show_my_work", "my teacher asked me to show my work"),
    ("water_cycle", "can you send me a picture of the water cycle for my science project"),
    ("mum_surprise", "my mom asked me to keep a secret about the surprise party"),
    ("romeo", "why does Romeo keep his marriage a secret in the play"),
]


@pytest.mark.parametrize("name,text", MUST_ESCALATE, ids=[n for n, _ in MUST_ESCALATE])
def test_a_child_reporting_grooming_is_escalated(name, text):
    d = detect_disclosure(text)
    assert d is not None, (
        f"{name}: a child reported predatory contact and NOTHING was escalated. "
        f"No adult is told. Text: {text!r}"
    )
    assert d.kind == "predatory_contact", f"{name}: escalated as {d.kind!r}"


@pytest.mark.parametrize(
    "name,text", MUST_NOT_ESCALATE, ids=[n for n, _ in MUST_NOT_ESCALATE]
)
def test_ordinary_schoolwork_is_not_escalated_as_predatory(name, text):
    """A false alert is not free: it trains the parent to ignore the next one."""
    d = detect_disclosure(text)
    assert d is None, (
        f"{name}: ordinary schoolwork escalated as {d.kind!r}. Text: {text!r}"
    )


# The pattern layer's half: these BLOCK (they are sexual content), independently
# of whether the disclosure layer escalates. Closes the noun gap left by #320,
# which covered photo/pic/image/video but not picture, message, text or snap.
SEXUAL_NOUNS = [
    "he asked me to send him explicit pictures",
    "a guy keeps sending me explicit messages",
    "someone sent me explicit texts",
    "he sends me explicit snaps",
]


@pytest.mark.parametrize("text", SEXUAL_NOUNS, ids=range(len(SEXUAL_NOUNS)))
def test_the_pattern_layer_catches_every_explicit_noun(text):
    pm = _PatternMatcher()
    assert pm.check(_strip_invisible(text), _stage_normalize(text), context="") is not None, (
        f"not caught by the deterministic layer: {text!r}. It would reach the "
        f"llama-guard classifier, which is a real backstop but not defence in "
        f"depth."
    )


@pytest.mark.parametrize(
    "text",
    [
        "A simile uses like or as to make an explicit comparison.",
        "Write the explicit formula for the nth term of the sequence.",
    ],
)
def test_widening_the_nouns_did_not_re_break_the_curriculum(text):
    """The whole point of #320 was that bare "explicit" is K-12 vocabulary."""
    pm = _PatternMatcher()
    assert pm.check(_strip_invisible(text), _stage_normalize(text), context="") is None
