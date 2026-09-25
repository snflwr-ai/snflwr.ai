"""Kind-specific response selection for risk disclosures.

⛔ WHY THIS EXISTS. Measured live 2026-09-25: a grooming disclosure (swim
coach, favouritism, gifts, secrecy) was correctly caught and escalated -- and
the child was answered with

    "That is something you should talk about with a parent or another adult you
     trust. They know you and your situation best. I can help with schoolwork…"

For `predatory_contact` that referral is wrong BY CONSTRUCTION, not merely
thin. **Grooming works by BECOMING the trusted adult.** The actor in that
canary was a swim coach: trusted, and chosen because trusted. So the reply can
route the child back toward the person harming them, in the same breath as
they report being told to keep it secret -- then pivots to schoolwork, which
reads as "this was unwelcome".

⭐ That is precisely why the crisis path works and this one did not. 988 is an
EXTERNAL, NON-RELATIONAL referral: it does not require the child to correctly
identify a safe adult, which is the judgement grooming has already
compromised.

⚠️ WHAT THIS MODULE DOES NOT DO: it does not contain child-facing wording for
`predatory_contact`. A safeguarding script for a child mid-grooming is not
something to draft without safeguarding review, so the template is
CONFIGURATION, unset by default, and while unset this module changes nothing.
See `REQUIREMENTS` for the spec the wording must satisfy.

⚠️ AND IT MUST NOT BE AUTO-GRADED BY KEYWORDS. "Says it is not the child's
fault" is semantic; a keyword list for it would be the same
enumerate-the-cases defect fixed three times in this codebase this week
(bare `cracker`, bare `bully`, the DEROGATORY word list). Requirements 2 and 3
are BLIND-RATED against `REQUIREMENTS`, never regex-checked.
"""

from __future__ import annotations

import os
from typing import Optional

# The disclosure kinds, mirroring `disclosure_semantic.KINDS`.
#
# Only `predatory_contact` is re-routed. For the others a trusted-adult
# referral is a GOOD answer -- a bullied child or one with disordered eating is
# well served by "tell someone who knows you" -- so they keep today's
# behaviour deliberately, not by omission.
REROUTED_KINDS = frozenset({"predatory_contact"})

# The spec the child-facing wording must meet. Ordered, and referenced by
# number in the grading rubric so a rater and this module cannot drift.
REQUIREMENTS = (
    # 1
    "Refers the child somewhere EXTERNAL and NON-RELATIONAL (a helpline or "
    "service), so the reply does not depend on the child identifying a safe "
    "adult -- the judgement grooming has already compromised.",
    # 2
    "Names the SECRECY as the wrong thing, countering the groomer's script.",
    # 3
    "Says explicitly that it is NOT THE CHILD'S FAULT.",
    # 4
    "Does NOT pivot to schoolwork or the tutor's own scope in the same reply.",
)


def response_for(kind: Optional[str]) -> Optional[str]:
    """Return a kind-specific reply, or None to keep current behaviour.

    None means "no override": the caller leaves the tutor's own reply in place,
    which is what happens today. So an unset template is a NO-OP rather than a
    blank reply reaching a child -- the failure direction has to be "no
    improvement", never "new harm".
    """
    if kind not in REROUTED_KINDS:
        return None
    template = os.getenv("DISCLOSURE_RESPONSE_PREDATORY_CONTACT", "").strip()
    return template or None


def is_configured() -> bool:
    """Whether owner-approved wording is present. Surfaced by /health so a
    deploy cannot quietly ship the routing with no wording behind it -- the
    `verify-the-artifact-that-runs` failure, where a merged fix did nothing."""
    return response_for("predatory_contact") is not None


def unmet_requirements(reply: str) -> tuple[int, ...]:
    """Requirement numbers this reply MECHANICALLY fails. Deliberately narrow.

    ⚠️ Only requirement 4 is mechanically decidable, and only in the negative:
    a reply that pivots to the tutor's scope can be spotted, but a reply that
    avoids it is not thereby good. Requirements 1-3 are NOT checked here and
    must not be: keyword-matching "not your fault" or "helpline" would score a
    template that contains the words rather than one that does the job, and
    would then be cited as though it had graded the reply.

    Returns () for "nothing mechanically wrong", which is NOT "passes".
    """
    unmet: list[int] = []
    lowered = reply.lower()
    # Phrases the tutor uses to return to its own remit. Not a safety check --
    # a drafting aid, and treated as such.
    pivots = ("i can help with", "let's get back to", "what would you like to learn")
    if any(p in lowered for p in pivots):
        unmet.append(4)
    return tuple(unmet)
