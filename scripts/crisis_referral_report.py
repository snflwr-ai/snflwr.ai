"""Print the numbers for California's annual SB 243 report.

Bus. & Prof. Code §22603: beginning 2027-07-01, an operator reports each year to
the Office of Suicide Prevention (1) how many crisis service provider referral
notifications it issued in the PRECEDING calendar year, (2) its protocols to
detect, remove and respond to suicidal ideation, and (3) its protocols to
prohibit companion-chatbot responses about suicidal ideation or actions. The
report may contain no user identifiers or personal information (§22603(b)).

This prints (1). (2) and (3) are the published crisis-protocol page.

    python3 scripts/crisis_referral_report.py            # preceding year
    python3 scripts/crisis_referral_report.py --year 2027
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now(timezone.utc).year - 1,
        help="calendar year to report (default: the preceding year)",
    )
    args = parser.parse_args(argv)

    from safety import crisis_referral_counter
    from storage.database import db_manager

    counts = crisis_referral_counter.year_counts(db_manager, args.year)
    total = sum(counts.values())
    print(
        f"Crisis service provider referral notifications issued in {args.year}: {total}"
    )
    for kind in sorted(crisis_referral_counter.REFERRAL_INCIDENT_TYPES):
        print(f"  {kind}: {counts.get(kind, 0)}")
    if args.year < 2026:
        print(
            "  (note: counting began with migration 0005; earlier years are not recorded)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
