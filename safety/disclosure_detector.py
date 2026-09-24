"""Detect a child DISCLOSING risk, as distinct from requesting harmful content.

A harm classifier answers "is this content harmful?". Llama Guard returns `safe`
for "an adult online wants to meet me alone" and it is RIGHT to: the child's
message is not harmful content. But that is the message a K-12 product most needs
an adult to see.

Measured 2026-09-12/13 on 20 independently generated disclosures:

    harmful content requests blocked        4/4
    tutor redirected child to an adult     15/17   <- the REPLY was already good
    disclosures escalated to an adult       1/12   <- nobody was ever told
    replies naming a crisis resource        0/17

Nothing else surfaces conversation content to a parent (analytics carries no
transcript), so an unrecorded disclosure is invisible to every adult forever.

WHY PATTERNS HERE, when they failed for the homework gate. The cost asymmetry is
inverted. A false positive means a parent gets a note about a child who was fine.
A miss means nobody learns a child disclosed grooming. Recall is bought at the
price of precision deliberately -- the opposite of the homework case, where a
false positive degraded a real answer a child needed.

This detector NEVER blocks. It marks a turn for escalation while the tutor's own
reply is served unchanged.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# Ordered most to least severe; the first match wins so the escalation carries
# the right severity.
_CLASSES: Tuple[Tuple[str, str], ...] = (
    (
        "suicidal_ideation",
        r"\b(?:"
        r"want(?:s|ed)? to (?:die|disappear|not exist|not be here)"
        r"|(?:just )?want to (?:go to sleep|sleep) and not wake"
        r"|don'?t want to (?:be here|wake up|exist|live)"
        r"|wish i (?:was|were) (?:dead|gone|not here)"
        r"|(?:just )?wanna be (?:gone|dead)"
        r"|hate (?:existing|being alive|my life)"
        r"|stop(?:ped)? showing up to (?:school and )?life"
        r"|feel like i'?m disappearing"
        r"|nobody would (?:notice|care) if i"
        r"|end (?:it|things|my life)"
        r"|hurt(?:ing)? myself|harm myself|cut myself"
        r"|stop bothering anyone"
        r")",
    ),
    (
        "predatory_contact",
        # Requires BOTH halves, checked in detect_disclosure below rather than as
        # one alternation. A bare verb is not a signal: "send me a picture of a
        # cell diagram for my biology homework" and "my teacher asked me to show
        # my work" both matched an OR-of-verbs version of this and were flagged
        # predatory. The distinguishing feature is WHO is asking.
        None,
    ),
    (
        "bullying_victim",
        r"\b(?:"
        r"(?:being|getting) (?:bullied|picked on|teased|targeted|left out|excluded)"
        r"|they keep (?:saying|calling|ignoring|making fun)"
        r"|everyone (?:is )?(?:laughing at|looking at) me"
        r"|feel like a ghost"
        r"|(?:nobody|no one) (?:lets me|will let me|talks to me)"
        r"|(?:scared|afraid) to go to (?:lunch|school|class)"
        r"|makes? me feel so small"
        r"|spread(?:ing)? (?:a )?rumor about me"
        r")",
    ),
    (
        "disordered_eating",
        r"\b(?:"
        r"(?:starve|starving) myself"
        r"|stop(?:ped)? eating (?:completely|altogether)?"
        r"|(?:skip|skipping) (?:breakfast|lunch|dinner|meals)"
        r"|make myself (?:vomit|throw up|sick)"
        r"|only drink water for"
        r"|(?:feel|feeling) (?:so )?(?:fat|disgusting) "
        r"|shrink my waist"
        r"|lose \d+ ?(?:pounds|lbs|kg) (?:before|by|in)"
        r")",
    ),
)

# Schoolwork ABOUT a dark topic is not a disclosure. Measured: the existing
# pattern stage blocks "foreshadowing before the suicides in Romeo and Juliet"
# as substring evasion, which is a child researching a set text.
_ACADEMIC_FRAME = re.compile(
    r"\b(?:"
    r"for (?:my|our) (?:\w+ ){0,2}(?:essay|class|homework|assignment|paper|project|quiz|presentation|unit)"
    r"|in (?:the (?:novel|book|play|story)|romeo and juliet|of mice and men|lord of the flies"
    r"|hamlet|macbeth|the great gatsby|1984)"
    r"|(?:analy[sz]e|analysing|analyzing|interpret|explain|discuss|compare) the"
    r"|(?:author|character|protagonist|narrator)(?:'s)? (?:use|motivation|choice)"
    r"|(?:dramatic |verbal )?irony|foreshadowing|symbolis(?:m|e)|theme of"
    r"|(?:historical|ethical) (?:context|arguments?)"
    r"|statistics on|caloric intake|neurotransmitters|for (?:my )?health class"
    r"|daily recommended"
    r")",
    re.IGNORECASE,
)

# Who is asking: an adult or an online stranger, not a teacher or a classmate.
_STRANGER = re.compile(
    r"\b(?:"
    r"(?:a |this |some )?(?:guy|man|woman|adult|stranger|older (?:guy|man|kid|boy|girl))\b"
    r"|someone (?:on|from) (?:discord|roblox|snap|snapchat|insta|instagram|tiktok|a game|a server)"
    r"|(?:met|met up with) (?:someone|a guy|a man|a woman|this person)"
    r"|(?:this|that) (?:person|user|account)\b"
    r"|(?:someone|somebody) (?:i met )?online\b"
    r"|on a (?:gaming )?server\b"
    r")",
    re.IGNORECASE,
)
# What is being asked for: private contact, images, secrecy, isolation, payment.
# ⚠️ ASKS WITH NO INNOCENT READING — these escalate WITHOUT a named stranger.
#
# Owner decision 2026-09-24: "those type of messages should still be flagged
# even if a name isn't mentioned". A child says "HE asked me to send him
# explicit pictures" far more often than "a man online asked me"; requiring the
# stranger noun meant the most natural report of grooming escalated nothing.
#
# The design's caution is preserved by SPLITTING the ask, not by dropping the
# WHO half. The false positives the two-part check was built to stop -- "send me
# a picture of a cell diagram for my biology homework", "my teacher asked me to
# show my work" -- are AMBIGUOUS asks: innocent in a tutoring context and only
# meaningful when a stranger is doing the asking. Those still require WHO,
# below.
#
# What is here has no schoolwork reading at all: sexual imagery, meeting alone
# or in a hotel, and payment for pictures (the sextortion shape). A parent alert
# on one of these is not a cost worth trading a missed disclosure for.
_STRONG_ASK = re.compile(
    r"\b(?:"
    # sexual imagery, however the sentence is arranged.
    # ⚠️ `naked` carries a negative lookahead for "eye". "Which planets are
    # visible to the naked eye" and "too small to show up to the naked eye" are
    # core primary-science sentences, and both escalated as predatory contact
    # before the lookahead was added.
    r"(?:send|sending|sent|show|showing|post|posted)\b[^.?!]{0,30}"
    r"\b(?:explicit|nudes?|naked(?!\s*eye)|sexual|inappropriate)\b"
    r"|\b(?:explicit|nudes?|naked(?!\s*eye)|sexual|inappropriate)\b[^.?!]{0,20}"
    r"\b(?:photos?|pics?|pictures?|videos?|selfies?|snaps?|images?)\b"
    r"|\bsend (?:me |him |her |them |you )?(?:some )?nudes?\b"
    # sextortion
    r"|\b(?:pay|paid|pays|send|sent|offered)\b[^.?!]{0,20}"
    r"\b(?:money|gift ?cards?|robux|v-?bucks)\b[^.?!]{0,25}"
    r"\b(?:photos?|pics?|pictures?|videos?|nudes?)\b"
    r")",
    re.IGNORECASE,
)

# Meeting in circumstances a child should not be arranging -- but UNLIKE the
# imagery above, these have an innocent reading, so they are suppressed by the
# familiar-context guard below rather than firing unconditionally.
#
# ⚠️ "in person" and "at my house" are NOT here at all. "we are meeting in
# person for the group project" escalated as predatory when they were; school
# groups meet in person, and a false parent alert trains the parent to ignore
# the next one. Those stay in the contextual ask, which requires a named
# stranger.
#
# "alone" excludes `you`, because "you" is the TUTOR: "can I meet you alone in
# the tutoring room" is a child asking this product for a private session, not
# a disclosure about a third party.
_STRONG_MEETING = re.compile(
    r"\b(?:"
    r"meet(?:ing|\s+up)?\b[^.?!]{0,25}\b(?:hotel|motel)\b"
    r"|meet(?:ing|\s+up)?\b(?:(?!\byou\b)[^.?!]){0,15}\balone\b"
    r"|come alone\b"
    r")",
    re.IGNORECASE,
)

# A meeting with family, school or a class in the sentence is a school trip, not
# grooming. Found by adversarial sweep: "we are meeting at the hotel for the
# field trip with my parents" escalated without this.
_FAMILIAR_MEETING = re.compile(
    r"\b(?:parents?|mom|mum|mother|dad|father|family|grand(?:ma|pa|mother|father)"
    # ⚠️ NOT bare "school". "he wants to meet me alone after school is out" is a
    # disclosure, and bare `school` suppressed it -- "after school" is a TIME,
    # not a chaperone. Only school words that actually name a supervised setting
    # belong here.
    r"|teacher|coach|class|classmates?|school ?(?:trip|bus|gate|grounds)"
    r"|at school\b|field ?trip|group project|team|tutor(?:ing)?)\b",
    re.IGNORECASE,
)

_PRIVATE_ASK = re.compile(
    r"\b(?:"
    r"(?:wants?|asked|asking|telling) (?:me )?to (?:meet|video ?call|come over|show|send)"
    r"|shouldn'?t tell (?:my |anyone|my mom|my dad|my parents)"
    # ⚠️ "keep A secret" was NOT matched until 2026-09-24 -- the pattern required
    # "it" or "this". "a man online asked me to keep a secret from my parents"
    # is the most natural way a child reports this, and it escalated NOTHING.
    r"|keep (?:it|this|that|a|our|the) (?:a )?secret"
    r"|(?:our|a|this) (?:little )?secret"
    r"|don'?t tell (?:my |your )?(?:mom|dad|parents|anyone|mum)"
    r"|not to tell (?:my |your |his |her )?(?:mom|dad|parents|anyone|mum|teacher)"
    r"|(?:said|says) (?:he|she|they) (?:loves?|likes?) me"
    r"|only talk to (?:him|her|them)"
    r"|stop talking to (?:my )?friends"
    r"|(?:pay|paid|pays|send) me (?:money|a gift ?card|if|to|for)"
    r"|(?:meet|meeting)\b[^.?!]{0,20}\b(?:hotel|motel|alone|in person)"
    # ⚠️ ADJACENCY. This required the noun immediately after the pronoun, so ANY
    # adjective defeated it: "send him EXPLICIT pictures" did not match while
    # "send him pictures" did. Allow up to two words between, and cover the
    # nouns a child actually uses (pictures, snaps, nudes, messages, texts).
    r"|send (?:me |them |him |her |you )?(?:\w+ ){0,2}"
    r"(?:photos?|pics?|pictures?|videos?|selfies?|snaps?|nudes?|images?)"
    r"|(?:sending|sends|sent) (?:me |him |her |them )?(?:\w+ ){0,2}"
    r"(?:photos?|pics?|pictures?|videos?|selfies?|snaps?|nudes?|images?|messages?|texts?)"
    r"|show (?:them|him|her|me) (?:my|your) (?:room|house|body)"
    r"|asking (?:me )?(?:about|for) my (?:school|address|room)"
    r"|gift ?card"
    r")",
    re.IGNORECASE,
)

_COMPILED = tuple(
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in _CLASSES if pat is not None
)


@dataclass(frozen=True)
class Disclosure:
    """A child appears to be disclosing risk. NEVER a reason to block."""

    kind: str
    matched: str


def detect_disclosure(text: str) -> Optional[Disclosure]:
    """Return the disclosure class, or None.

    An academic framing vetoes the match: a child writing an essay about the
    suicides in Romeo and Juliet is doing schoolwork, and escalating that trains
    parents to ignore the alerts.
    """
    if not (text or "").strip():
        return None
    if _ACADEMIC_FRAME.search(text):
        return None
    # Ordered by severity: ideation first, then the two-part predatory check,
    # then the remaining single-pattern classes.
    for kind, pattern in _COMPILED:
        if kind == "suicidal_ideation":
            m = pattern.search(text)
            if m:
                return Disclosure(kind=kind, matched=m.group(0)[:60])
    # An ask with no innocent reading escalates on its own -- the WHO half is
    # not required, because there is no schoolwork sentence that looks like it.
    strong = _STRONG_ASK.search(text)
    if strong:
        return Disclosure(kind="predatory_contact", matched=strong.group(0)[:60])
    # A meeting escalates on its own only when nothing in the sentence places it
    # with family, a class or a school trip.
    meeting = _STRONG_MEETING.search(text)
    if meeting and not _FAMILIAR_MEETING.search(text):
        return Disclosure(kind="predatory_contact", matched=meeting.group(0)[:60])
    who = _STRANGER.search(text)
    what = _PRIVATE_ASK.search(text)
    if who and what:
        return Disclosure(
            kind="predatory_contact", matched=f"{who.group(0)} / {what.group(0)}"[:60]
        )
    for kind, pattern in _COMPILED:
        if kind == "suicidal_ideation":
            continue
        m = pattern.search(text)
        if m:
            return Disclosure(kind=kind, matched=m.group(0)[:60])
    return None


# Appended to the tutor's own reply, never instead of it. 0 of 17 replies named a
# crisis resource; "talk to a counsellor" is right but incomplete on its own.
_CRISIS_RESOURCES = {
    "suicidal_ideation": (
        "\n\nIf you want to talk to someone right now, you can call or text 988 "
        "(Suicide & Crisis Lifeline) any time, or text HOME to 741741. "
        "They are there for exactly this."
    ),
    "disordered_eating": (
        "\n\nIf you want to talk to someone about food and how you feel about your "
        "body, you can text HOME to 741741 any time."
    ),
}


def crisis_suffix(kind: str) -> str:
    """Resource text to append for this disclosure class ('' when none applies)."""
    return _CRISIS_RESOURCES.get(kind, "")
