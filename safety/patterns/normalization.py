"""Text-normalization maps and normalize_text() for the safety patterns.

Extracted verbatim from the former safety/patterns.py (no logic change).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Tuple

# ============================================================================
# Normalisation constants
# ============================================================================

LEET_MAP: Dict[str, str] = {
    "0": "o",
    "1": "l",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "8": "b",
    "!": "i",
    "@": "a",
    "$": "s",
    "|": "i",
    "+": "t",
    "(": "c",
}

# Alternate leet map where ambiguous chars resolve differently.
# "1" is commonly both "l" (h1tler) and "i" (sh1t).  We run both.
LEET_MAP_ALT: Dict[str, str] = {
    **LEET_MAP,
    "1": "i",
}

HOMOGLYPH_MAP: Dict[str, str] = {
    # Cyrillic lowercase
    "а": "a",
    "е": "e",
    "і": "i",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "һ": "h",
    "к": "k",
    "м": "m",
    "н": "h",
    "т": "t",
    # Cyrillic uppercase
    "А": "a",
    "В": "b",
    "Е": "e",
    "К": "k",
    "М": "m",
    "Н": "h",
    "О": "o",
    "Р": "p",
    "С": "c",
    "Т": "t",
    "У": "y",
    "Х": "x",
    # Greek lowercase
    "α": "a",
    "ε": "e",
    "ι": "i",
    "ο": "o",
    "κ": "k",
    "ρ": "p",
    "υ": "u",
    "χ": "x",
    # Greek uppercase
    "Α": "a",
    "Ε": "e",
    "Η": "h",
    "Ι": "i",
    "Κ": "k",
    "Μ": "m",
    "Ν": "n",
    "Ο": "o",
    "Ρ": "p",
    "Τ": "t",
    "Υ": "y",
    "Χ": "x",
}

INVISIBLE_CHARS = frozenset(
    {
        "​",
        "‌",
        "‍",
        "‎",
        "‏",
        "⁠",
        "⁡",
        "⁢",
        "⁣",
        "⁤",
        "﻿",
        "­",
        "͏",
        "؜",
        "᠎",
    }
)

BIDI_CONTROLS = frozenset(
    {
        "‪",
        "‫",
        "‬",
        "‭",
        "‮",
        "⁦",
        "⁧",
        "⁨",
        "⁩",
    }
)

STRIP_CHARS = INVISIBLE_CHARS | BIDI_CONTROLS

SINGLE_LETTER_SPACING_RE = re.compile(r"\b([a-z])\s+(?=[a-z]\b)")


# ============================================================================
# Normalisation helper
# ============================================================================


def normalize_text(text: str) -> Tuple[str, str]:
    """Return *(lightly_normalized, letters_only)*.

    * **lightly_normalized** preserves word boundaries (spaces) so that
      ``\\b``-anchored regex patterns can match.
    * **letters_only** strips everything except ``[a-z]`` so substring
      checks catch obfuscated words like ``n_i_g_g_e_r``.

    Because some leet-speak chars are ambiguous (``1`` can be ``l`` or
    ``i``), the letters-only form is the *union* of both mappings
    (joined by ``|``) so that substring checks catch either interpretation.
    """
    try:
        lowered = text.lower()

        # 1. Strip invisible / bidi characters
        cleaned = "".join(ch for ch in lowered if ch not in STRIP_CHARS)

        # 2. Homoglyph substitution
        chars = list(cleaned)
        for i, ch in enumerate(chars):
            if ch in HOMOGLYPH_MAP:
                chars[i] = HOMOGLYPH_MAP[ch]
        mapped = "".join(chars)

        # 3. NFKD normalisation
        nfkd = unicodedata.normalize("NFKD", mapped)

        # 4. Strip combining diacritics
        stripped = "".join(ch for ch in nfkd if not unicodedata.combining(ch))

        # 5. Leet-speak substitution (primary map: 1->l)
        chars = list(stripped)
        for i, ch in enumerate(chars):
            if ch in LEET_MAP:
                chars[i] = LEET_MAP[ch]
        substituted = "".join(chars)

        # 5b. Alternate leet substitution (1->i) for letters-only form
        chars_alt = list(stripped)
        for i, ch in enumerate(chars_alt):
            if ch in LEET_MAP_ALT:
                chars_alt[i] = LEET_MAP_ALT[ch]
        substituted_alt = "".join(chars_alt)

        # 6. Replace common separators with spaces (usernames use _ and -)
        spaced = re.sub(r"[_\-.]", " ", substituted)

        # 7. Collapse single-letter spacing ("h a t e" -> "hate")
        lightly = SINGLE_LETTER_SPACING_RE.sub(r"\1", spaced)

        # 8. Letters-only form — combine both leet interpretations
        letters_primary = re.sub(r"[^a-z]", "", lightly)
        letters_alt = re.sub(r"[^a-z]", "", substituted_alt)

        return lightly, letters_primary + "|" + letters_alt

    except Exception:
        low = text.lower()
        return low, re.sub(r"[^a-z]", "", low)
