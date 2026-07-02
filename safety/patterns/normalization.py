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
    "\u0430": "a",
    "\u0435": "e",
    "\u0456": "i",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0443": "y",
    "\u0445": "x",
    "\u04bb": "h",
    "\u043a": "k",
    "\u043c": "m",
    "\u043d": "h",
    "\u0442": "t",
    # Cyrillic uppercase
    "\u0410": "a",
    "\u0412": "b",
    "\u0415": "e",
    "\u041a": "k",
    "\u041c": "m",
    "\u041d": "h",
    "\u041e": "o",
    "\u0420": "p",
    "\u0421": "c",
    "\u0422": "t",
    "\u0423": "y",
    "\u0425": "x",
    # Greek lowercase
    "\u03b1": "a",
    "\u03b5": "e",
    "\u03b9": "i",
    "\u03bf": "o",
    "\u03ba": "k",
    "\u03c1": "p",
    "\u03c5": "u",
    "\u03c7": "x",
    # Greek uppercase
    "\u0391": "a",
    "\u0395": "e",
    "\u0397": "h",
    "\u0399": "i",
    "\u039a": "k",
    "\u039c": "m",
    "\u039d": "n",
    "\u039f": "o",
    "\u03a1": "p",
    "\u03a4": "t",
    "\u03a5": "y",
    "\u03a7": "x",
}

INVISIBLE_CHARS = frozenset(
    {
        "\u200b",
        "\u200c",
        "\u200d",
        "\u200e",
        "\u200f",
        "\u2060",
        "\u2061",
        "\u2062",
        "\u2063",
        "\u2064",
        "\ufeff",
        "\u00ad",
        "\u034f",
        "\u061c",
        "\u180e",
    }
)

BIDI_CONTROLS = frozenset(
    {
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
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

        # 8. Letters-only form -- combine both leet interpretations
        letters_primary = re.sub(r"[^a-z]", "", lightly)
        letters_alt = re.sub(r"[^a-z]", "", substituted_alt)

        return lightly, letters_primary + "|" + letters_alt

    except Exception:
        low = text.lower()
        return low, re.sub(r"[^a-z]", "", low)
