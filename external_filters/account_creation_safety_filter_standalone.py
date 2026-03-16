"""
Account Creation Safety Filter - Standalone Single-File Version

Bilingual (English / Spanish) pure-Python keyword filter for account
registration flows.  Blocks offensive, derogatory, hateful, violent,
sexual, and self-harm language in usernames, display names, bios, and
email local parts.

Zero external dependencies -- uses only the Python standard library.
Drop this single file into any project and import it.

Usage:
    from account_creation_safety_filter_standalone import (
        AccountCreationSafetyFilter,
    )

    f = AccountCreationSafetyFilter()
    result = f.check_account(
        username="some_user",
        display_name="Some User",
        bio="Hello world",
        email="some_user@example.com",
    )
    if not result["valid"]:
        for v in result["violations"]:
            print(v)

577 patterns (430 regex + 147 substring) across 7 categories:
    HATE_SPEECH, SEXUAL_CONTENT, VIOLENCE, SELF_HARM,
    PROFANITY, DRUGS, DEROGATORY

Evasion defenses:
    - Dual leet-speak normalization (1->l and 1->i)
    - Homoglyph detection (Cyrillic/Greek Unicode lookalikes)
    - Zero-width / invisible character stripping
    - Separator evasion (underscores, dots, hyphens)
    - Single-letter spacing collapse ("h a t e" -> "hate")
    - 90-word false-positive allowlist
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ===========================================================================
# Text-normalisation constants
# ===========================================================================

_LEET_MAP: Dict[str, str] = {
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
_LEET_MAP_ALT: Dict[str, str] = {
    **_LEET_MAP,
    "1": "i",
}

_HOMOGLYPH_MAP: Dict[str, str] = {
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

_INVISIBLE_CHARS = frozenset(
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

_BIDI_CONTROLS = frozenset(
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

_STRIP_CHARS = _INVISIBLE_CHARS | _BIDI_CONTROLS

_SINGLE_LETTER_SPACING_RE = re.compile(r"\b([a-z])\s+(?=[a-z]\b)")


# ===========================================================================
# Normalisation helper
# ===========================================================================


def _normalize_text(text: str) -> Tuple[str, str]:
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
        cleaned = "".join(ch for ch in lowered if ch not in _STRIP_CHARS)

        # 2. Homoglyph substitution
        chars = list(cleaned)
        for i, ch in enumerate(chars):
            if ch in _HOMOGLYPH_MAP:
                chars[i] = _HOMOGLYPH_MAP[ch]
        mapped = "".join(chars)

        # 3. NFKD normalisation
        nfkd = unicodedata.normalize("NFKD", mapped)

        # 4. Strip combining diacritics
        stripped = "".join(ch for ch in nfkd if not unicodedata.combining(ch))

        # 5. Leet-speak substitution (primary map: 1->l)
        chars = list(stripped)
        for i, ch in enumerate(chars):
            if ch in _LEET_MAP:
                chars[i] = _LEET_MAP[ch]
        substituted = "".join(chars)

        # 5b. Alternate leet substitution (1->i) for letters-only form
        chars_alt = list(stripped)
        for i, ch in enumerate(chars_alt):
            if ch in _LEET_MAP_ALT:
                chars_alt[i] = _LEET_MAP_ALT[ch]
        substituted_alt = "".join(chars_alt)

        # 6. Replace common separators with spaces (usernames use _ and -)
        spaced = re.sub(r"[_\-.]", " ", substituted)

        # 7. Collapse single-letter spacing ("h a t e" -> "hate")
        lightly = _SINGLE_LETTER_SPACING_RE.sub(r"\1", spaced)

        # 8. Letters-only form -- combine both leet interpretations
        letters_primary = re.sub(r"[^a-z]", "", lightly)
        letters_alt = re.sub(r"[^a-z]", "", substituted_alt)

        return lightly, letters_primary + "|" + letters_alt

    except Exception:
        low = text.lower()
        return low, re.sub(r"[^a-z]", "", low)


# ===========================================================================
# Pattern definitions (English + Spanish)
# ===========================================================================

_CATEGORY_PATTERNS: Dict[str, List[Tuple[str, str]]] = {
    # =================================================================
    # HATE_SPEECH -- racial, ethnic, gender, orientation slurs
    # =================================================================
    "HATE_SPEECH": [
        (r"\bn[i1l][g9]+[aer3]+s?\b", "racial slur (EN)"),
        (r"\bcoon\b", "racial slur (EN)"),
        (r"\bdarkie\b", "racial slur (EN)"),
        (r"\bjigaboo\b", "racial slur (EN)"),
        (r"\bjiggaboo\b", "racial slur (EN)"),
        (r"\bporch\s*monkey\b", "racial slur (EN)"),
        (r"\bspook\b", "racial slur (EN)"),
        (r"\bspear\s*chucker\b", "racial slur (EN)"),
        (r"\bspic\b", "ethnic slur (EN)"),
        (r"\bspick\b", "ethnic slur (EN)"),
        (r"\bchink\b", "ethnic slur (EN)"),
        (r"\bgook\b", "ethnic slur (EN)"),
        (r"\bslant\s*eye\b", "ethnic slur (EN)"),
        (r"\bzipper\s*head\b", "ethnic slur (EN)"),
        (r"\bwetback\b", "ethnic slur (EN)"),
        (r"\bbeaner\b", "ethnic slur (EN)"),
        (r"\bgringo\b", "ethnic slur (EN)"),
        (r"\bkike\b", "antisemitic slur (EN)"),
        (r"\bheeb\b", "antisemitic slur (EN)"),
        (r"\bjew\s*boy\b", "antisemitic slur (EN)"),
        (r"\bsandnigg\w*\b", "racial slur (EN)"),
        (r"\btowelhead\b", "ethnic slur (EN)"),
        (r"\brag\s*head\b", "ethnic slur (EN)"),
        (r"\bcamel\s*jockey\b", "ethnic slur (EN)"),
        (r"\bpaki\b", "ethnic slur (EN)"),
        (r"\baborigine\b", "ethnic slur (EN)"),
        (r"\babo\b", "ethnic slur (EN)"),
        (r"\bhalf\s*breed\b", "racial slur (EN)"),
        (r"\bmixed\s*breed\b", "racial slur (EN)"),
        (r"\bcracker\b", "racial slur (EN)"),
        (r"\bwhite\s*trash\b", "racial slur (EN)"),
        (r"\bredneck\b", "derogatory (EN)"),
        (r"\bhillbilly\b", "derogatory (EN)"),
        (r"\bredskin\b", "ethnic slur (EN)"),
        (r"\bsquaw\b", "ethnic slur (EN)"),
        (r"\bwop\b", "ethnic slur (EN)"),
        (r"\bdago\b", "ethnic slur (EN)"),
        (r"\bguinea\b", "ethnic slur (EN)"),
        (r"\bpolack\b", "ethnic slur (EN)"),
        (r"\bgypsy\b", "ethnic slur (EN)"),
        (r"\bpikey\b", "ethnic slur (EN)"),
        (r"\bfagg?ot\w*\b", "orientation slur (EN)"),
        (r"\bfag\b", "orientation slur (EN)"),
        (r"\bdyke\b", "orientation slur (EN)"),
        (r"\blesbo\b", "orientation slur (EN)"),
        (r"\btranny\b", "transphobic slur (EN)"),
        (r"\bshemale\b", "transphobic slur (EN)"),
        (r"\btrapboy\b", "transphobic slur (EN)"),
        (r"\bhe\s*she\b", "transphobic slur (EN)"),
        (r"\bwhore\b", "gender slur (EN)"),
        (r"\bcunt\b", "gender slur (EN)"),
        (r"\bslut\b", "gender slur (EN)"),
        (r"\bskank\b", "gender slur (EN)"),
        (r"\bhoe\b", "gender slur (EN)"),
        (r"\bhoebag\b", "gender slur (EN)"),
        (r"\bfemoid\b", "misogynist slur (EN)"),
        (r"\bthot\b", "gender slur (EN)"),
        (r"\bincel\b", "misogynist term (EN)"),
        (r"\bsimp\b", "derogatory (EN)"),
        (r"\bwhite\s*power\b", "supremacist term (EN)"),
        (r"\bwhite\s*suprem\w*\b", "supremacist term (EN)"),
        (r"\bwhite\s*pride\b", "supremacist term (EN)"),
        (r"\b(heil|sieg)\s*heil\b", "Nazi reference (EN)"),
        (r"\b14\s*88\b", "hate symbol"),
        (r"\b1488\b", "hate symbol"),
        (r"\bnazi\w*\b", "Nazi reference (EN)"),
        (r"\bhitler\b", "Nazi reference (EN)"),
        (r"\breich\b", "Nazi reference (EN)"),
        (r"\bgestapo\b", "Nazi reference (EN)"),
        (r"\bswastika\b", "Nazi reference (EN)"),
        (r"\baryan\s*nation\b", "supremacist term (EN)"),
        (r"\baryan\s*race\b", "supremacist term (EN)"),
        (r"\baryan\s*brotherhood\b", "supremacist term (EN)"),
        (r"\bkkk\b", "hate group (EN)"),
        (r"\bku\s*klux\b", "hate group (EN)"),
        (r"\bklans\w*\b", "hate group (EN)"),
        (r"\bskin\s*head\b", "hate group (EN)"),
        (r"\bneonazi\b", "hate group (EN)"),
        (r"\bnegro\s+de\s+mierda\b", "racial slur (ES)"),
        (r"\bnegro\s+(sucio|cochino|asqueroso|inmundo)\b", "racial slur (ES)"),
        (r"\bsudaca\b", "ethnic slur (ES)"),
        (r"\bmaric[oó]n\w*\b", "orientation slur (ES)"),
        (r"\bmarica\b", "orientation slur (ES)"),
        (r"\bjoto\w*\b", "orientation slur (ES)"),
        (r"\bpuñal\b", "orientation slur (ES)"),
        (r"\bafeminado\b", "orientation slur (ES)"),
        (r"\bmachorra\b", "orientation slur (ES)"),
        (r"\btortillera\b", "orientation slur (ES)"),
        (r"\bindio\s*(mugroso|sucio|ignorante|bruto)\b", "ethnic slur (ES)"),
        (r"\bnaco\b", "ethnic slur (ES)"),
        (r"\bgabacho\b", "ethnic slur (ES)"),
        (r"\bgringo\s*(sucio|cochino|asqueroso)\b", "ethnic slur (ES)"),
        (r"\bcholo\b", "ethnic slur (ES)"),
        (r"\bmayate\b", "racial slur (ES)"),
        (r"\bprieto\b", "racial slur (ES)"),
        (r"\bchango\b", "racial slur (ES)"),
        (r"\bsimio\b", "racial slur (ES)"),
        (r"\bmono\s+(sucio|asqueroso)\b", "racial slur (ES)"),
    ],
    # =================================================================
    # SEXUAL_CONTENT
    # =================================================================
    "SEXUAL_CONTENT": [
        (r"\bporn\w*\b", "explicit term (EN)"),
        (r"\bxxx\b", "explicit term (EN)"),
        (r"\bhentai\b", "explicit term (EN)"),
        (r"\bmilf\b", "explicit term (EN)"),
        (r"\bdilf\b", "explicit term (EN)"),
        (r"\bgilf\b", "explicit term (EN)"),
        (r"\bcocksucker\b", "explicit term (EN)"),
        (r"\bcock\b", "explicit term (EN)"),
        (r"\bfuck\w*\b", "profanity (EN)"),
        (r"\bmasturbat\w*\b", "explicit term (EN)"),
        (r"\borgasm\w*\b", "explicit term (EN)"),
        (r"\bsexting\b", "explicit term (EN)"),
        (r"\bdick(s|head|face)?\b", "explicit term (EN)"),
        (r"\bpussy\b", "explicit term (EN)"),
        (r"\bvagina\b", "explicit term (EN)"),
        (r"\bpenis\b", "explicit term (EN)"),
        (r"\bboobs?\b", "explicit term (EN)"),
        (r"\btits?\b", "explicit term (EN)"),
        (r"\btitties\b", "explicit term (EN)"),
        (r"\berotic\w*\b", "explicit term (EN)"),
        (r"\bnude[sz]?\b", "explicit term (EN)"),
        (r"\bnaked\b", "explicit term (EN)"),
        (r"\bcum\b", "explicit term (EN)"),
        (r"\bcumshot\b", "explicit term (EN)"),
        (r"\bjizz\b", "explicit term (EN)"),
        (r"\bblowjob\b", "explicit term (EN)"),
        (r"\bhandjob\b", "explicit term (EN)"),
        (r"\brape\b", "explicit term (EN)"),
        (r"\brapist\b", "explicit term (EN)"),
        (r"\bmolest\w*\b", "explicit term (EN)"),
        (r"\bpedophil\w*\b", "explicit term (EN)"),
        (r"\bsodom\w*\b", "explicit term (EN)"),
        (r"\banal\b", "explicit term (EN)"),
        (r"\bbondage\b", "explicit term (EN)"),
        (r"\bfetish\b", "explicit term (EN)"),
        (r"\bdominatrix\b", "explicit term (EN)"),
        (r"\bstripper\b", "explicit term (EN)"),
        (r"\bescort\b", "explicit term (EN)"),
        (r"\bhooker\b", "explicit term (EN)"),
        (r"\bprostitut\w*\b", "explicit term (EN)"),
        (r"\bpimp\b", "explicit term (EN)"),
        (r"\bboner\b", "explicit term (EN)"),
        (r"\bhorny\b", "explicit term (EN)"),
        (r"\bbukkake\b", "explicit term (EN)"),
        (r"\bcreampie\b", "explicit term (EN)"),
        (r"\bdoggy\s*style\b", "explicit term (EN)"),
        (r"\bdeepthroat\b", "explicit term (EN)"),
        (r"\bgang\s*bang\b", "explicit term (EN)"),
        (r"\borgy\b", "explicit term (EN)"),
        (r"\bthreesome\b", "explicit term (EN)"),
        (r"\bsex\s*slave\b", "explicit term (EN)"),
        (r"\bclitoris\b", "explicit term (EN)"),
        (r"\bejaculat\w*\b", "explicit term (EN)"),
        (r"\bqueef\b", "explicit term (EN)"),
        (r"\bsmegma\b", "explicit term (EN)"),
        (r"\bporno\w*\b", "explicit term (ES)"),
        (r"\bverga\b", "explicit term (ES)"),
        (r"\bchingar\w*\b", "profanity (ES)"),
        (r"\bchingad[ao]\b", "profanity (ES)"),
        (r"\btetas\b", "explicit term (ES)"),
        (r"\bchichis?\b", "explicit term (ES)"),
        (r"\bmamada\b", "explicit term (ES)"),
        (r"\bfollar\b", "explicit term (ES)"),
        (r"\bpolla\b", "explicit term (ES)"),
        (r"\bpene\b", "explicit term (ES)"),
        (r"\bculo\b", "explicit term (ES)"),
        (r"\bcoger\b", "explicit term (ES)"),
        (r"\bcog(ida|ido)\b", "explicit term (ES)"),
        (r"\bcaliente\b", "explicit term (ES)"),
        (r"\bcachond[ao]\b", "explicit term (ES)"),
        (r"\bviola(dor|cion)\b", "explicit term (ES)"),
        (r"\bviolar\b", "explicit term (ES)"),
        (r"\bprostitu\w*\b", "explicit term (ES)"),
        (r"\bramera\b", "explicit term (ES)"),
        (r"\bzorra\b", "explicit term (ES)"),
        (r"\bgolfa\b", "explicit term (ES)"),
        (r"\bpiruja\b", "explicit term (ES)"),
        (r"\bpedofil\w*\b", "explicit term (ES)"),
        (r"\borgasmo\b", "explicit term (ES)"),
        (r"\bnalgas\b", "explicit term (ES)"),
        (r"\bpanocha\b", "explicit term (ES)"),
        (r"\bpaloma\b", "explicit term (ES)"),
    ],
    # =================================================================
    # VIOLENCE
    # =================================================================
    "VIOLENCE": [
        (r"\bkill(er|ing|s)?\b", "violence term (EN)"),
        (r"\bmurder(er|ous|ing|s)?\b", "violence term (EN)"),
        (r"\bschoolshoot\w*\b", "violence term (EN)"),
        (r"\bmass\s*murder\w*\b", "violence term (EN)"),
        (r"\bmass\s*shoot\w*\b", "violence term (EN)"),
        (r"\bserial\s*kill\w*\b", "violence term (EN)"),
        (r"\bgenocide\b", "violence term (EN)"),
        (r"\bterroris[mt]\w*\b", "violence term (EN)"),
        (r"\bjihad\w*\b", "violence term (EN)"),
        (r"\bbehead(ing)?\b", "violence term (EN)"),
        (r"\btortur(e|ing|er)\b", "violence term (EN)"),
        (r"\bbloodbath\b", "violence term (EN)"),
        (r"\bbloodlust\b", "violence term (EN)"),
        (r"\bshooter\b", "violence term (EN)"),
        (r"\bstab(ber|bing|bed)?\b", "violence term (EN)"),
        (r"\bstrangle\b", "violence term (EN)"),
        (r"\bsmother\b", "violence term (EN)"),
        (r"\bslaught\w*\b", "violence term (EN)"),
        (r"\bmassacre\b", "violence term (EN)"),
        (r"\bexecut(e|ion|ioner)\b", "violence term (EN)"),
        (r"\blynch(ing|ed)?\b", "violence term (EN)"),
        (r"\bhomicid\w*\b", "violence term (EN)"),
        (r"\bmanslaughter\b", "violence term (EN)"),
        (r"\barson(ist)?\b", "violence term (EN)"),
        (r"\bbomb(er|ing)?\b", "violence term (EN)"),
        (r"\bexplosi\w*\b", "violence term (EN)"),
        (r"\bgore\b", "violence term (EN)"),
        (r"\bsnuff\b", "violence term (EN)"),
        (r"\bdismember\w*\b", "violence term (EN)"),
        (r"\bmutilat\w*\b", "violence term (EN)"),
        (r"\bskin\s*alive\b", "violence term (EN)"),
        (r"\bdie\b", "violence term (EN)"),
        (r"\bdeath\b", "violence term (EN)"),
        (r"\bdead\s*body\b", "violence term (EN)"),
        (r"\bcorpse\b", "violence term (EN)"),
        (r"\bbleed\s*out\b", "violence term (EN)"),
        (r"\bgun\s*man\b", "violence term (EN)"),
        (r"\bhostage\b", "violence term (EN)"),
        (r"\bkidnap\w*\b", "violence term (EN)"),
        (r"\bassault\b", "violence term (EN)"),
        (r"\bbatter\w*\b", "violence term (EN)"),
        (r"\bmatar\b", "violence term (ES)"),
        (r"\bmatanza\b", "violence term (ES)"),
        (r"\basesina[rt]\w*\b", "violence term (ES)"),
        (r"\basesino\b", "violence term (ES)"),
        (r"\bmasacre\b", "violence term (ES)"),
        (r"\bterrorista\b", "violence term (ES)"),
        (r"\bdecapitar\b", "violence term (ES)"),
        (r"\btortura\w*\b", "violence term (ES)"),
        (r"\bbalacera\b", "violence term (ES)"),
        (r"\bsicario\b", "violence term (ES)"),
        (r"\bnarco\w*\b", "violence term (ES)"),
        (r"\bmuerte\b", "violence term (ES)"),
        (r"\bmuerto\b", "violence term (ES)"),
        (r"\bcadaver\b", "violence term (ES)"),
        (r"\bdegollar\b", "violence term (ES)"),
        (r"\blinchar\b", "violence term (ES)"),
        (r"\bhomicid\w*\b", "violence term (ES)"),
        (r"\bsecuestr\w*\b", "violence term (ES)"),
        (r"\bviolen(cia|to)\b", "violence term (ES)"),
        (r"\bbomba\b", "violence term (ES)"),
        (r"\bexplosivo\b", "violence term (ES)"),
        (r"\bsangre\b", "violence term (ES)"),
        (r"\bdesangrarse\b", "violence term (ES)"),
        (r"\bapuñalar\b", "violence term (ES)"),
        (r"\bestrangular\b", "violence term (ES)"),
        (r"\bincendio\s*provocado\b", "violence term (ES)"),
    ],
    # =================================================================
    # SELF_HARM
    # =================================================================
    "SELF_HARM": [
        (r"\bsuicid[ea]l?\b", "self-harm term (EN)"),
        (r"\bselfharm\w*\b", "self-harm term (EN)"),
        (r"\bself\s*harm\w*\b", "self-harm term (EN)"),
        (r"\bself\s*injur\w*\b", "self-harm term (EN)"),
        (r"\bcutmyself\b", "self-harm term (EN)"),
        (r"\bcut\s*myself\b", "self-harm term (EN)"),
        (r"\bkillmyself\b", "self-harm term (EN)"),
        (r"\bkill\s*myself\b", "self-harm term (EN)"),
        (r"\biwanttodie\b", "self-harm term (EN)"),
        (r"\bi\s*want\s*to\s*die\b", "self-harm term (EN)"),
        (r"\boverdose\b", "self-harm term (EN)"),
        (r"\bslit\s*(my\s*)?(wrist|throat)\b", "self-harm term (EN)"),
        (r"\bhang\s*myself\b", "self-harm term (EN)"),
        (r"\bjump\s*off\s*(a\s*)?(bridge|building|roof)\b", "self-harm term (EN)"),
        (r"\bend\s*my\s*life\b", "self-harm term (EN)"),
        (r"\bend\s*it\s*all\b", "self-harm term (EN)"),
        (r"\bwant\s*to\s*die\b", "self-harm term (EN)"),
        (r"\bno\s*reason\s*to\s*live\b", "self-harm term (EN)"),
        (r"\bbetter\s*off\s*dead\b", "self-harm term (EN)"),
        (r"\banorexia\b", "self-harm term (EN)"),
        (r"\bbulimia\b", "self-harm term (EN)"),
        (r"\bpurging\b", "self-harm term (EN)"),
        (r"\bsuicid(io|a|arme|arse)\b", "self-harm term (ES)"),
        (r"\bmatarme\b", "self-harm term (ES)"),
        (r"\bcortarme\b", "self-harm term (ES)"),
        (r"\bquiero\s*morir\b", "self-harm term (ES)"),
        (r"\bquieromorir\b", "self-harm term (ES)"),
        (r"\bautolesi[oó]n\w*\b", "self-harm term (ES)"),
        (r"\bsobredosis\b", "self-harm term (ES)"),
        (r"\bcortarme\s*las?\s*venas?\b", "self-harm term (ES)"),
        (r"\bahorcarme\b", "self-harm term (ES)"),
        (r"\btirarme\s*(de|del)\b", "self-harm term (ES)"),
        (r"\bterminar\s*con\s*todo\b", "self-harm term (ES)"),
        (r"\bno\s*quiero\s*vivir\b", "self-harm term (ES)"),
        (r"\bmejor\s*muert[oa]\b", "self-harm term (ES)"),
        (r"\banorexia\b", "self-harm term (ES)"),
        (r"\bbulimia\b", "self-harm term (ES)"),
    ],
    # =================================================================
    # PROFANITY
    # =================================================================
    "PROFANITY": [
        (r"\bshit\w*\b", "profanity (EN)"),
        (r"\bbullshit\b", "profanity (EN)"),
        (r"\bass(hole|wipe|hat|face|clown)?\b", "profanity (EN)"),
        (r"\bbitch\w*\b", "profanity (EN)"),
        (r"\bbastard\b", "profanity (EN)"),
        (r"\bdamn(it)?\b", "profanity (EN)"),
        (r"\bhell\b", "profanity (EN)"),
        (r"\bcrap\b", "profanity (EN)"),
        (r"\bpiss\w*\b", "profanity (EN)"),
        (r"\bturd\b", "profanity (EN)"),
        (r"\bdouche\w*\b", "profanity (EN)"),
        (r"\bjackass\b", "profanity (EN)"),
        (r"\bdumbass\b", "profanity (EN)"),
        (r"\bbadass\b", "profanity (EN)"),
        (r"\bwanker\b", "profanity (EN)"),
        (r"\bbollocks\b", "profanity (EN)"),
        (r"\bwank\b", "profanity (EN)"),
        (r"\bsod\s*off\b", "profanity (EN)"),
        (r"\bbugger\b", "profanity (EN)"),
        (r"\barse\b", "profanity (EN)"),
        (r"\bmotherfuck\w*\b", "profanity (EN)"),
        (r"\bstfu\b", "profanity (EN)"),
        (r"\bgtfo\b", "profanity (EN)"),
        (r"\bwtf\b", "profanity (EN)"),
        (r"\bfml\b", "profanity (EN)"),
        (r"\bmierda\b", "profanity (ES)"),
        (r"\bcarajo\b", "profanity (ES)"),
        (r"\bchinga\w*\b", "profanity (ES)"),
        (r"\bcabr[oó]n\w*\b", "profanity (ES)"),
        (r"\bpinche\b", "profanity (ES)"),
        (r"\bvete\s*a\s*la\s*(mierda|verga|chingada)\b", "profanity (ES)"),
        (r"\bhijo\s*de\s*(puta|perra)\b", "profanity (ES)"),
        (r"\bchingada\s*madre\b", "profanity (ES)"),
        (r"\bla\s*madre\b", "profanity (ES)"),
        (r"\bno\s*mames\b", "profanity (ES)"),
        (r"\bchale\b", "profanity (ES)"),
        (r"\bculero\b", "profanity (ES)"),
        (r"\bpendejad\w*\b", "profanity (ES)"),
        (r"\bmaldito\b", "profanity (ES)"),
        (r"\bmaldita\b", "profanity (ES)"),
        (r"\bjoder\b", "profanity (ES)"),
        (r"\bcoño\b", "profanity (ES)"),
        (r"\bhost?ia\b", "profanity (ES)"),
        (r"\bcojones\b", "profanity (ES)"),
        (r"\bgilipollas\b", "profanity (ES)"),
        (r"\bcapullo\b", "profanity (ES)"),
    ],
    # =================================================================
    # DRUGS
    # =================================================================
    "DRUGS": [
        (r"\bcocaine\b", "drug reference (EN)"),
        (r"\bcoke\s*head\b", "drug reference (EN)"),
        (r"\bcrack\s*head\b", "drug reference (EN)"),
        (r"\bheroin\b", "drug reference (EN)"),
        (r"\bmeth\b", "drug reference (EN)"),
        (r"\bmethamphetamine\b", "drug reference (EN)"),
        (r"\bcrystal\s*meth\b", "drug reference (EN)"),
        (r"\bweed\s*420\b", "drug reference (EN)"),
        (r"\b420\b", "drug reference (EN)"),
        (r"\bblaze\s*it\b", "drug reference (EN)"),
        (r"\bstoner\b", "drug reference (EN)"),
        (r"\bpot\s*head\b", "drug reference (EN)"),
        (r"\bjunkie\b", "drug reference (EN)"),
        (r"\bcrackhead\b", "drug reference (EN)"),
        (r"\blsd\b", "drug reference (EN)"),
        (r"\becstasy\b", "drug reference (EN)"),
        (r"\bmolly\b", "drug reference (EN)"),
        (r"\bketamine\b", "drug reference (EN)"),
        (r"\bfentanyl\b", "drug reference (EN)"),
        (r"\bxanax\b", "drug reference (EN)"),
        (r"\bpercocet\b", "drug reference (EN)"),
        (r"\boxycontin\b", "drug reference (EN)"),
        (r"\badderall\b", "drug reference (EN)"),
        (r"\bshrooms\b", "drug reference (EN)"),
        (r"\bopium\b", "drug reference (EN)"),
        (r"\bcrack\b", "drug reference (EN)"),
        (r"\bdope\b", "drug reference (EN)"),
        (r"\bhigh\s*af\b", "drug reference (EN)"),
        (r"\bcocaina\b", "drug reference (ES)"),
        (r"\bheroina\b", "drug reference (ES)"),
        (r"\bmetanfetamina\b", "drug reference (ES)"),
        (r"\bmarihuana\b", "drug reference (ES)"),
        (r"\bmota\b", "drug reference (ES)"),
        (r"\byerba\b", "drug reference (ES)"),
        (r"\bporro\b", "drug reference (ES)"),
        (r"\bchurro\b", "drug reference (ES)"),
        (r"\bperico\b", "drug reference (ES)"),
        (r"\bcrista[l]?\b", "drug reference (ES)"),
        (r"\bdrogadicto\b", "drug reference (ES)"),
        (r"\bdrogad[oa]\b", "drug reference (ES)"),
        (r"\bpasti(lla|s)\b", "drug reference (ES)"),
        (r"\btraficante\b", "drug reference (ES)"),
    ],
    # =================================================================
    # DEROGATORY
    # =================================================================
    "DEROGATORY": [
        (r"\bretard(ed|s)?\b", "ableist slur (EN)"),
        (r"\bspaz(z)?\b", "ableist slur (EN)"),
        (r"\bcripple\b", "ableist slur (EN)"),
        (r"\bimbecile\b", "derogatory (EN)"),
        (r"\bmoron\b", "derogatory (EN)"),
        (r"\bidiot\b", "derogatory (EN)"),
        (r"\bfreak\b", "derogatory (EN)"),
        (r"\bloser\b", "derogatory (EN)"),
        (r"\bpathetic\b", "derogatory (EN)"),
        (r"\bdegen(erate)?\b", "derogatory (EN)"),
        (r"\bscum\b", "derogatory (EN)"),
        (r"\bscumbag\b", "derogatory (EN)"),
        (r"\bvermin\b", "derogatory (EN)"),
        (r"\bsubhuman\b", "derogatory (EN)"),
        (r"\blard\s*ass\b", "body shaming (EN)"),
        (r"\bfatty\b", "body shaming (EN)"),
        (r"\bfatass\b", "body shaming (EN)"),
        (r"\bugly\s*ass\b", "body shaming (EN)"),
        (r"\bsociopath\b", "derogatory (EN)"),
        (r"\bpsycho(path)?\b", "derogatory (EN)"),
        (r"\blunatic\b", "derogatory (EN)"),
        (r"\bmental\s*case\b", "derogatory (EN)"),
        (r"\bnutcase\b", "derogatory (EN)"),
        (r"\bnutjob\b", "derogatory (EN)"),
        (r"\bcrazy\b", "derogatory (EN)"),
        (r"\binsane\b", "derogatory (EN)"),
        (r"\bweeb\b", "derogatory (EN)"),
        (r"\bneckbeard\b", "derogatory (EN)"),
        (r"\bvirgin\b", "derogatory (EN)"),
        (r"\btrigger\w*\b", "derogatory (EN)"),
        (r"\bsnowflake\b", "derogatory (EN)"),
        (r"\blibtard\b", "derogatory (EN)"),
        (r"\bcuck\b", "derogatory (EN)"),
        (r"\bsoyboy\b", "derogatory (EN)"),
        (r"\bretrasad[oa]\b", "ableist slur (ES)"),
        (r"\bpendej[oa]\b", "profanity (ES)"),
        (r"\bput[oa]\b", "profanity (ES)"),
        (r"\best[uú]pid[oa]\b", "derogatory (ES)"),
        (r"\bidiota\b", "derogatory (ES)"),
        (r"\bimbecil\b", "derogatory (ES)"),
        (r"\btarad[oa]\b", "derogatory (ES)"),
        (r"\bmens[oa]\b", "derogatory (ES)"),
        (r"\bton?t[oa]\b", "derogatory (ES)"),
        (r"\bbab?os[oa]\b", "derogatory (ES)"),
        (r"\bburr[oa]\b", "derogatory (ES)"),
        (r"\bbrut[oa]\b", "derogatory (ES)"),
        (r"\binutil\b", "derogatory (ES)"),
        (r"\bbasura\b", "derogatory (ES)"),
        (r"\bescoria\b", "derogatory (ES)"),
        (r"\benferm[oa]\s*mental\b", "derogatory (ES)"),
        (r"\bloc[oa]\b", "derogatory (ES)"),
        (r"\bdemente\b", "derogatory (ES)"),
        (r"\bgord[oa]\s*(asqueros[oa]|cerdo)?\b", "body shaming (ES)"),
        (r"\bcerdo\b", "derogatory (ES)"),
        (r"\bfe[oa]\b", "body shaming (ES)"),
        (r"\bmuert[oa]\s*de\s*hambre\b", "derogatory (ES)"),
        (r"\barrastrad[oa]\b", "derogatory (ES)"),
        (r"\bcorrient[oe]\b", "derogatory (ES)"),
    ],
}


# ===========================================================================
# Substring evasion checks (matched against letters-only normalised form)
# ===========================================================================

_SUBSTR_CHECKS: Dict[str, List[Tuple[str, str]]] = {
    "HATE_SPEECH": [
        ("nigger", "racial slur"),
        ("nigga", "racial slur"),
        ("nigg", "racial slur"),
        ("coon", "racial slur"),
        ("darkie", "racial slur"),
        ("jigaboo", "racial slur"),
        ("porchmonkey", "racial slur"),
        ("spearchucker", "racial slur"),
        ("faggot", "orientation slur"),
        ("fagot", "orientation slur"),
        ("dyke", "orientation slur"),
        ("tranny", "transphobic slur"),
        ("shemale", "transphobic slur"),
        ("kike", "antisemitic slur"),
        ("wetback", "ethnic slur"),
        ("beaner", "ethnic slur"),
        ("chink", "ethnic slur"),
        ("gook", "ethnic slur"),
        ("towelhead", "ethnic slur"),
        ("raghead", "ethnic slur"),
        ("spic", "ethnic slur"),
        ("redskin", "ethnic slur"),
        ("maricon", "orientation slur (ES)"),
        ("mayate", "racial slur (ES)"),
        ("prieto", "racial slur (ES)"),
        ("nazi", "Nazi reference"),
        ("neonazi", "Nazi reference"),
        ("hitler", "Nazi reference"),
        ("hltler", "Nazi reference (leet)"),
        ("whitepower", "supremacist term"),
        ("whitesuprem", "supremacist term"),
        ("aryanrace", "supremacist term"),
        ("kuklux", "hate group"),
        ("skinhead", "hate group"),
    ],
    "SEXUAL_CONTENT": [
        ("porn", "explicit term"),
        ("hentai", "explicit term"),
        ("cocksucker", "explicit term"),
        ("blowjob", "explicit term"),
        ("handjob", "explicit term"),
        ("gangbang", "explicit term"),
        ("threesome", "explicit term"),
        ("deepthroat", "explicit term"),
        ("bukkake", "explicit term"),
        ("creampie", "explicit term"),
        ("masturbat", "explicit term"),
        ("ejaculat", "explicit term"),
        ("cumshot", "explicit term"),
        ("milf", "explicit term"),
        ("dilf", "explicit term"),
        ("rape", "explicit term"),
        ("rapist", "explicit term"),
        ("molest", "explicit term"),
        ("pedophil", "explicit term"),
        ("pedofil", "explicit term (ES)"),
        ("prostitu", "explicit term"),
        ("hooker", "explicit term"),
        ("stripper", "explicit term"),
        ("sexslave", "explicit term"),
        ("violador", "explicit term (ES)"),
        ("violacion", "explicit term (ES)"),
        ("ramera", "explicit term (ES)"),
        ("zorra", "explicit term (ES)"),
    ],
    "VIOLENCE": [
        ("schoolshoot", "violence term"),
        ("massmurder", "violence term"),
        ("massshoot", "violence term"),
        ("serialkill", "violence term"),
        ("killer", "violence term"),
        ("murder", "violence term"),
        ("genocide", "violence term"),
        ("terrorist", "violence term"),
        ("behead", "violence term"),
        ("dismember", "violence term"),
        ("mutilat", "violence term"),
        ("slaughter", "violence term"),
        ("massacre", "violence term"),
        ("asesino", "violence term (ES)"),
        ("sicario", "violence term (ES)"),
        ("narco", "violence term (ES)"),
        ("decapitar", "violence term (ES)"),
        ("degollar", "violence term (ES)"),
    ],
    "SELF_HARM": [
        ("suicide", "self-harm term"),
        ("selfharm", "self-harm term"),
        ("selfinjur", "self-harm term"),
        ("killmyself", "self-harm term"),
        ("iwanttodie", "self-harm term"),
        ("cutmyself", "self-harm term"),
        ("endmylife", "self-harm term"),
        ("enditall", "self-harm term"),
        ("betteroffdead", "self-harm term"),
        ("slitmywrist", "self-harm term"),
        ("hangmyself", "self-harm term"),
        ("quieromorir", "self-harm term (ES)"),
        ("matarme", "self-harm term (ES)"),
        ("cortarme", "self-harm term (ES)"),
        ("autolesion", "self-harm term (ES)"),
        ("suicidio", "self-harm term (ES)"),
        ("ahorcarme", "self-harm term (ES)"),
    ],
    "PROFANITY": [
        ("shit", "profanity"),
        ("motherfuck", "profanity"),
        ("bullshit", "profanity"),
        ("asshole", "profanity"),
        ("asswipe", "profanity"),
        ("asshat", "profanity"),
        ("assface", "profanity"),
        ("dumbass", "profanity"),
        ("jackass", "profanity"),
        ("douchebag", "profanity"),
        ("bitch", "profanity"),
        ("bastard", "profanity"),
        ("wanker", "profanity"),
        ("bollocks", "profanity"),
        ("bugger", "profanity"),
        ("piss", "profanity"),
        ("hijodeputa", "profanity (ES)"),
        ("hijodeperra", "profanity (ES)"),
        ("chingadamadre", "profanity (ES)"),
        ("gilipollas", "profanity (ES)"),
        ("mierda", "profanity (ES)"),
        ("cabron", "profanity (ES)"),
        ("culero", "profanity (ES)"),
        ("cojones", "profanity (ES)"),
    ],
    "DRUGS": [
        ("cocaine", "drug reference"),
        ("cocaina", "drug reference (ES)"),
        ("heroin", "drug reference"),
        ("heroina", "drug reference (ES)"),
        ("methamphetamine", "drug reference"),
        ("metanfetamina", "drug reference (ES)"),
        ("crystalmeth", "drug reference"),
        ("crackhead", "drug reference"),
        ("junkie", "drug reference"),
        ("fentanyl", "drug reference"),
        ("oxycontin", "drug reference"),
        ("drogadicto", "drug reference (ES)"),
        ("traficante", "drug reference (ES)"),
    ],
    "DEROGATORY": [
        ("retard", "ableist slur"),
        ("retarded", "ableist slur"),
        ("subhuman", "derogatory"),
        ("scumbag", "derogatory"),
        ("libtard", "derogatory"),
        ("soyboy", "derogatory"),
        ("neckbeard", "derogatory"),
        ("nutjob", "derogatory"),
        ("nutcase", "derogatory"),
        ("fatass", "body shaming"),
        ("lardass", "body shaming"),
        ("retrasado", "ableist slur (ES)"),
        ("escoria", "derogatory (ES)"),
    ],
}


# ===========================================================================
# False-positive allowlist
# ===========================================================================

_FALSE_POSITIVE_ALLOWLIST = frozenset({
    "cocoon", "raccoon", "tycoon",
    "grape", "drape", "scrape", "trapeze",
    "therapist",
    "shitake", "shiitake",
    "assessment", "assemble", "assign", "assist", "associate", "class",
    "bass", "brass", "compass", "embassy", "grass", "harass", "mass",
    "sassafras", "classic", "passage", "passenger",
    "accumulate", "circumvent", "document", "cucumber",
    "shoe", "phoenix", "honest",
    "night", "knight", "nigiri",
    "dickens", "predict", "addiction", "dictionary", "verdict",
    "analog", "analysis", "analyst", "canal",
    "skill", "killdeer", "killjoy",
    "diesel", "diet", "soldier", "studies",
    "firecracker",
    "hello", "shell", "seashell",
    "title", "entitled", "constitution", "petition", "appetite",
    "cocktail", "peacock", "hancock", "cockpit",
    "sussex", "essex", "sextant",
    "despicable", "suspicion", "auspicious",
    "scunthorpe",
    "unit", "ignite", "finite",
    "shrug", "drugstore",
    "white", "exhibit", "prohibit",
    "homework", "homogeneous",
    "battery",
    "laughter",
    "establish", "stability", "stable",
    "molecule",
    "pimple",
})


# ===========================================================================
# Compiled pattern cache (built once at import time)
# ===========================================================================

_COMPILED_PATTERNS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    category: [(re.compile(pat), desc) for pat, desc in entries]
    for category, entries in _CATEGORY_PATTERNS.items()
}

_SEVERITY_MAP: Dict[str, str] = {
    "HATE_SPEECH": "high",
    "SEXUAL_CONTENT": "high",
    "VIOLENCE": "high",
    "SELF_HARM": "high",
    "PROFANITY": "medium",
    "DRUGS": "medium",
    "DEROGATORY": "medium",
}


# ===========================================================================
# Main filter class
# ===========================================================================


class AccountCreationSafetyFilter:
    """Bilingual (EN/ES) safety filter for account creation fields.

    Validates usernames, display names, bios, and email local parts
    against offensive / derogatory language patterns using regex
    matching on normalised text.  Optionally logs incidents to SQLite.
    """

    def __init__(
        self,
        db_path: str = "account_safety_logs.db",
        enable_logging: bool = True,
    ) -> None:
        self._db_path = db_path
        self._enable_logging = enable_logging
        if enable_logging:
            self._init_logging_database()

    # -- public API ---------------------------------------------------------

    def check_field(self, text: str, field_name: str) -> Dict:
        """Validate a single text field.

        Returns::

            {"valid": True/False, "violations": [...]}
        """
        if not text:
            return {"valid": True, "violations": []}

        lightly, letters_only = _normalize_text(text)
        text_lower = text.lower().strip()

        # Also produce a "pre-normalized" form that preserves digits (for
        # patterns like \\b420\\b that only make sense on the original text).
        pre_norm = re.sub(r"[_\-.]", " ", text_lower)

        violations: List[Dict[str, str]] = []

        # Check if any individual word in the input is allowlisted.
        input_words = set(re.sub(r"[_\-.~\s]+", " ", text_lower).split())
        allowlisted_words = input_words & _FALSE_POSITIVE_ALLOWLIST

        for category, patterns in _COMPILED_PATTERNS.items():
            matched = False
            for regex, desc in patterns:
                hit_lightly = regex.search(lightly)
                hit_pre = regex.search(pre_norm)
                if hit_lightly or hit_pre:
                    if allowlisted_words:
                        hit = hit_lightly or hit_pre
                        matched_text = hit.group(0).strip()
                        if any(
                            matched_text in aw for aw in allowlisted_words
                        ):
                            continue
                    violations.append(
                        {
                            "field": field_name,
                            "category": category,
                            "pattern_matched": desc,
                        }
                    )
                    matched = True
                    break

            # Substring check on letters-only form
            if not matched:
                for substr, desc in _SUBSTR_CHECKS.get(category, []):
                    if substr in letters_only:
                        if allowlisted_words and any(
                            substr in aw for aw in allowlisted_words
                        ):
                            continue
                        violations.append(
                            {
                                "field": field_name,
                                "category": category,
                                "pattern_matched": desc,
                            }
                        )
                        break

        return {"valid": len(violations) == 0, "violations": violations}

    def check_account(
        self,
        username: str,
        display_name: str,
        bio: Optional[str] = None,
        email: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Dict:
        """Validate all account-creation fields at once.

        Returns::

            {"valid": True/False, "violations": [...]}
        """
        all_violations: List[Dict[str, str]] = []

        fields: List[Tuple[str, Optional[str]]] = [
            ("username", username),
            ("display_name", display_name),
            ("bio", bio),
            ("email", self._extract_email_local(email) if email else None),
        ]

        for field_name, value in fields:
            if not value:
                continue
            result = self.check_field(value, field_name)
            if not result["valid"]:
                all_violations.extend(result["violations"])

        if all_violations and self._enable_logging:
            for v in all_violations:
                field_value = dict(fields).get(v["field"], "")
                self._log_incident(
                    ip_address=ip_address,
                    attempted_value=field_value or "",
                    field_name=v["field"],
                    category=v["category"],
                    pattern_matched=v["pattern_matched"],
                )

        return {"valid": len(all_violations) == 0, "violations": all_violations}

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _extract_email_local(email: str) -> str:
        """Return the local part of an email address (before ``@``)."""
        parts = email.split("@", 1)
        return parts[0] if parts else ""

    # -- SQLite logging -----------------------------------------------------

    def _init_logging_database(self) -> None:
        try:
            db_path = Path(self._db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS account_creation_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    ip_address TEXT,
                    attempted_value TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    pattern_matched TEXT,
                    severity TEXT NOT NULL,
                    reviewed BOOLEAN DEFAULT 0,
                    notes TEXT
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS account_creation_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total_checks INTEGER DEFAULT 0,
                    blocked_attempts INTEGER DEFAULT 0,
                    UNIQUE(date)
                )
                """
            )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ACCOUNT SAFETY FILTER] Database init error: {e}")

    def _log_incident(
        self,
        ip_address: Optional[str],
        attempted_value: str,
        field_name: str,
        category: str,
        pattern_matched: str,
    ) -> None:
        if not self._enable_logging:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()

            severity = _SEVERITY_MAP.get(category, "low")

            cursor.execute(
                """
                INSERT INTO account_creation_incidents (
                    timestamp, ip_address, attempted_value,
                    field_name, category, pattern_matched, severity
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    ip_address,
                    attempted_value[:500],
                    field_name,
                    category,
                    pattern_matched,
                    severity,
                ),
            )

            today = datetime.now().date().isoformat()
            cursor.execute(
                """
                INSERT INTO account_creation_analytics
                    (date, total_checks, blocked_attempts)
                VALUES (?, 1, 1)
                ON CONFLICT(date) DO UPDATE SET
                    total_checks = total_checks + 1,
                    blocked_attempts = blocked_attempts + 1
                """,
                (today,),
            )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ACCOUNT SAFETY FILTER] Logging error: {e}")
