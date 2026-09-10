"""COMPILED_PATTERNS (compiled from CATEGORY_PATTERNS) and SEVERITY_MAP.

Extracted verbatim from the former safety/patterns.py.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from safety.patterns.category_patterns import CATEGORY_PATTERNS

# ============================================================================
# Compiled pattern cache (built once at import time)
# ============================================================================

# Order matters: the matcher walks these in order and returns on the FIRST hit,
# so this dict's key order IS the category precedence.
#
# Until 2026-09-09 that precedence was an accident of where each block happened
# to be written in category_patterns.py, and VIOLENCE sat above SELF_HARM. The
# visible cost: "what is the easiest way to die" and "how do people kill
# themselves" were blocked as VIOLENCE. Blocking is right, but the child then
# got a generic refusal with no helpline, and the incident was logged — and
# escalated to the parent — as violence rather than self-harm.
#
# SELF_HARM now runs first, unconditionally. A message that looks like both a
# crisis and something else is a crisis: that is the reading with the survivable
# error. Everything after it keeps its previous relative order, so this changes
# nothing except the one case it is meant to change.
CATEGORY_PRECEDENCE: Tuple[str, ...] = ("SELF_HARM",)

_ORDERED_CATEGORIES = [c for c in CATEGORY_PRECEDENCE if c in CATEGORY_PATTERNS] + [
    c for c in CATEGORY_PATTERNS if c not in CATEGORY_PRECEDENCE
]

COMPILED_PATTERNS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    category: [(re.compile(pat), desc) for pat, desc in CATEGORY_PATTERNS[category]]
    for category in _ORDERED_CATEGORIES
}

# ============================================================================
# Severity mapping
# ============================================================================

SEVERITY_MAP: Dict[str, str] = {
    "HATE_SPEECH": "high",
    "SEXUAL_CONTENT": "high",
    "VIOLENCE": "high",
    "SELF_HARM": "high",
    "PROFANITY": "medium",
    "DRUGS": "medium",
    "DEROGATORY": "medium",
}
