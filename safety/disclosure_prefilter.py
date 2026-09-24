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

⭐ The discriminator is that a disclosure has a THIRD PARTY as the SUBJECT of
the verb ("HE asked me", "my coach wants"), while a tutoring request has the
tutor as the implied object ("show ME"). Requiring the actor immediately before
the verb separates them, and is why v2/v3 cost 0.9%/8.0% instead of 32.5%.

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
    r"|post(?:s|ed)?|follow(?:s|ed)?|added|invite(?:s|d)?)"
)

# Subject-verb, tolerating an auxiliary or adverb or two: "he keeps asking",
# "my coach wants", "he said he would send".
_SUBJ_VERB = re.compile(rf"\b{_PERSON}\s+(?:\w+\s+){{0,2}}{_VERB}\b", re.IGNORECASE)

# Content self-evidently about the child's own safety, where the actor may be
# absent from the sentence entirely ("told to come alone and not tell anyone").
# A missing subject must not gate these out.
_STANDALONE = re.compile(
    r"\b(?:nudes?|sextort\w*|underwear|took my clothes|take off my"
    r"|meet (?:me |him |her |them )?alone|come alone|don'?t tell|not to tell"
    r"|keep (?:it|this|that|a|our|the) (?:a )?secret|our little secret"
    r"|without (?:my |his |her )?\w+ knowing|sneak(?:ing)? out"
    r"|groom(?:ed|ing)?|touched me|inappropriate(?:ly)?)\b",
    re.IGNORECASE,
)


def should_check_disclosure(text: str) -> bool:
    """True when this turn is worth one semantic disclosure call.

    Cheap: two regex searches, no model, no I/O. Fires on ~8% of real student
    turns.
    """
    if not text:
        return False
    return bool(_SUBJ_VERB.search(text) or _STANDALONE.search(text))
