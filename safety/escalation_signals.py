"""Escalate on the TUTOR'S OWN REFUSAL, not on classifying the child's input.

Measured 2026-09-12/13. Input classification of what a child says kept failing --
the child-input distribution is unbounded, and five separate pattern rounds each
fixed the phrasings just measured and missed the next:

    bullying requests, full input stack   1/8  = 12.5%
    risk disclosures, full input stack          38.5%

But the MODEL is the effective safety layer and it performs well: it refused 8 of
8 requests to help bully a named classmate, and redirected 15 of 17 risk
disclosures to a trusted adult. Its replies come from a stable system prompt, so
that distribution is NARROW -- which is exactly the condition under which
patterns work, and the condition child input never satisfied.

Detecting the refusal instead:

    risk disclosures        16/17 = 94.1%
    bullying requests        7/8  = 87.5%
    homework refusals        0/61 = 0%     (the tutor refuses homework constantly)
    genuine tutoring         0/37 = 0%

This NEVER blocks and never alters the reply. The child already got a good answer;
this exists so an adult finds out. Before it, a child could try to harass
classmates every day and no parent, teacher or record would ever exist --
analytics.py carries no transcript, so an unrecorded turn is invisible forever.
"""

import re
from typing import Optional

# A refusal that names harm to a PERSON is a safety refusal whatever the framing.
# This catches bullying dressed as coursework ("...for my English assignment"),
# which a homework-gate exclusion would wrongly filter out.
_PERSON_HARM = re.compile(
    r"(?:targets?|insults?|mean|hurtful|hurt|embarrass|humiliat)\w*\s+"
    r"(?:to\s+|toward\s+)?(?:another|other|someone|people|person|them)",
    re.IGNORECASE,
)

# Redirect to a CARE adult. Bare "teacher" is deliberately excluded: homework
# replies say "ask your teacher" and that is not a safety event (measured 4.9%
# of homework refusals).
_CARE_REDIRECT = re.compile(
    r"\b(?:parent|school counsel|counsellor|counselor|adult you trust|"
    r"trusted adult|doctor)\b",
    re.IGNORECASE,
)

# A bare refusal. Ambiguous on its own -- the tutor refuses homework with the same
# words -- so it only counts when the turn is not a homework request.
_GENERIC_REFUSAL = re.compile(
    r"\b(?:i can(?:'|no)?t help|i cannot help|i can(?:'|no)?t write|i cannot write"
    r"|not able to help|i can(?:'|no)?t assist)\b",
    re.IGNORECASE,
)

_AGE_PREFIX = re.compile(r"^\[Student age range:[^\]]*\]\s*")


def escalation_signal(user_text: str, reply: str, is_homework: bool) -> Optional[str]:
    """Why this turn should reach an adult, or None.

    ``is_homework`` comes from the homework gate that already ran on this turn.
    It only gates the ambiguous generic refusal; a refusal naming harm to a person
    escalates regardless, because bullying requests are sometimes framed as
    coursework.
    """
    body = _AGE_PREFIX.sub("", reply or "")
    if not body.strip():
        return None
    if _PERSON_HARM.search(body):
        return "refused_harm_to_person"
    if _CARE_REDIRECT.search(body):
        return "redirected_to_care_adult"
    if _GENERIC_REFUSAL.search(body) and not is_homework:
        return "refused_non_homework"
    return None
