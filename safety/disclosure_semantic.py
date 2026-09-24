"""Semantic disclosure classification, behind the cheap prefilter.

WHY THIS EXISTS, measured rather than assumed. On a peer session's held-out
sealed set of 22 disclosures + 10 clean
(`~/snflwr-artefacts/2026-09-24-disclosure-sealed/RESULT-no-semantic-backstop.md`):

    regex `detect_disclosure`      6 / 22
    llama-guard (the harm layer)   2 / 22   <- and 3 FALSE BLOCKS on 10 clean
    either                         7 / 22

Two whole categories -- secrecy with no meeting, and a trusted adult as the
actor -- are **0/4 by either net**. 15 of 22 disclosures reach no adult at all.

⚠️ llama-guard's 2/22 is not a weak classifier, it is the WRONG INSTRUMENT, and
the disclosure module's own docstring predicted it on day one: a harm
classifier asks "is this content harmful?", and a child reporting grooming is
the victim describing it, not producing it. That is why this file exists
separately instead of tuning the harm layer.

Three regex rounds in one day went 7/7 -> 24/24 -> 6/22 depending only on who
wrote the set. Hand-enumerated patterns get precision (0 false alerts on clean)
and lose recall to every natural rephrasing. So the regex stays as the
deterministic FLOOR and this adds meaning on top; it does not replace it.

⚠️ NOT YET WIRED INTO THE SERVING PATH. No arm has been measured and none has
been certified against a cold sealed set, and `judge-certified-per-class` is on
record here: llama-guard's 2/22 above is exactly what an uncertified
off-construct judge looks like. Wiring happens after certification, not before.
"""

import logging
import os
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ⭐ PINNED, and deliberately not read from the tutor/backbone setting.
# `safety-check-must-not-follow-the-backbone`: a safety classifier that
# defaults to the main model degrades SILENTLY on a backbone swap -- the call
# still succeeds, still returns a verdict, and the verdict is just worse. The
# literal default here means a backbone change cannot move this classifier
# without someone editing this line or setting the env var on purpose.
#
# ⚠️ The value below is a PLACEHOLDER pending the arm measurement. Two arms are
# to be compared before anything ships:
#   * `gemma4:e4b` on CPU  -- independent of the GPU and of the card contention
#     that `ironclaw-evicts-snflwr-tutor-from-gpu` records
#   * the resident 31b     -- p50 1.6s / p90 4.4s per
#     `confirm-on-the-resident-tutor`, but it is the backbone, so pinning to it
#     re-creates the coupling this constant exists to prevent
DISCLOSURE_MODEL: str = os.getenv("DISCLOSURE_MODEL", "gemma4:e4b")

# Kinds this returns. Must match `disclosure_detector.Disclosure.kind` so both
# nets feed one escalation path and one incident vocabulary.
KINDS = (
    "predatory_contact",
    "suicidal_ideation",
    "bullying_victim",
    "disordered_eating",
)

CLASSIFIER_SYSTEM = (
    "You are a child-safeguarding classifier for a K-12 tutoring service. "
    "You do not talk to children and you never write a reply to one. "
    "You output one JSON object and nothing else."
)

# The construct is DISCLOSURE, not harm. Spelled out because getting this wrong
# is precisely how llama-guard scores 2/22: it is asked whether the text is
# harmful, and a child describing what an adult did to them is not.
_PROMPT = """A child using a homework tutor sent the message below. Decide whether the child is DISCLOSING something an adult in their life needs to be told about.

You are NOT judging whether the message is harmful, rude, or against the rules. The child is the possible VICTIM here, not the offender. A calm, polite, well-spelled message can be a disclosure.

Answer with one of these kinds:

- "predatory_contact": someone is grooming, sexualising, isolating or extorting the child. Includes: asking for sexual images; sending them; arranging to meet alone, at a hotel, or in secret; offering money, gift cards or game currency for pictures; telling the child to keep contact secret or not tell their parents; threatening to share images.
  ⚠️ The person may be someone the child knows and names plainly -- "he", "my coach", "my teacher", "my friend's dad", "my babysitter's boyfriend". A named, trusted adult is the MOST common real case, not a reason to dismiss it. Secrecy about contact is a signal, never an excuse.
- "suicidal_ideation": the child expresses wanting to die, to disappear, to not exist, or to hurt themselves.
- "bullying_victim": the child is being bullied, excluded, humiliated or targeted by others.
- "disordered_eating": the child describes starving themselves, purging, or compulsive restriction.
- "none": no disclosure.

Answer "none" for ordinary schoolwork, including when it mentions people, meetings, secrets, bodies or sex as SUBJECT MATTER. All of these are "none":
- "send me a picture of a cell diagram for my biology homework"
- "my teacher asked me to show my work"
- "show me images of sexual reproduction in plants"
- "we are meeting at the hotel for the robotics competition"
- "my mom asked me to keep a secret about the surprise party"
- "why does Romeo keep his marriage a secret in the play"
- "which planets are visible to the naked eye"

The distinction is whether the CHILD is describing something happening to THEM, not whether the topic sounds serious.

"you" in the child's message means THIS TUTORING SERVICE, not a third person. A child asking you to meet them, help them privately, or keep something between the two of you is "none" -- there is no other person in that sentence. Only a THIRD party can be the subject of a disclosure.

Child's message:
<<Q>>

Reply with exactly this JSON and nothing else:
{"kind": "<one of predatory_contact, suicidal_ideation, bullying_victim, disordered_eating, none>", "why": "<at most 12 words>"}"""

_JSON = re.compile(r'"kind"\s*:\s*"([a-z_]+)"')


class DisclosureClassifierUnavailable(RuntimeError):
    """The semantic pass could not produce a verdict.

    Raised rather than swallowed. The caller must decide the outage path, and
    that decision is NOT this module's to make silently:

      * failing "closed" for a disclosure detector means ESCALATING on every
        error, which spams parent alerts during any outage and trains the
        parent to ignore them -- the failure mode `clean-but-useless-is-invisible`
        and the rubric work both keep running into
      * failing "open" means a silent miss, which is the thing this whole
        subsystem exists to stop

    The intended caller behaviour is NEITHER: fall back to the deterministic
    regex result, and record a distinct incident so a reviewer can see the
    semantic pass was down. `fail-closed-branch-was-dead-code` is on record
    here -- a swallowed exception made a reasoned fail-closed branch
    unreachable for the failure that actually happens.
    """


def _parse(raw: str) -> Optional[str]:
    """Pull the kind out of a model reply, or None if it is not parseable."""
    if not raw:
        return None
    m = _JSON.search(raw)
    if not m:
        # Tolerate a bare word, which small models emit when they drop the JSON.
        bare = raw.strip().strip('".').lower()
        return bare if bare in KINDS or bare == "none" else None
    kind = m.group(1).lower()
    return kind if kind in KINDS or kind == "none" else None


async def classify_disclosure(
    text: str,
    generate: Callable[[str], Awaitable[str]],
) -> Optional[str]:
    """Return a disclosure kind, or None for no disclosure.

    `generate` is injected so this module holds no client, no model resolution
    and no timeout policy -- the caller owns those, as the reveal confirm does.

    Raises DisclosureClassifierUnavailable when the model errors or returns
    something unparseable. It does NOT return "no disclosure" in that case:
    that conflation is how a safety layer fails silently.
    """
    prompt = _PROMPT.replace("<<Q>>", text or "")
    try:
        raw = await generate(prompt)
    except Exception as exc:  # noqa: BLE001 -- re-raised as our own type
        raise DisclosureClassifierUnavailable(str(exc)) from exc
    kind = _parse(raw)
    if kind is None:
        raise DisclosureClassifierUnavailable(f"unparseable verdict: {raw[:120]!r}")
    return None if kind == "none" else kind
