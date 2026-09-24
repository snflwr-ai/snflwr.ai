"""Yearly count of crisis-line referrals, kept past incident retention.

The child sees a 988 referral in two situations:

* a message is BLOCKED as self-harm -> ``get_safe_response`` always serves the
  988 text (incident_type ``self_harm``);
* a suicidal-ideation DISCLOSURE is answered and the crisis suffix with 988 is
  appended (incident_type ``disclosure_suicidal_ideation``).

Both are logged through ``incident_logger.log_incident``, which calls
``record`` here. SB 243 §22603 asks for the yearly number; see migration 0005
for why the incident rows themselves cannot be the source of that number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from utils.logger import get_logger

logger = get_logger(__name__)

REFERRAL_INCIDENT_TYPES = frozenset({"self_harm", "disclosure_suicidal_ideation"})

_UPSERT = (
    "INSERT INTO crisis_referral_counts (year, kind, count) VALUES (?, ?, 1) "
    "ON CONFLICT (year, kind) DO UPDATE SET count = crisis_referral_counts.count + 1"
)


def record(db, incident_type: str) -> None:
    """Count one referral if this incident type carries one. Never raises."""
    if incident_type not in REFERRAL_INCIDENT_TYPES:
        return
    try:
        db.execute_write(_UPSERT, (datetime.now(timezone.utc).year, incident_type))
    except Exception as exc:
        logger.error("Crisis-referral count NOT recorded (%s): %s", incident_type, exc)


def year_counts(db, year: int) -> Dict[str, int]:
    """Referral counts for one calendar year, by kind."""
    rows = db.execute_query(
        "SELECT kind, count FROM crisis_referral_counts WHERE year = ?", (year,)
    )
    out: Dict[str, int] = {}
    for row in rows or []:
        kind = row["kind"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        out[kind] = int(count)
    return out
