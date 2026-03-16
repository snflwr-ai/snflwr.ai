"""
Account Creation Safety Filter - Bilingual (English / Spanish)

Standalone, pure-Python keyword filter for account registration flows.
Blocks offensive, derogatory, hateful, violent, sexual, and self-harm
language in usernames, display names, bios, and email local parts.

Adapted from snflwr.ai's openwebui_safety_filter_age_adaptive.py and
safety/pipeline.py normalization logic.  No external ML model required.

All pattern definitions, normalisation constants, and the false-positive
allowlist live in safety/patterns.py (single source of truth).

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
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from safety.patterns import (
    normalize_text as _normalize_text,
    COMPILED_PATTERNS as _COMPILED_PATTERNS,
    SUBSTR_CHECKS as _SUBSTR_CHECKS,
    FALSE_POSITIVE_ALLOWLIST as _FALSE_POSITIVE_ALLOWLIST,
    SEVERITY_MAP as _SEVERITY_MAP,
)


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
        # Split on underscores/hyphens/dots/spaces to get individual tokens.
        input_words = set(re.sub(r"[_\-.~\s]+", " ", text_lower).split())
        allowlisted_words = input_words & _FALSE_POSITIVE_ALLOWLIST

        for category, patterns in _COMPILED_PATTERNS.items():
            matched = False
            for regex, desc in patterns:
                hit_lightly = regex.search(lightly)
                hit_pre = regex.search(pre_norm)
                if hit_lightly or hit_pre:
                    # If the regex matched inside an allowlisted word,
                    # treat it as a false positive and skip.
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
                    break  # one match per category is enough

            # Substring check on letters-only form (skip if allowlisted)
            if not matched:
                for substr, desc in _SUBSTR_CHECKS.get(category, []):
                    if substr in letters_only:
                        # Skip if substring matches inside an allowlisted word
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
