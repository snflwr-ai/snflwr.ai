"""
Account Creation Safety Filter - Bilingual (English / Spanish)

Standalone, pure-Python keyword filter for account registration flows.
Blocks offensive, derogatory, hateful, violent, sexual, and self-harm
language in usernames, display names, bios, and email local parts.

Adapted from snflwr.ai's openwebui_safety_filter_age_adaptive.py and
safety/pipeline.py normalization logic.  No external ML model required.

Usage:
    from external_filters.account_creation_safety_filter import (
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
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Text-normalisation constants (ported from safety/pipeline.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> Tuple[str, str]:
    """Return *(lightly_normalized, letters_only)*.

    * **lightly_normalized** preserves word boundaries (spaces) so that
      ``\\b``-anchored regex patterns can match.
    * **letters_only** strips everything except ``[a-z]`` so substring
      checks catch obfuscated words like ``n_i_g_g_e_r``.
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

        # 5. Leet-speak substitution
        chars = list(stripped)
        for i, ch in enumerate(chars):
            if ch in _LEET_MAP:
                chars[i] = _LEET_MAP[ch]
        substituted = "".join(chars)

        # 6. Replace common separators with spaces (usernames use _ and -)
        spaced = re.sub(r"[_\-.]", " ", substituted)

        # 7. Collapse single-letter spacing ("h a t e" -> "hate")
        lightly = _SINGLE_LETTER_SPACING_RE.sub(r"\1", spaced)

        # 8. Letters-only form
        letters_only = re.sub(r"[^a-z]", "", lightly)

        return lightly, letters_only

    except Exception:
        low = text.lower()
        return low, re.sub(r"[^a-z]", "", low)


# ---------------------------------------------------------------------------
# Pattern definitions (English + Spanish)
# ---------------------------------------------------------------------------

# Each entry is (compiled_regex, human_description).  Regex patterns are
# matched against the *lightly normalised* form (word boundaries preserved).
# A parallel ``_SUBSTR_CHECKS`` dict maps categories to plain strings that
# are checked against the *letters-only* form via ``in`` substring match.

_CATEGORY_PATTERNS: Dict[str, List[Tuple[str, str]]] = {
    # ----- HATE_SPEECH -----
    "HATE_SPEECH": [
        # -- English racial / ethnic slurs --
        (r"\bn[i1l][g9]+[aer3]+\b", "racial slur (EN)"),
        (r"\bcoon\b", "racial slur (EN)"),
        (r"\bspic\b", "ethnic slur (EN)"),
        (r"\bchink\b", "ethnic slur (EN)"),
        (r"\bgook\b", "ethnic slur (EN)"),
        (r"\bwetback\b", "ethnic slur (EN)"),
        (r"\bkike\b", "antisemitic slur (EN)"),
        (r"\bsandnigg\w*\b", "racial slur (EN)"),
        (r"\btowelhead\b", "ethnic slur (EN)"),
        (r"\bcamel\s*jockey\b", "ethnic slur (EN)"),
        (r"\bbeaner\b", "ethnic slur (EN)"),
        # -- English gender / orientation slurs --
        (r"\bfagg?ot\b", "orientation slur (EN)"),
        (r"\bdyke\b", "orientation slur (EN)"),
        (r"\btranny\b", "transphobic slur (EN)"),
        (r"\bshemale\b", "transphobic slur (EN)"),
        (r"\bwhore\b", "gender slur (EN)"),
        (r"\bcunt\b", "gender slur (EN)"),
        (r"\bslut\b", "gender slur (EN)"),
        (r"\bfemoid\b", "misogynist slur (EN)"),
        (r"\bthot\b", "gender slur (EN)"),
        # -- Supremacist / hate symbols --
        (r"\bwhite\s*power\b", "supremacist term (EN)"),
        (r"\bwhite\s*suprem\w*\b", "supremacist term (EN)"),
        (r"\b(heil|sieg)\s*heil\b", "Nazi reference (EN)"),
        (r"\b14\s*88\b", "hate symbol"),
        (r"\bnazi\b", "Nazi reference (EN)"),
        (r"\bhitler\b", "Nazi reference (EN)"),
        (r"\baryan\s*nation\b", "supremacist term (EN)"),
        # -- Spanish racial / ethnic / orientation slurs --
        (r"\bnegro\s+de\s+mierda\b", "racial slur (ES)"),
        (r"\bsudaca\b", "ethnic slur (ES)"),
        (r"\bmaric[oó]n\b", "orientation slur (ES)"),
        (r"\bjoto\b", "orientation slur (ES)"),
        (r"\bindio\s*(mugroso|sucio)\b", "ethnic slur (ES)"),
    ],
    # ----- SEXUAL_CONTENT -----
    "SEXUAL_CONTENT": [
        # -- English --
        (r"\bporn\b", "explicit term (EN)"),
        (r"\bxxx\b", "explicit term (EN)"),
        (r"\bhentai\b", "explicit term (EN)"),
        (r"\bmilf\b", "explicit term (EN)"),
        (r"\bcocksucker\b", "explicit term (EN)"),
        (r"\bfuck\w*\b", "profanity (EN)"),
        (r"\bmasturbat\w*\b", "explicit term (EN)"),
        (r"\borgasm\b", "explicit term (EN)"),
        (r"\bsexting\b", "explicit term (EN)"),
        (r"\bdick\b", "explicit term (EN)"),
        (r"\bpussy\b", "explicit term (EN)"),
        (r"\bboobs\b", "explicit term (EN)"),
        (r"\berotic\w*\b", "explicit term (EN)"),
        (r"\bnude[sz]?\b", "explicit term (EN)"),
        (r"\bnaked\b", "explicit term (EN)"),
        (r"\bcum\b", "explicit term (EN)"),
        # -- Spanish --
        (r"\bporno\b", "explicit term (ES)"),
        (r"\bverga\b", "explicit term (ES)"),
        (r"\bchingar\b", "profanity (ES)"),
        (r"\btetas\b", "explicit term (ES)"),
        (r"\bmamada\b", "explicit term (ES)"),
        (r"\bfollar\b", "explicit term (ES)"),
        (r"\bpolla\b", "explicit term (ES)"),
    ],
    # ----- VIOLENCE -----
    "VIOLENCE": [
        # -- English --
        (r"\bkill(er|ing)?\b", "violence term (EN)"),
        (r"\bmurder(er|ous)?\b", "violence term (EN)"),
        (r"\bschoolshoot\w*\b", "violence term (EN)"),
        (r"\bmassmurder\b", "violence term (EN)"),
        (r"\bgenocide\b", "violence term (EN)"),
        (r"\bterrorist\b", "violence term (EN)"),
        (r"\bbehead\b", "violence term (EN)"),
        (r"\btorture\b", "violence term (EN)"),
        (r"\bbloodbath\b", "violence term (EN)"),
        (r"\bschoolshooter\b", "violence term (EN)"),
        (r"\bshooter\b", "violence term (EN)"),
        (r"\bstab(ber|bing)?\b", "violence term (EN)"),
        # -- Spanish --
        (r"\bmatar\b", "violence term (ES)"),
        (r"\basesino\b", "violence term (ES)"),
        (r"\bmasacre\b", "violence term (ES)"),
        (r"\bterrorista\b", "violence term (ES)"),
        (r"\bdecapitar\b", "violence term (ES)"),
        (r"\btortura\b", "violence term (ES)"),
        (r"\bbalacera\b", "violence term (ES)"),
        (r"\bsicario\b", "violence term (ES)"),
    ],
    # ----- SELF_HARM -----
    "SELF_HARM": [
        # -- English --
        (r"\bsuicid[ea]l?\b", "self-harm term (EN)"),
        (r"\bselfharm\b", "self-harm term (EN)"),
        (r"\bcutmyself\b", "self-harm term (EN)"),
        (r"\bkillmyself\b", "self-harm term (EN)"),
        (r"\biwanttodie\b", "self-harm term (EN)"),
        (r"\boverdose\b", "self-harm term (EN)"),
        # -- Spanish --
        (r"\bsuicidio\b", "self-harm term (ES)"),
        (r"\bmatarme\b", "self-harm term (ES)"),
        (r"\bcortarme\b", "self-harm term (ES)"),
        (r"\bquieromorir\b", "self-harm term (ES)"),
        (r"\bautolesi[oó]n\b", "self-harm term (ES)"),
    ],
    # ----- DEROGATORY -----
    "DEROGATORY": [
        # -- English --
        (r"\bretard(ed)?\b", "ableist slur (EN)"),
        (r"\bspaz\b", "ableist slur (EN)"),
        (r"\bcripple\b", "ableist slur (EN)"),
        (r"\bimbecile\b", "derogatory (EN)"),
        (r"\bmoron\b", "derogatory (EN)"),
        # -- Spanish --
        (r"\bretrasado\b", "ableist slur (ES)"),
        (r"\bpendejo\b", "profanity (ES)"),
        (r"\bputo\b", "profanity (ES)"),
        (r"\bputa\b", "profanity (ES)"),
        (r"\best[uú]pido\b", "derogatory (ES)"),
        (r"\bidiota\b", "derogatory (ES)"),
        (r"\bimbecil\b", "derogatory (ES)"),
        (r"\btarado\b", "derogatory (ES)"),
        (r"\bmenso\b", "derogatory (ES)"),
    ],
}

# Substring checks against the *letters-only* normalised form.
# These catch heavily obfuscated variants (e.g. n_i_g_g_e_r, p.u" t.a).
_SUBSTR_CHECKS: Dict[str, List[Tuple[str, str]]] = {
    "HATE_SPEECH": [
        ("nigger", "racial slur"),
        ("nigga", "racial slur"),
        ("faggot", "orientation slur"),
        ("kike", "antisemitic slur"),
        ("wetback", "ethnic slur"),
        ("beaner", "ethnic slur"),
        ("chink", "ethnic slur"),
        ("maricon", "orientation slur (ES)"),
        ("nazi", "Nazi reference"),
        ("hitler", "Nazi reference"),
        ("hltler", "Nazi reference (leet)"),
    ],
    "SEXUAL_CONTENT": [
        ("porn", "explicit term"),
        ("hentai", "explicit term"),
        ("cocksucker", "explicit term"),
    ],
    "VIOLENCE": [
        ("schoolshooter", "violence term"),
        ("massmurder", "violence term"),
        ("killer", "violence term"),
    ],
    "SELF_HARM": [
        ("killmyself", "self-harm term"),
        ("iwanttodie", "self-harm term"),
        ("quieromorir", "self-harm term (ES)"),
    ],
    "DEROGATORY": [],
}


# ---------------------------------------------------------------------------
# Compiled pattern cache (built once at module load)
# ---------------------------------------------------------------------------

_COMPILED_PATTERNS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    category: [(re.compile(pat), desc) for pat, desc in entries]
    for category, entries in _CATEGORY_PATTERNS.items()
}


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

_SEVERITY_MAP: Dict[str, str] = {
    "HATE_SPEECH": "high",
    "SEXUAL_CONTENT": "high",
    "VIOLENCE": "high",
    "SELF_HARM": "high",
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
        violations: List[Dict[str, str]] = []

        for category, patterns in _COMPILED_PATTERNS.items():
            for regex, desc in patterns:
                if regex.search(lightly):
                    violations.append(
                        {
                            "field": field_name,
                            "category": category,
                            "pattern_matched": desc,
                        }
                    )
                    break  # one match per category is enough

            # Substring check on letters-only form
            if category not in [v["category"] for v in violations]:
                for substr, desc in _SUBSTR_CHECKS.get(category, []):
                    if substr in letters_only:
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

        # Log blocked attempts
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
