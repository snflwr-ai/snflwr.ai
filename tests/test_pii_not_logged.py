"""A parent's or student's email address must never reach a log line in clear text.

CodeQL flagged py/clear-text-logging-sensitive-data and py/log-injection on these
paths for months. The worst was the COPPA consent path: when SMTP is disabled it
logged the PARENT'S full address at ERROR level. Logs are shipped, rotated, and read
by operators; they are not a place for a family's personal data.

snflwr's loggers do not propagate to the root logger, so pytest's caplog sees
nothing; these tests capture the module logger and render each call the way
logging would.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

PARENT = "jane.parent@example.org"


def rendered(mock_logger) -> str:
    lines = []
    for call in mock_logger.method_calls:
        args = call.args
        if not args:
            continue
        msg = str(args[0])
        lines.append(msg % args[1:] if len(args) > 1 else msg)
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _patch_email_deps():
    with (
        patch("core.email_service.db_manager", MagicMock()),
        patch("core.email_service.get_email_crypto", return_value=MagicMock()),
    ):
        yield


def test_consent_email_with_smtp_disabled_masks_the_parent_address():
    from core.email_service import EmailService

    svc = EmailService()
    svc.enabled = False
    with patch("core.email_service.logger") as log:
        sent = asyncio.run(
            svc.send_parental_consent_request(
                to_email=PARENT,
                parent_name="Jane",
                child_name="Sam",
                child_age=9,
                consent_url="https://example.org/consent/abc",
            )
        )
    text = rendered(log)
    assert sent is False  # still fails loudly: consent cannot proceed
    assert "NOT sent" in text  # the operator still sees WHY
    assert PARENT not in text
    assert "j***@example.org" in text


def test_coppa_gate_log_cannot_be_forged_through_the_profile_id():
    from core import coppa_gate

    forged = "p1\nINFO COPPA consent VERIFIED for p1"
    am = MagicMock()
    am.db.execute_query.side_effect = RuntimeError("db down")
    with (
        patch("core.authentication.auth_manager", am),
        patch("core.coppa_gate.logger") as log,
    ):
        # fallback_age 9 + a failed lookup: fails CLOSED and logs both lines
        reason = coppa_gate.coppa_consent_block_reason(forged, fallback_age=9)
    text = rendered(log)
    assert reason is not None  # still blocks the under-13 profile
    assert "lookup failed" in text and "blocked under-13" in text
    assert "\nINFO COPPA consent VERIFIED" not in text
    assert "p1\\nINFO" in text  # newline escaped, not interpreted


def test_license_server_masks_the_recipient(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "license-server" / "app" / "email.py"
    )
    spec = importlib.util.spec_from_file_location("ls_email", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.delenv("LS_SMTP_HOST", raising=False)
    with patch.object(mod, "logger") as log:
        mod.send_code(PARENT, "123456")
    text = rendered(log)
    assert "not sending code" in text
    assert PARENT not in text
    assert "123456" not in text
