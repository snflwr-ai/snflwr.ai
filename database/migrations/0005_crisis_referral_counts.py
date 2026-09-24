"""0005 — durable yearly count of crisis-line referrals.

California SB 243 (Bus. & Prof. Code §22603), beginning 2027-07-01, requires an
annual report to the Office of Suicide Prevention of "the number of times the
operator has issued a crisis service provider referral notification ... in the
preceding calendar year." The incidents that carry those referrals are purged
90 days after resolution (SAFETY_LOG_RETENTION_DAYS), so counting them at
report time would undercount. This table keeps only a number per year and
kind — no profile, no content, no timestamp finer than the year.
Idempotent."""

revision = "0005"
name = "crisis_referral_counts"

_TABLE = """
CREATE TABLE IF NOT EXISTS crisis_referral_counts (
    year INTEGER NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (year, kind)
)
"""


def up(cursor, dialect):
    cursor.execute(_TABLE)
