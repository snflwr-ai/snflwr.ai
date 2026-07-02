"""safety/patterns — single source of truth for offensive-language patterns.

Bilingual (English / Spanish) regex patterns, substring evasion checks,
text normalisation helpers, and a false-positive allowlist shared by:

    - openwebui_safety_filter_age_adaptive.py  (Open WebUI front-line filter)
    - safety/pipeline  Stage 3                 (backend pattern matcher)
    - external_filters/account_creation_safety_filter.py  (account creation)

Updating patterns here automatically strengthens all three filters. Split into
focused submodules; this package re-exports the same public surface.
"""

from safety.patterns.allowlist import FALSE_POSITIVE_ALLOWLIST
from safety.patterns.category_patterns import CATEGORY_PATTERNS
from safety.patterns.compiled import COMPILED_PATTERNS, SEVERITY_MAP
from safety.patterns.normalization import (
    BIDI_CONTROLS,
    HOMOGLYPH_MAP,
    INVISIBLE_CHARS,
    LEET_MAP,
    LEET_MAP_ALT,
    SINGLE_LETTER_SPACING_RE,
    STRIP_CHARS,
    normalize_text,
)
from safety.patterns.substring_checks import SUBSTR_CHECKS

__all__ = [
    "LEET_MAP",
    "LEET_MAP_ALT",
    "HOMOGLYPH_MAP",
    "INVISIBLE_CHARS",
    "BIDI_CONTROLS",
    "STRIP_CHARS",
    "SINGLE_LETTER_SPACING_RE",
    "normalize_text",
    "CATEGORY_PATTERNS",
    "SUBSTR_CHECKS",
    "FALSE_POSITIVE_ALLOWLIST",
    "COMPILED_PATTERNS",
    "SEVERITY_MAP",
]
