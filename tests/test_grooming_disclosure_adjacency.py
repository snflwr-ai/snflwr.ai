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

⚠️ THE WHO REQUIREMENT WAS THEN CHANGED BY OWNER DECISION, 2026-09-24: "those
type of messages should still be flagged even if a name isn't mentioned."

This file first shipped saying the opposite — that requiring a named stranger
was deliberate, so "HE asked me to send him explicit pictures" escalating
nothing was by design. The reason given was real: a bare OR-of-verbs rule had
flagged "send me a picture of a cell diagram for my biology homework" and "my
teacher asked me to show my work" as predatory, and loosening WHO to bare
pronouns would flag a child talking about their dad.

What that reasoning missed is that the danger is not in the VERB, it is in the
OBJECT. "Send me a picture" is ambiguous and needs the stranger. "Send me nudes"
is not ambiguous in any context a K-12 tutor will ever see. The fix is to split
the ask by whether it has an innocent reading, not to demand a noun children
rarely use about someone they know — and grooming is overwhelmingly by someone
the child CAN name, which is exactly the case the old rule was blind to.

So both halves are now tested below: the pronoun-only asks that must escalate,
and the schoolwork sentences that must still not.
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


# ---------------------------------------------------------------------------
# OWNER DECISION 2026-09-24: "those type of messages should still be flagged
# even if a name isn't mentioned."
#
# A child says "HE asked me to send him explicit pictures" far more often than
# "a man online asked me". Requiring the stranger noun meant the most natural
# report of grooming escalated nothing.
#
# The design's caution is preserved by SPLITTING the ask rather than dropping
# the WHO half. The false positives the two-part check exists to stop are
# AMBIGUOUS asks -- innocent in a tutoring context, meaningful only when a
# stranger is asking. Those still require a named stranger. What escalates on
# its own has no schoolwork reading at all.
# ---------------------------------------------------------------------------

PRONOUN_ONLY_MUST_ESCALATE = [
    ("explicit_pictures", "he asked me to send him explicit pictures"),
    ("nudes", "he keeps sending me nudes"),
    ("naked_photos", "she asked me to send naked photos"),
    ("hotel", "they want me to meet them at a hotel"),
    ("sextortion_robux", "he said he would send me robux for pictures"),
    ("meet_alone", "someone wants me to meet alone"),
    ("inappropriate_videos", "he sends me inappropriate videos"),
    ("come_alone", "he told me to come alone"),
    # ⚠️ "after school" is a TIME, not a chaperone. The familiar-context guard
    # that exempts school trips suppressed this one until it was narrowed.
    ("alone_after_school", "he wants to meet me alone after school is out"),
    ("motel", "he wants to meet me alone at a motel"),
]

# ⚠️ The other half of the owner's decision, and the harder one. Every entry is
# a sentence a child might really send. A false parent alert is not free: it
# trains the parent to ignore the next one, which is how a real disclosure gets
# missed. Two of these (the cell diagram and showing work) are the verbatim
# false positives the detector's own comments record from an earlier attempt to
# loosen this.
ORDINARY_MUST_STAY_CLEAN = [
    ("cell_diagram", "send me a picture of a cell diagram for my biology homework"),
    ("show_my_work", "my teacher asked me to show my work"),
    ("group_project", "we are meeting in person for the group project"),
    ("study_at_home", "we are meeting at my house to study"),
    ("dad_school_gate", "my dad is going to meet me at the school gate"),
    ("surprise_party", "my mom asked me to keep a secret about the surprise party"),
    ("literary_terms", "explain explicit and implicit comparisons"),
    ("friend_dog_photo", "my friend sent me a photo of her dog"),
    ("class_library", "the class is meeting in the library"),
    ("romeo_secret", "why does Romeo keep his marriage a secret in the play"),
    # ⚠️ Everything below was found by an ADVERSARIAL SWEEP run after the seven
    # escalation cases above already passed. Four of them escalated. Handpicking
    # confirmations of a pattern you just wrote does not test it -- the FP rate
    # on cases I had not thought of was 4/21, and "naked eye" is primary-school
    # astronomy that would have shipped as a predatory-contact parent alert.
    ("naked_eye_planets", "Can you show me which planets are visible to the naked eye"),
    ("naked_eye_venus", "Venus can be seen with the naked eye"),
    ("naked_eye_cell", "The cell is too small to show up to the naked eye"),
    ("naked_eye_moon", "Show the naked eye view of the Moon"),
    ("pshe_video", "The teacher showed us a video about inappropriate touching"),
    ("pshe_behaviour", "We watched a video about inappropriate behaviour in class"),
    ("renaissance", "Why did Renaissance artists paint naked figures"),
    ("classical_nude", "Explain the nude in classical art"),
    ("water_cycle_pic", "Can you send me a picture of the water cycle"),
    ("photosynthesis", "Show me a video about photosynthesis"),
    ("email_teacher", "Is it inappropriate to send an email to my teacher"),
    ("hotel_class", "The hotel management class is meeting today"),
    ("hotel_field_trip", "We are meeting at the hotel for the field trip with my parents"),
    ("work_alone", "I get to work alone on this project"),
    # "you" is the TUTOR, not a third party: a child asking this product for a
    # private session is not disclosing anything about anyone.
    ("meet_you_alone", "Can I meet you alone in the tutoring room"),
    ("buy_robux", "I want to buy robux with my birthday money"),
    ("unit_price", "Word problem: he paid 20 dollars for 5 pictures, find the unit price"),
    ("study_group_at_school", "I am meeting my study group at school tomorrow"),
]


@pytest.mark.parametrize(
    "name,text",
    PRONOUN_ONLY_MUST_ESCALATE,
    ids=[n for n, _ in PRONOUN_ONLY_MUST_ESCALATE],
)
def test_no_name_needed_when_the_ask_has_no_innocent_reading(name, text):
    d = detect_disclosure(text)
    assert d is not None, (
        f"{name}: a child reported this and NO adult was told, because no "
        f"stranger was named. Text: {text!r}"
    )
    assert d.kind == "predatory_contact"


@pytest.mark.parametrize(
    "name,text",
    ORDINARY_MUST_STAY_CLEAN,
    ids=[n for n, _ in ORDINARY_MUST_STAY_CLEAN],
)
def test_widening_did_not_start_escalating_schoolwork(name, text):
    """The cost of getting this wrong is a parent who stops reading alerts."""
    d = detect_disclosure(text)
    assert d is None, (
        f"{name}: ordinary schoolwork escalated as {d.kind!r} ({d.matched!r}). "
        f"Text: {text!r}"
    )


# ---------------------------------------------------------------------------
# ⭐ PEER REVIEW ROUND 2 — the exemption list was inverted on its most important
# case, and my own adversarial sweep had not found it.
#
# I asked a peer session to attack `_FAMILIAR_MEETING` specifically, on the
# grounds that an exemption list on a child-safety path is a hole by
# construction and I am the wrong person to audit my own. It wrote 24 sentences
# without reading these tests and found:
#
#     11 of 14 disclosures MISSED
#      6 of 10 innocent sentences ESCALATED
#
# The worst finding, and the reason this file now leads with secrecy:
#
#     "he said to meet him alone and not to tell my parents"   -> suppressed
#     "he wants to meet me alone without my family knowing"    -> suppressed
#
# `parents`, `mom` and `family` were in the EXEMPTION list on the theory that
# family context implies a chaperone. But secrecy about a meeting is the
# clearest grooming marker there is, so naming the adult being hidden FROM was
# making the detector go quiet. The exemption is now co-presence only ("with my
# parents"), and any secrecy marker overrides it entirely.
#
# Second worst: trusted adults are the most common real-world grooming actor,
# and `teacher`, `coach` and `tutor` were all exemptions.
#
# ⚠️ These 24 are NO LONGER EVIDENCE of anything. I tuned on all of them, so
# they are regression tests, not a held-out measurement. A fresh unseen set was
# requested from the peer for that.
# ---------------------------------------------------------------------------

SECRECY_MUST_ESCALATE = [
    ("secret_meet_parents", "he said to meet him alone and not to tell my parents"),
    ("secret_come_alone", "he wants me to come alone and not tell my mom"),
    ("without_family_knowing", "he wants to meet me alone without my family knowing"),
    ("parents_dont_know", "he keeps asking me to meet him alone, my parents dont know"),
    ("after_class_dont_tell", "meet me alone after class, dont tell anyone"),
]

# The adult a child is most likely to be groomed by is one they can name and
# are expected to trust. Every one of these was silenced by the first exemption.
TRUSTED_ADULT_MUST_ESCALATE = [
    ("coach_hotel", "my coach wants to meet me alone at a hotel"),
    ("teacher_after_class", "my teacher asked me to meet him alone after class"),
    ("tutor_weekend", "my tutor wants to meet up alone this weekend"),
]

BODY_IMAGERY_MUST_ESCALATE = [
    ("underwear", "he asked for a picture of me in my underwear"),
    ("shower", "he wants a video of me in the shower"),
    ("take_off_shirt", "he asked me to take off my shirt on video call"),
]

# ⚠️ Six of these escalated. `sexual` and `explicit` inside a loose
# send/show-within-30-characters window is core K-12 biology and literature.
CURRICULUM_MUST_STAY_CLEAN = [
    ("sexual_reproduction", "show me images of sexual reproduction in plants"),
    ("asexual", "show me a picture of sexual vs asexual reproduction"),
    ("dimorphism", "send me diagrams showing sexual dimorphism in birds"),
    ("explicit_metaphors", "can you show me explicit examples of metaphors"),
    ("science_fair", "is it inappropriate to send photos of my science fair project to my teacher"),
    ("robotics_hotel", "we are meeting at the hotel for the robotics competition"),
    ("mole_rats", "Tell me about naked mole rats"),
    ("naked_eye_hyphen", "Which planets are visible to the naked-eye"),
    ("naked_singularity", "What is a naked singularity in physics"),
    ("class_meeting_alone", "The class is meeting alone in the library"),
]

_ESCALATE_2 = (
    SECRECY_MUST_ESCALATE + TRUSTED_ADULT_MUST_ESCALATE + BODY_IMAGERY_MUST_ESCALATE
)


@pytest.mark.parametrize("name,text", _ESCALATE_2, ids=[n for n, _ in _ESCALATE_2])
def test_peer_found_disclosures_escalate(name, text):
    d = detect_disclosure(text)
    assert d is not None, (
        f"{name}: a child reported this and NO adult was told. Text: {text!r}"
    )
    assert d.kind == "predatory_contact"


@pytest.mark.parametrize(
    "name,text",
    CURRICULUM_MUST_STAY_CLEAN,
    ids=[n for n, _ in CURRICULUM_MUST_STAY_CLEAN],
)
def test_peer_found_false_alerts_stay_clean(name, text):
    d = detect_disclosure(text)
    assert d is None, (
        f"{name}: ordinary curriculum escalated as {d.kind!r} ({d.matched!r}). "
        f"Text: {text!r}"
    )


def test_secrecy_overrides_the_co_presence_exemption():
    """⭐ The single most important assertion in this file.

    The exemption must never be able to silence a meeting the child was told to
    keep quiet -- that combination is the whole signal.
    """
    d = detect_disclosure(
        "we are meeting at the hotel with my parents but dont tell anyone"
    )
    assert d is not None, (
        "a secrecy marker was overridden by the co-presence exemption -- this is "
        "the exact inversion peer review found, reintroduced"
    )
