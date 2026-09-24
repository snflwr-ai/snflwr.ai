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
#
# ⚠️ ASKS WITH NO INNOCENT READING — these escalate WITHOUT a named stranger.
#
# Owner decision 2026-09-24: "those type of messages should still be flagged
# even if a name isn't mentioned". A child says "HE asked me to send him
# explicit pictures" far more often than "a man online asked me"; requiring the
# stranger noun meant the most natural report of grooming escalated nothing.
#
# The design's caution is preserved by SPLITTING the ask, not by dropping the
# WHO half. Ambiguous asks -- "send me a picture", "come over", "meet in
# person" -- still require WHO. Only asks with no schoolwork reading fire alone.

# ⭐ SECRECY IS THE SIGNAL, NOT THE EXEMPTION.
#
# The first version of this file put `parents`, `mom` and `family` in an
# EXEMPTION list, on the theory that family context means a chaperone. A peer
# review found that inverts the most important case in the file:
#
#     "he said to meet him alone and not to tell my parents"   -> suppressed
#     "he wants to meet me alone without my family knowing"    -> suppressed
#
# Secrecy about a meeting is the single clearest grooming marker there is, and
# naming the adult being hidden FROM was making the detector go quiet. Secrecy
# now forces escalation and disables every exemption below.
_SECRECY = re.compile(
    r"\b(?:"
    r"do(?:n'?t| not) (?:tell|say anything|mention)"
    r"|not to tell"
    r"|tell no ?(?:one|body)"
    r"|(?:no ?one|nobody) (?:can|should|must) know"
    r"|keep (?:it|this|that|a|our|the) (?:a )?secret"
    r"|our little secret"
    r"|without (?:my |his |her |their |anyone'?s? )?(?:\w+ )?knowing"
    r"|(?:parents?|mom|mum|dad|family|they) (?:don'?t|doesn'?t|do not) know"
    r"|between (?:me and him|me and her|us)\b"
    r"|delete (?:the|our|my) (?:messages?|chats?|texts?)"
    r")",
    re.IGNORECASE,
)

# Sexual imagery. Every branch here names the CHILD as the subject of the image
# or uses a word with no other reading.
#
# ⚠️ A verb-first branch -- (send|show) within 30 chars of
# (explicit|sexual|naked|inappropriate) -- was removed after it escalated SIX
# ordinary sentences a peer wrote, all core curriculum:
#
#     "show me images of sexual reproduction in plants"
#     "show me a picture of sexual vs asexual reproduction"
#     "send me diagrams showing sexual dimorphism in birds"
#     "can you show me explicit examples of metaphors"
#     "is it inappropriate to send photos of my science fair project"
#
# The adjective now has to MODIFY the imagery noun (at most one word between),
# which is what distinguishes "explicit pictures" from "explicit examples of"
# and "sexual reproduction" from "sexual photos".
_STRONG_ASK = re.compile(
    r"(?:"
    # "nudes" has no second meaning in any K-12 sentence.
    r"\b(?:send|sends|sending|sent|want|wants|asked|asking)\b[^.?!]{0,20}\bnudes?\b"
    # adjective directly modifying an image noun
    r"|\b(?:explicit|naked(?!\s*(?:eye|mole))|nude|sexual|inappropriate)\s+"
    r"(?:\w+\s+){0,1}(?:photos?|pics?|pictures?|videos?|selfies?|snaps?|images?)\b"
    # the child's own body: "a picture of me in my underwear", "video of me in
    # the shower", "take off your shirt on video call"
    r"|\b(?:photos?|pics?|pictures?|videos?|selfies?|snaps?)\s+of\s+(?:me|you|myself|yourself)\b"
    r"[^.?!]{0,30}\b(?:underwear|bra|panties|knickers|shower|bath|bed|undressed"
    r"|naked|no clothes|without (?:my |your )?clothes)\b"
    r"|\btake (?:off|of) (?:my|your|his|her) (?:shirt|top|clothes|pants|trousers|dress|skirt)\b"
    # sextortion
    r"|\b(?:pay|paid|pays|send|sent|offered|buy|get)\b[^.?!]{0,20}"
    r"\b(?:money|gift ?cards?|robux|v-?bucks)\b[^.?!]{0,25}"
    r"\b(?:photos?|pics?|pictures?|videos?|nudes?)\b"
    r")",
    re.IGNORECASE,
)

# Meeting in circumstances a child should not be arranging. Unlike the imagery
# above these have an innocent reading, so a CO-PRESENCE exemption can suppress
# them -- but only co-presence, and never when secrecy is present.
#
# ⚠️ "in person" and "at my house" are in NO strong tier: school groups meet in
# person, and a false parent alert trains the parent to ignore the next one.
#
# "alone" excludes `you`, because "you" is the TUTOR: "can I meet you alone in
# the tutoring room" is a child asking this product for a private session.
_STRONG_MEETING = re.compile(
    r"\b(?:"
    r"meet(?:ing|s|\s+up)?\b[^.?!]{0,25}\b(?:hotel|motel)\b"
    r"|meet(?:ing|s|\s+up)?\b(?:(?!\byou\b)[^.?!]){0,15}\balone\b"
    r"|come alone\b"
    r")",
    re.IGNORECASE,
)

# ⚠️ THIS IS AN EXEMPTION ON A CHILD-SAFETY PATH, so every entry is a hole by
# construction and the list is deliberately tiny.
#
# It matches CO-PRESENCE ("with my parents", "for the robotics competition"),
# never a bare mention of a person. The bare-mention version suppressed:
#
#     "my coach wants to meet me alone at a hotel"      (coach)
#     "my teacher asked me to meet him alone after class" (teacher, class)
#     "he wants to meet me alone after school is out"   (school)
#
# Trusted adults are the most common real-world grooming actor, so naming one
# must never quiet the detector; and "after school"/"after class" are TIMES,
# not chaperones.
_CHAPERONED = re.compile(
    r"\b(?:"
    r"with (?:my |our |the )?(?:parents?|mom|mum|dad|father|mother|family"
    r"|grand(?:ma|pa|mother|father)|teacher|coach|class|team|group|everyone)"
    r"|(?:field|school|class) ?trip"
    r"|competition|tournament|match|rehearsal|recital|excursion"
    r"|group project|study group"
    # A GROUP as the subject of the meeting: "the class is meeting alone in the
    # library". A class is not what gets groomed alone; a child is.
    r"|(?:class|team|group|club|everyone)\s+(?:is|are|was|were)\s+meet" r")",
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
    # A meeting a child is told to keep quiet escalates unconditionally. Secrecy
    # about a meeting OVERRIDES the co-presence exemption rather than being
    # exempted by it -- naming the adult being hidden FROM must never make this
    # detector go quiet.
    meeting = _STRONG_MEETING.search(text)
    secret = _SECRECY.search(text)
    if meeting:
        if secret:
            return Disclosure(
                kind="predatory_contact",
                matched=f"{meeting.group(0)} / {secret.group(0)}"[:60],
            )
        if not _CHAPERONED.search(text):
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
