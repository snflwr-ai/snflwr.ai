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
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Normalization. The clean-probe measurement (2026-09-12) found the gate caught
# only 47% of independently-written homework-dodge asks, and 1 of 10 from K-2
# students. The cause was register, not subtlety: a six-year-old writes "anser",
# a teenager writes "can u", and neither reaches a pattern spelled "answer" or
# "can you". Normalizing the surface fixes a CLASS of misses rather than the
# individual phrasings that exposed it.
# ---------------------------------------------------------------------------

# Texting shorthand -> words. Whole-token replacement only.
_SHORTHAND = {
    "u": "you",
    "ur": "your",
    "pls": "please",
    "plz": "please",
    "plese": "please",
    "4": "for",
    "2": "to",
    "bc": "because",
    "cuz": "because",
    "rn": "right now",
    "hw": "homework",
    "asap": "right now",
    "tmrw": "tomorrow",
    "thx": "thanks",
    "dont": "don't",
    "cant": "can't",
    "im": "i'm",
    "wat": "what",
    "wut": "what",
    "n": "and",
    "r": "are",
    "y": "why",
    "tho": "though",
    "abt": "about",
}

# Words worth repairing when a student misspells them. Only these -- a general
# spell-corrector would rewrite the student's own content.
_KEY_WORDS = (
    "answer answers question questions problem problems spelling spell word words "
    "homework worksheet assignment sentence sentences paragraph paragraphs essay "
    "summary conclusion thesis science history english reading writing correct "
    "number numbers solution solutions finish write tomorrow because teacher"
).split()


# Real English words that the repair above would otherwise destroy: "work" is
# within 0.72 of "word", "these" of "thesis", "base" of "because". Derived by
# running _repair over /usr/share/dict/words and keeping every real word it
# changed into something that is not its own stem -- an inflection collapsing
# onto its stem ("answering" -> "answer") is correct and stays. Regenerate with
# scripts/derive_trigger_stoplist.py after changing _KEY_WORDS or the threshold.
_NEVER_REPAIR = frozenset("""
    abner abstinent alignment alignments alinement anger angers anise
    antwerp anuses arraignment arraignments assigned assigning aster asters
    awes base beau beaus became bemuse briefcase collision collusion
    concession concessions conciliation concludes concluding conclusive
    conclusively concubine concussion concussions confusing confusion
    confusions contusing contusion contusions convolution convolutions
    convulsion convulsions core cornet correlate corset egis elfish elisha
    elvish emissary engels enlist fiendish finch finds fines finises finks
    finnish finns fins fish fishy furnish hailstorm histogram historian
    historic histories homer homeward homewrecker housework nimbler
    novembers nubs nuder numbed numberless numbest numbness numbs numeral
    numerals numerates numeric numerous nuremberg parables parole parolees
    paroles petroleum poem poems pole polemics poles potables probable
    probables probates probe probed probes problematic profiles prom proms
    provable prowlers queasiness quentin questing questionable questionably
    questioners quests quieting quotations racing radiating radioing raging
    raiding rain raking rang raping raring rating raving razing reaching
    reacting readiness readying realizing reaming reaping rearing rearming
    reasoning rebating rebinding recalling recanting recapping recasting
    receding recording reddening redding redeeming redoing redrawing
    reducing refunding regain regaining regaling regarding rehabbing
    rehashing reheating rein relapsing relating relaxing relaying releasing
    reloading remain remaining remaking remanding remarking remedying
    reminding renaming rendering rending renegading repairing repaying
    repealing repeating replacing replaying rereading residing restating
    retailing retain retaining retaking retarding retracing retreading
    revaluing revamping revealing rewarding rewinding rewording riding ring
    salience sallying salutation salutations saluting sampling sapience
    sapling saplings satelliting scene scenes scenic schelling sconce
    scouting sculling scummy sealing seductions seedling selections sell
    seller selling sells senates senecas sentencing sentience sentient
    sentiments sentinel sentinels sequence sequencers sequences settees
    settling seventeens severances shell shellacking shelling shells shelly
    shelving shilling shouting shovelling shrivelling silence silenced
    silencer silencers silences since sine situations sixpence sixpences
    sling smarmy smell smelling smells smelly smelting snell snivelling
    snorkelling solon speaking spearing speccing specking speckling
    speculating speeding spellbind spellbinder spellbinds spelt spence
    spending spewing spiel spieling spiels spill spilling spills spiralling
    splaying splicing spoiling spoliation spooling spouting spreeing
    stalling stances stapling stealing steeling stella stenches stencilling
    sterling stilling suction suctions sugary sullying summaries summarily
    summarize summations summer summery summitry sweeteners swell swelling
    swellings swells swilling swivelling teachable teaches tear teaser tech
    tess tessa testis tests tether thais thatcher thees their theirs theism
    theist theists these theses theseus thespians thespis thins this tracer
    treachery tress waite waited waiter waiting ward wards warier waring
    warlord warlords weighting whistling white whiten whitening whiter
    whites whiting whitings whittling whores whorled whorls wilford wiling
    wilted wilting wing wining winter wintering wiping wireds wirier wiring
    wit witching wither withering within witting wittingly wood woodard
    woods woodsy wooers wordiest wordings wore work workday workdays worked
    works world worldly worlds worm wormed worms worn worried worse worsen
    worst worsted worsting worsts woulds wounds wraith wreathing wresting
    wrestling wrier wriest wriggling wright wring wringing wrings wrinkling
    wrist wrists writable writhe writhed writhes writhing writs written
    wrote
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _repair(token: str) -> str:
    """Map one token to a key word when it is a plausible misspelling of it."""
    if token in _SHORTHAND:
        return _SHORTHAND[token]
    if len(token) < 3 or token in _KEY_WORDS or token in _NEVER_REPAIR:
        return token
    best, best_score = token, 0.0
    for kw in _KEY_WORDS:
        # A cheap length guard keeps this to a handful of real comparisons.
        if abs(len(kw) - len(token)) > 3 or kw[0] != token[0]:
            continue
        score = SequenceMatcher(None, token, kw).ratio()
        if score > best_score:
            best, best_score = kw, score
    return best if best_score >= 0.72 else token


def normalize(user_text: str) -> str:
    """Lowercase, expand texting shorthand, repair misspelled key words.

    Used only for MATCHING. The student's text is never rewritten for any other
    purpose, and the repair vocabulary is deliberately tiny.
    """
    text = (user_text or "").lower()
    return _TOKEN_RE.sub(lambda m: _repair(m.group(0)), text)


# ---------------------------------------------------------------------------
# Tier 1: a demand for the finished product. These fire on their own.
# Matched against normalize(text), so patterns are written in canonical spelling.
# ---------------------------------------------------------------------------

# Nouns naming a thing the STUDENT was assigned to hand in.
_DELIVERABLE = (
    r"(?:essay|paragraphs?|reports?|papers?|thesis(?: statement)?|topic sentences?|"
    r"conclusions?|summary|summaries|sentences?|answers?|introductions?|intros?|"
    r"analysis|responses?|reflections?|entry|entries|scripts?|outlines?|letters?|"
    r"speech|captions?|translations?|journals?|worksheets?|homework|assignments?|"
    r"(?:chemical |balanced |net ionic )equations?)"
)
# Verbs that mean "produce it", in the tenses students actually type.
_PRODUCE = (
    r"(?:write|writing|wrote|do|doing|did|solve|solving|solved|finish|finishing|"
    r"finished|summarize|summarise|translate|calculate|compute|factor|answer|"
    r"answering|fill|draft|drafting|drafted|generate|make|type|list|fix|give|say)"
)

_DEMAND = [
    # "just tell me", "just give me the answer", "please just say it"
    r"just (tell|give|say|write|show) (me|it)\b",
    r"just (tell|give) me\b",
    r"just the (answer|value|number|formula|word|sentence|result|solution)s?",
    r"\bonly the (answer|value|number|formula|word|result|solution)s?\b",
    # "give me the sentence to write", "tell me the answer", "say the word"
    r"(give|tell|write|spell|say|generate|send|show) (me )?(the|a|an) (\w+ ){0,3}"
    r"(answer|value|number|formula|word|sentence|result|solution|definition|"
    r"right (number|answer))s?",
    r"\bgive me (one|it|them|these|the rest)\b",
    r"\b(give|send|tell|show|hand) (it|them|that|these|those) to me\b",
    # delegation: "...for me". Anchored on "for me" so "solve for x" is safe.
    rf"\b{_PRODUCE}\b[^.?!]{{0,60}}\bfor me\b",
    r"\bfor me\b[^.?!]{0,40}\b(so i can|to copy|to hand in|to turn in)\b",
    r"do my (\w+ ){0,3}(homework|assignment|essay|paper|worksheet|project|lab|"
    r"report|problems?|questions?)",
    # copy / paste / hand-in intent
    r"so i can (copy|paste|write it down|hand it in|turn it in|write that down|"
    r"enter|put|submit)",
    r"i can (just )?(copy|paste|edit)\b",
    r"\b(paste|copy) it in\b",
    r"before i (turn|hand) it in\b",
    r"\b(so i|and i'?ll|i'?ll) (can )?(just )?(use|submit|turn in|hand in) (it|that|them)\b",
    # fill-in-the-blank framings
    r"fill (it |them |these )?in\b",
    r"fill in the blanks?\b",
    r"that goes in the blank\b",
    # explicit refusal of the teaching
    r"(don'?t|do not) (want the steps|explain|show me|teach)",
    r"no (working|steps|explanation)",
    r"without (the )?steps",
    r"stop explaining\b",
    r"i'?m done thinking",
    r"i (already )?(understand|get) it,? i just need",
    r"i didn'?t (finish|read|do) it\b",
    r"\bi skipped (it|the (book|reading|chapter))\b",
    # cheating said out loud
    r"\bcheat\b",
    r"\bfake (essay|paper|report|reflection|summary|entry)\b",
    r"(make it |so it )?(sound|look) like i (actually |really )?"
    r"(read|did|wrote|understood|got)\b",
    r"don'?t want (my teacher|anyone) to know\b",
    # "Can you write it?", "can you answer these", "can you list them all"
    r"\bdo the rest of (my|the)\b",
    # "I need the final number." / "I just want the answers."
    r"\bi (just |really |only )?(need|want) the (\w+ ){0,2}"
    r"(answer|number|value|result|solution|word|sentence|definition)s?\b",
    # interrogative demand: "what is the right answer for question 2"
    r"\bwhat('?s| is) the (\w+ ){0,2}" r"(answer|word|number|solution|definition)s?\b",
    rf"\bi (just )?need (a|an|the) (\w+ ){{0,3}}{_DELIVERABLE}\b",
    rf"\bi (just )?need you to {_PRODUCE}\b",
    r"\bi can'?t find the (answer|word|number|solution)s?\b",
    r"not how to get there|not the steps|skip the steps",
    # explicit refusal to do the work
    r"(don'?t|do not|dont) want to (work it out|do it|do the work|figure it out"
    r"|think about it|work them out)",
    # copy intent, broader surface than "so I can copy"
    r"so i can (put|write|stick) (it|that|them) (on|in|down)",
    r"(put|write) (it|that|them) on my (sheet|paper|worksheet|homework)",
]


# ---------------------------------------------------------------------------
# Tier 1b: the student STATES the work in full and asks for its result.
#
# Measured 2026-09-12 on probes where the student can actually be answered
# (states the equation, names the book), trigger recall was 71.9% and every miss
# shared one shape: an assigned question typed out VERBATIM, in neutral academic
# language. "What is the derivative of f(x) = 3x^4 - 2x^3 + 5x - 7?" carries no
# demand marker and no assignment vocabulary -- it is indistinguishable, as text,
# from a textbook question. Those misses produced 4 of the 5 reveals that reached
# a student.
#
# Firing here is cheap and firing wrongly is cheaper than missing: the trigger
# only decides whether to CHECK the answer. A genuine learner who states their
# problem gets a guiding answer, which passes the confirm unchanged. The cost of
# a false fire is one ~0.9s check; the cost of a miss is a child handed the
# answer to their homework.
# ---------------------------------------------------------------------------

# The turn contains the actual work, not a reference to work the tutor cannot see.
_STATED_WORK = re.compile(
    r"\bhere is (the|my|this) (problem|question|equation|paragraph|experiment|data|prompt)\b"
    # "this statistics problem:", "this reaction:", "this paragraph:" -- the colon
    # introduces the work itself rather than pointing at something unseen.
    r"|\bthis (\w+ ){0,2}(problem|question|reaction|passage|paragraph|equation|"
    r"sentence|phrase|concept|prompt)\s*[:\"\u201c']"
    # a chemical reaction written out in words
    r"|\b\w+ reacts with \b"
    # any quoted block of real length -- the student has pasted their own text or
    # the assignment's text into the turn
    r"|[\"\u201c\u2018][^\"\u201d\u2019]{25,}[\"\u201d\u2019]"
    # a pasted assignment prompt, in quotes, carrying an assignment imperative
    r"|[\"\u201c\u2018']\s*(describe|analyz|analys|explain|compare|discuss|evaluate|"
    r"identify|summariz|summaris|outline|write|argue)[^\"\u201d\u2019']{15,}"
    # a mathematical expression: 3x^4, 2y + 5 = 17, 456 * 23, f(x)
    r"|\b[a-z]?\(?[a-z]\)?\s*[\^]\s*\d"
    r"|\d\s*[-+*/^=]\s*\d"
    r"|\b\d+\s*(kg|cm|mm|km|ml|m/s|g\b|inches|inch|feet|miles|hours|minutes|seconds|"
    r"degrees|moles?|grams?)\b",
    re.IGNORECASE,
)

# The student has already attempted it and wants their work checked. This is the
# single shape that every false positive from the stated-work rule shared, and it
# is the opposite of a demand: the work exists, they want to know if it is right.
_OWN_ATTEMPT = re.compile(
    r"\bi (tried|solved|calculated|computed|wrote|added|subtracted|multiplied|divided|"
    r"moved|dropped|put|got|balanced|factored|graphed|converted|drew|measured|set up|"
    r"think the answer|believe the answer|came up with|already (did|solved|tried))\b"
    r"|\bi think i (did|got) (it|this|that) right\b"
    r"|\bi did (it|this) (right|correctly)\b"
    r"|\bmy (answer|work|attempt|solution|reasoning|calculation)\b"
    r"|\bhere is my (work|answer|attempt|solution|draft)\b"
    r"|\bis (this|that|my answer|my work) (right|correct|ok)\b",
    re.IGNORECASE,
)

# ...and asks for the RESULT of it.
_RESULT_REQUEST = re.compile(
    r"\bwhat (is|are|was|were) the\b"
    r"|\b(calculate|compute|determine|find|solve|provide|state|list|rewrite|translate)\b"
    r"|\bhow (many|much|far|long|fast)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tier 2: assignment context AND a bare imperative/answer demand. Neither half
# is sufficient -- a worksheet mention alone is not a homework REQUEST, and a
# bare imperative alone is often legitimate ("Explain entropy").
# ---------------------------------------------------------------------------
_CONTEXT = re.compile(
    r"\b(worksheet|homework|assignment|take-?home|quiz|problem set|reading log|"
    r"study guide|lab report|my lab\b|packet|due (at|by|tomorrow|tonight|in)|"
    r"annotation homework|spelling list|exit ticket|pop quiz|"
    r"(question|problem|page|number|chapter) \d+|\d+(st|nd|rd|th) (question|problem)|"
    r"my (math |spelling |reading |science )?(sheet|paper|book|list)\b|"
    r"turn (it|this) in|hand (it|this) in|graded|for my \w+ class)\b",
    re.IGNORECASE,
)
_BARE_DEMAND = re.compile(
    r"\b(just|only|quick(ly)?|please just)\b.{0,30}\b(say|tell|give|write|need)\b"
    r"|\b(what|which|who|when|where|how many|how much)\b[^?]{0,70}\?"
    r"|\b(write|summarize|summarise|translate|solve|give|tell|spell|fill|"
    r"generate|draft)\b",
    re.IGNORECASE,
)

# A genuine learner in a school register trips tier 2 constantly. These phrases
# mark the turn as wanting UNDERSTANDING, and veto tier 2 (never tier 1).
_LEARNING_INTENT = re.compile(
    r"\b(i (don'?t|do not|dont) (understand|get|know why|see why|see how)|"
    r"i want to understand|i need to understand|help me understand|"
    r"why (does|do|is|are|did|would|can'?t)|how (does|do|did) (it|this|that|they)|"
    r"can you explain|explain (why|how|what)|what does .{0,40} mean|"
    r"i'?m confused about|i am confused about|where did i (go wrong|mess up)|"
    r"is my (answer|work|reasoning|logic|thinking) (correct|right|sound)|"
    r"did i do (this|it|that) right|what am i missing|"
    r"conceptual explanation|step-by-step logic|the logic behind|"
    r"give me a hint|just a hint|where (do i|to) start|how do i start|"
    # "How do I solve 3x + 5 = 20?" asks for the METHOD. Contrast "What is the
    # perimeter of a rectangle with length 12 and width 5?", which asks for the
    # PRODUCT -- that distinction is what the stated-work rule turns on.
    r"how (do|would|can|should) i\b|"
    r"point me in the right direction|walk me through|"
    r"am i on the right track)\b",
    re.IGNORECASE,
)

# Naming a deliverable next to a produce-verb is strong but not conclusive: a
# student describing what was assigned ("my teacher assigned us to write a
# paragraph", "I am doing a biology worksheet") uses the same words as one
# demanding it. These yield to _LEARNING_INTENT; the tier-1 list above does not.
_SOFT_DEMAND = [
    rf"{_PRODUCE} (me )?(my|the|a|an) (\w+ ){{0,3}}{_DELIVERABLE}\b",
    rf"can you (just )?{_PRODUCE}\b[^?]{{0,40}}"
    r"\b(it|this|that|them|these|the rest|mine|all of them)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _DEMAND]
_COMPILED_SOFT = [re.compile(p, re.IGNORECASE) for p in _SOFT_DEMAND]


def is_homework_request(user_text: str) -> bool:
    """True if the turn asks the tutor to PRODUCE the student's assigned answer."""
    text = normalize(user_text)
    if any(p.search(text) for p in _COMPILED):
        return True
    # A turn that says outright it wants to understand is not a demand for the
    # product, however much assignment vocabulary it happens to carry.
    if _LEARNING_INTENT.search(text):
        return False
    # Work stated in full plus a request for its result -- but only when the
    # student has NOT shown an attempt of their own. Sits below the learning-intent
    # veto on purpose: stating a problem and asking to understand it is tutoring.
    if (
        _STATED_WORK.search(text)
        and _RESULT_REQUEST.search(text)
        and not _OWN_ATTEMPT.search(text)
    ):
        return True
    if any(p.search(text) for p in _COMPILED_SOFT):
        return True
    # Context alone never fires; it must be paired with a demand.
    return bool(_CONTEXT.search(text) and _BARE_DEMAND.search(text))
