"""SB 243 §22603 asks for the yearly number of crisis-line referrals. The
incident rows that carry them are purged 90 days after resolution, so the count
lives in its own table (migration 0005) and survives that purge."""

import importlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from safety import crisis_referral_counter as crc


class _Db:
    """Minimal execute_write/execute_query over a real in-memory SQLite."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:")
        importlib.import_module("database.migrations.0005_crisis_referral_counts").up(
            self.con.cursor(), "sqlite"
        )

    def execute_write(self, sql, params=()):
        self.con.execute(sql, params)
        self.con.commit()

    def execute_query(self, sql, params=()):
        cur = self.con.execute(sql, params)
        return cur.fetchall()


def test_migration_is_idempotent():
    con = sqlite3.connect(":memory:")
    mod = importlib.import_module("database.migrations.0005_crisis_referral_counts")
    mod.up(con.cursor(), "sqlite")
    mod.up(con.cursor(), "sqlite")
    cols = [r[1] for r in con.execute("PRAGMA table_info(crisis_referral_counts)")]
    assert cols == ["year", "kind", "count"]


def test_counts_referral_types_only():
    db = _Db()
    for t in ("self_harm", "self_harm", "disclosure_suicidal_ideation", "violence"):
        crc.record(db, t)
    year = crc.datetime.now(crc.timezone.utc).year
    assert crc.year_counts(db, year) == {
        "self_harm": 2,
        "disclosure_suicidal_ideation": 1,
    }


def test_count_holds_no_identifiers():
    db = _Db()
    crc.record(db, "self_harm")
    cols = [r[1] for r in db.con.execute("PRAGMA table_info(crisis_referral_counts)")]
    assert not {"profile_id", "session_id", "content", "timestamp"} & set(cols)


def test_record_never_raises():
    broken = MagicMock()
    broken.execute_write.side_effect = RuntimeError("db down")
    crc.record(broken, "self_harm")  # must not raise


def test_incident_logger_counts_before_insert():
    """The count is written BEFORE the incident insert, so a referral the child
    already saw is counted even if the insert then fails."""
    from safety.incident_logger import IncidentLogger

    db = MagicMock()
    db.execute_query.return_value = []
    il = IncidentLogger(db=db)
    with patch.object(il, "encryption"):
        il.log_incident(
            profile_id="p1",
            incident_type="self_harm",
            severity="critical",
            content_snippet="x",
            send_alert=False,
        )
    first_sql = db.execute_write.call_args_list[0].args[0]
    assert "crisis_referral_counts" in first_sql


@pytest.mark.parametrize(
    "kind",
    ["suicidal_ideation", "predatory_contact", "bullying_victim", "disordered_eating"],
)
def test_every_disclosure_kind_uses_a_severity_the_logger_accepts(kind):
    """The logger rejects anything but minor/major/critical. Disclosures used to
    send "moderate", so bullying and disordered-eating were never recorded."""
    from api.routes.ollama_proxy import blocks

    with patch("safety.incident_logger.incident_logger") as il:
        blocks._record_disclosure_incident("p1", kind, "m", "text")
    assert il.log_incident.call_args.kwargs["severity"] in {
        "minor",
        "major",
        "critical",
    }
