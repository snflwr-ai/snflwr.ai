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

COMPILED_PATTERNS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    category: [(re.compile(pat), desc) for pat, desc in entries]
    for category, entries in CATEGORY_PATTERNS.items()
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
