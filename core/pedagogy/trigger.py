"""Detect whether a student turn is a request to DO their assigned task
(produce the finished answer) rather than a genuine 'how/why does this work?'.

RECALL IS THE POINT. This gate decides whether the guidance enforcer looks at a
turn at all, so a miss means the pedagogy protection never runs and a revealed
answer ships to a child. A false positive costs one ~0.9 s confirm call that
returns "no_reveal" and changes nothing. Those are not comparable costs.

MEASURED 2026-09-11. The original pattern list documented itself as
"recall-favoring" but measured 56% recall / 0% false positives against 32
homework probes and 38 genuine learning questions -- precision-tuned, and
inert on 44% of realistic homework asks. It missed "Write the conclusion for my
lab", "fill in the blank on my worksheet", "Solve 4x + 7 = 31 for me" and
"it's one blank on a take-home quiz".

THE DISCRIMINATOR is a demand for a finished PRODUCT, not the presence of
school context. These are all genuine learning questions and must NOT match:
    "For my English class, why does Romeo kill himself at the end?"
    "How do I solve 3x + 5 = 20 for x? I keep getting stuck after the 5."
    "How do I figure out the theme of a novel we're reading in class?"
So assignment nouns (worksheet, quiz, homework) never fire on their own -- they
only count alongside a demand.
"""

import re

# ---------------------------------------------------------------------------
# Tier 1: a demand for the finished product. These fire on their own.
# ---------------------------------------------------------------------------
_DEMAND = [
    # "just tell me", "just give me the answer", "please just say it"
    r"just (tell|give|say|write|show) (me|it)\b",
    r"just (tell|give) me\b",
    r"just the (answer|value|number|formula|word|sentence|result|solution)",
    r"\bonly the (answer|value|number|formula|word|result|solution)\b",
    # "give me the sentence to write", "just write the word for me"
    r"(give|tell|write|spell) (me )?(the|a) (answer|value|number|formula|word|"
    r"sentence|result|solution|right (number|answer))",
    # delegation: "...for me". Anchored on "for me" so "solve for x" is safe.
    r"\b(do|solve|write|finish|summarize|summarise|translate|calculate|compute|"
    r"factor|answer|fill)\b[^.?!]{0,60}\bfor me\b",
    r"\bfor me\b[^.?!]{0,40}\b(so i can|to copy|to hand in|to turn in)\b",
    # "write my essay", "write the conclusion for my lab", "write my thesis"
    r"write (my|the) (\w+ ){0,3}(essay|paragraph|report|thesis|conclusion|"
    r"summary|sentence|answer|introduction|intro|analysis|response)\b",
    r"do my (homework|assignment|essay|paper|worksheet|project|lab)",
    # copy / paste intent
    r"so i can (copy|paste|write it down|hand it in|turn it in|write that down)",
    r"i can (just )?(copy|paste)\b",
    r"\b(paste|copy) it in\b",
    # fill-in-the-blank framings
    r"fill (it |them |these )?in\b",
    r"fill in the blanks?\b",
    # explicit refusal of the teaching
    r"(don'?t|do not) (want the steps|explain|show me|teach)",
    r"no (working|steps|explanation)",
    r"without (the )?steps",
    r"i'?m done thinking",
    r"i (already )?(understand|get) it,? i just need",
    r"i didn'?t (finish|read|do) it\b",
    # "Can you write it?", "can you do the rest of my math problems"
    r"can you (just )?(write|do|solve|finish|answer|type)\b[^?]{0,40}"
    r"\b(it|this|that|them|these|the rest|mine)\b",
    r"\bdo the rest of (my|the)\b",
    # "I need the final number, not how to get there."
    r"\bi (just )?need the (final |right |correct |actual )?"
    r"(answer|number|value|result|solution|word|sentence)\b",
    r"not how to get there|not the steps|skip the steps",
    # explicit refusal to do the work: "I dont want to work it out"
    r"(don'?t|do not|dont) want to (work it out|do it|do the work|figure it out"
    r"|think about it|work them out)",
    # copy intent, broader surface than "so I can copy"
    r"so i can (put|write|stick) (it|that|them) (on|in|down)",
    r"(put|write) (it|that|them) on my (sheet|paper|worksheet|homework)",
]

# ---------------------------------------------------------------------------
# Tier 2: assignment context AND a bare imperative/answer demand. Neither half
# is sufficient -- a worksheet mention alone is not a homework REQUEST, and a
# bare imperative alone is often legitimate ("Explain entropy").
# ---------------------------------------------------------------------------
_CONTEXT = re.compile(
    r"\b(worksheet|homework|assignment|take-?home|quiz|problem set|reading log|"
    r"study guide|lab report|my lab\b|packet|due (at|by|tomorrow|tonight)|"
    r"annotation homework|spelling list)\b",
    re.IGNORECASE,
)
_BARE_DEMAND = re.compile(
    r"\b(just|only|quick(ly)?|please just)\b.{0,30}\b(say|tell|give|write|need)\b"
    r"|\b(what|which|who|when|where|how many|how much)\b[^?]{0,70}\?"
    r"|\b(write|summarize|summarise|translate|solve|give|tell|spell|fill)\b",
    re.IGNORECASE,
)

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _DEMAND]


def is_homework_request(user_text: str) -> bool:
    """True if the turn asks the tutor to PRODUCE the student's assigned answer."""
    text = user_text or ""
    if any(p.search(text) for p in _COMPILED):
        return True
    # Context alone never fires; it must be paired with a demand.
    return bool(_CONTEXT.search(text) and _BARE_DEMAND.search(text))
