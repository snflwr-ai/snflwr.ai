"""Cheap gate deciding which turns are worth a semantic disclosure check.

The regex disclosure detector reaches **6 of 22** on a held-out sealed set
(`~/snflwr-artefacts/2026-09-24-disclosure-sealed/`), and llama-guard backstops
it on only **2 of 22** because a harm classifier is off-construct for a child
REPORTING harm. A semantic pass is the only thing that closes the gap, and a
semantic pass needs a gate: one model call per turn is not affordable against
A6 (p90 <= 40s).

Total recall is `prefilter_recall x classifier_recall`, so THIS FILE IS THE
CEILING. It is built to miss nothing and to fire rarely, in that order.

MEASURED 2026-09-24, before any classifier was written:

    version                              fire rate (212 real turns)   sealed ceiling
    v1  actor anywhere + verb anywhere            32.5%   DEAD              --
    v2  subject-verb adjacency, roles listed       0.9%                     18/22
    v3  subject-verb adjacency, structural         8.0%                     21/22

v1 died on two structural facts, not on tuning:
  * `my (\\w+)` matches "my homework", "my science question"
  * show/tell/ask is how a child addresses the TUTOR -- "can you show me the
    water cycle" is the most common shape in the corpus

⚠️ That discriminator is "a disclosure has a THIRD PARTY as the SUBJECT of the
verb" ("HE asked me", "my coach wants"), against a tutoring request having the
tutor as the implied object ("show ME"). Requiring the actor immediately before
the verb is why v2/v3 cost 0.9%/8.0% instead of 32.5%.

⚠️⚠️ **THAT DISCRIMINATOR IS ONLY TRUE OF GROOMING, AND I WROTE IT HERE AS
THOUGH IT WERE TRUE OF DISCLOSURE IN GENERAL.** It is false for every kind
where the CHILD is the subject. v3 therefore BLOCKED "i want to die", and on a
second cold sealed set passed only 10 of 27 disclosures — missing three whole
kinds (ideation 0/7, eating 0/6, physical bullying 0/2). v4 adds the
first-person branch below; see its comment for the full post-mortem.

    v4  + first-person branch                      9.0%                     -

The first-person branch cost ONE point of fire rate (8.0% -> 9.0%) and
recovered three of the four kinds this gate exists to serve. It was never a
cost trade-off; it was an omission.

v2 enumerated person roles (uncle, coach, pastor, babysitter, ...) and missed a
friend's parent, a babysitter's partner and a youth leader -- the
enumerate-the-safe-cases shape that has been wrong three times in this
subsystem now (see `sweep-only-finds-its-own-direction`). v3 takes any
possessive noun phrase instead and lets the adjacency keep it cheap: 4 points
of ceiling for 7 points of fire rate, and one fewer list to be wrong about.

⚠️ 21/22 is a CEILING, not a result. The classifier behind it has its own
recall and the product is not 1.0 x anything.
"""

import re

# A person, or anything a child would speak of as one. Deliberately NOT a list
# of roles; see the module docstring.
_PERSON = (
    r"(?:he|she|they|somebody|someone|some ?one|everyone|everybody"
    r"|(?:my|our|his|her|their)\s+\w+(?:'s\s+\w+){0,2}"
    r"|the\s+(?:group|team|others|older\s+\w+)"
    r"|(?:some|a\s+bunch\s+of|these|those)\s+(?:people|kids|guys|boys|girls|men)"
    r"|this\s+(?:guy|man|woman|person|kid|boy|girl|user|account)"
    r"|a(?:n)?\s+(?:guy|man|woman|person|stranger|adult|kid|boy|girl|older\s+\w+)"
    r")"
)

# Verbs that put that person in contact with the child. Broad on purpose: the
# adjacency requirement below, not this list, is what keeps the gate cheap.
_VERB = (
    r"(?:ask(?:s|ed|ing)?|want(?:s|ed)?|said|says|say|tell(?:s|ing)?|told"
    r"|send(?:s|ing)?|sent|show(?:s|ed|ing)?|meet(?:s|ing)?|met|make(?:s)?|made"
    r"|call(?:s|ed|ing)?|text(?:s|ed|ing)?|messag(?:e|es|ed|ing)|dm(?:s|ed)?"
    r"|snap(?:s|ped)?|chat(?:s|ted|ting)?|keep(?:s)?|kept|give|gave|offer(?:s|ed)?"
    r"|pay|paid|come|came|touch(?:es|ed)?|kiss(?:es|ed)?|love(?:s)?|threaten(?:s|ed)?"
    r"|post(?:s|ed)?|follow(?:s|ed)?|added|invite(?:s|d)?"
    # ⚠️ Physical harm was missing entirely: "they keep HITTING me" passed only
    # by accident via `keep`, and "he PUSHED me into the lockers" was blocked.
    r"|hit(?:s|ting)?|punch(?:es|ed|ing)?|kick(?:s|ed|ing)?|push(?:es|ed|ing)?"
    r"|shov(?:e|es|ed|ing)|slap(?:s|ped)?|hurt(?:s|ing)?|beat|bully|bullies"
    r"|bullied|tease(?:s|d)?|exclude(?:s|d)?|ignore(?:s|d)?|laugh(?:s|ed)?)"
)

# Subject-verb, tolerating an auxiliary or adverb or two: "he keeps asking",
# "my coach wants", "he said he would send".
_SUBJ_VERB = re.compile(rf"\b{_PERSON}\s+(?:\w+\s+){{0,2}}{_VERB}\b", re.IGNORECASE)

# ⚠️⚠️ FIRST-PERSON DISCLOSURES. This branch exists because the version above,
# on its own, BLOCKED "i want to die".
#
# Measured 2026-09-24 on a peer's second cold sealed set: the gate passed only
# 10 of 27 disclosures, and the misses were not scattered -- they were three
# entire KINDS:
#
#     suicidal_ideation   0 / 7      <- including "i want to die"
#     disordered_eating   0 / 6
#     bullying (physical) 0 / 2
#
# The cause is a sentence I wrote in this file's own docstring: "a disclosure
# has a THIRD PARTY as the SUBJECT of the verb". That is true of GROOMING, which
# was the only kind I had examples of, and false of every kind where the CHILD
# is the subject -- "I want to die", "I've been skipping lunch", "I make myself
# throw up". I generalised a discriminator from one category to four.
#
# My verification concealed it rather than catching it: the probe that "covered"
# the other three kinds called `classify_disclosure` DIRECTLY and never went
# through this gate, so I tested the second stage and left the stage I had
# labelled THE CEILING untested for three of the four kinds. Every "must fire"
# case in the test file was grooming-shaped.
#
# Broad on purpose: first person plus any distress/body/food/harm term. The
# classifier discriminates; this only has to not throw the turn away.
_FIRST_PERSON = re.compile(
    # ⚠️ The window is SHORT and the first person must be the SUBJECT. An
    # unbounded window fired on "i have to write about how Romeo dies" -- and
    # the alternation below needs its trailing \b, because without one `die`
    # matched `dies`. Literature is full of death, so an unanchored version
    # turns every set text into a model call.
    r"\b(?:i|i'?m|im|i'?ve|ive)\s+(?:\w+\s+){0,4}?(?:"
    # ideation and self-harm
    r"die|dying|dead|kill (?:myself|me)|end it|end things|not exist|disappear"
    r"|not be here|dont want to be here|don'?t want to be here|wake up"
    r"|hurt(?:ing)? myself|harm(?:ing)? myself|cut(?:ting)? myself|cuts?"
    r"|worthless|hopeless|hate (?:myself|my life|being alive|existing)"
    r"|wish i (?:was|were)|nobody would (?:care|notice|miss)|burden"
    # eating and body
    r"|starv\w*|skip(?:ping|ped)? (?:breakfast|lunch|dinner|meals?)"
    r"|stopped eating|not eating|throw(?:ing)? up|vomit\w*|purg\w*"
    r"|too fat|so fat|disgusting|lose weight|calories|my weight|my body"
    # being hurt or targeted by others
    r"|bullied|picked on|beat(?:en)? up|jumped me|hit me|hits me|punch\w*"
    r"|kick(?:ed|s)? me|push(?:ed|es)? me|shov\w*|spit on|threatened"
    r"|scared to go|afraid to go|no ?one (?:talks|sits|lets) "
    r"|left out|excluded|made fun of|laugh(?:ed|ing) at me|rumou?r"
    # secrecy and fear about an adult
    r"|scared of|afraid of|cant tell anyone|can'?t tell anyone|too scared to tell"
    r")\b",
    re.IGNORECASE,
)

# Content self-evidently about the child's own safety, where the actor may be
# absent from the sentence entirely ("told to come alone and not tell anyone").
# A missing subject must not gate these out.
_STANDALONE = re.compile(
    r"\b(?:nudes?|sextort\w*|underwear|took my clothes|take off my"
    r"|meet (?:me |him |her |them )?alone|come alone|don'?t tell|not to tell"
    r"|keep (?:it|this|that|a|our|the) (?:a )?secret|our little secret"
    r"|without (?:my |his |her )?\w+ knowing|sneak(?:ing)? out"
    r"|groom(?:ed|ing)?|touched me|inappropriate(?:ly)?"
    # Social exclusion has an IMPERSONAL subject, so neither of the branches
    # above reaches it: "nobody lets me sit with them at lunch".
    r"|(?:nobody|no ?one)\s+(?:\w+\s+){0,2}(?:lets? me|talks? to me|sits? with me"
    r"|will let me|likes? me|picks? me)"
    r")\b",
    re.IGNORECASE,
)


def should_check_disclosure(text: str) -> bool:
    """True when this turn is worth one semantic disclosure call.

    Cheap: two regex searches, no model, no I/O. Fires on ~8% of real student
    turns.
    """
    if not text:
        return False
    return bool(
        _SUBJ_VERB.search(text)
        or _STANDALONE.search(text)
        or _FIRST_PERSON.search(text)
    )
