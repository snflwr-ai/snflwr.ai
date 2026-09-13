"""Remove internal scaffolding the model narrates into its own reply.

The tutor system prompt tells the model that ``Each student message begins with
a hint like "[Student age range: 5-7]"`` and that it should *infer* the age when
no hint is present. Nothing in ``api/`` or ``core/`` ever prepends that hint, so
the model takes the second branch on every turn -- and then narrates the
inference back, opening its answer with ``[Student age range: 14-18]`` or, more
plainly, ``[Student age range: inferred 8-10]``.

Measured on sealed set 10 (2026-09-13): **26 of 49 served replies (53.1%)** began
with the tag. It reached the child because nothing strips it -- the OpenWebUI
filter defines an ``inlet`` and no ``outlet``, and the proxy serves upstream
``content`` verbatim.

The Modelfile now tells the model not to emit the tag, which is the real fix.
This module is the deterministic backstop, because a prompt instruction is a
probability and a child seeing internal scaffolding is a certainty when it
misses.

Only a *leading* tag is removed: mid-answer text that happens to match is left
alone, since the defect is specifically the model prefacing its reply.
"""

from __future__ import annotations

import re

# The hint the system prompt describes, as the model echoes it back. Tolerates
# the "inferred 8-10" form and any surrounding blank lines.
AGE_SCAFFOLD_PREFIX = re.compile(r"\A\s*\[Student age range:[^\]]*\]\s*")


def leading_scaffolding_len(text: str) -> int:
    """Number of leading characters that are scaffolding, or 0."""
    if not text:
        return 0
    match = AGE_SCAFFOLD_PREFIX.match(text)
    return match.end() if match else 0


def strip_scaffolding(text: str) -> str:
    """Return ``text`` without a leading age-range tag.

    Never raises and never returns ``None``: a non-string, or a reply that is
    *only* the tag, passes through unchanged rather than becoming empty -- an
    empty bubble is a worse failure than a visible tag.
    """
    if not isinstance(text, str):
        return text
    cut = leading_scaffolding_len(text)
    if not cut:
        return text
    remainder = text[cut:]
    return remainder if remainder.strip() else text
