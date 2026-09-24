"""The parental-consent email is COPPA's "direct notice" (16 CFR §312.4(c)(1),
as amended; compliance date 2026-04-22). It must tell the parent what will be
collected from the child, how it will be used, who else receives it and why,
that consent is required, and link the online privacy notice. It must not make
claims the product cannot back (the old footer said "COPPA Compliant" and
"Offline Operation", neither of which the email can promise)."""

from unittest.mock import patch

import pytest


@pytest.fixture
def svc():
    with patch("core.email_service.system_config") as cfg, patch(
        "core.email_service.db_manager"
    ), patch("core.email_service.get_email_crypto"):
        cfg.SMTP_ENABLED = True
        cfg.SMTP_HOST = "smtp.test"
        cfg.SMTP_PORT = 587
        cfg.SMTP_USE_TLS = False
        cfg.SMTP_USERNAME = ""
        cfg.SMTP_PASSWORD = ""
        cfg.SMTP_FROM_EMAIL = "f@test"
        cfg.SMTP_FROM_NAME = "t"
        cfg.PRIVACY_POLICY_URL = "https://snflwr.example/privacy"
        from core.email_service import EmailService

        s = EmailService()
        yield s, cfg


async def _render(service):
    with patch.object(service, "_send_email", return_value=(True, None)) as send:
        ok = await service.send_parental_consent_request(
            to_email="p@test",
            parent_name="Pat",
            child_name="Sam",
            child_age=9,
            consent_url="https://snflwr.example/consent?token=t",
        )
    assert ok is True
    return send.call_args.kwargs["html_body"]


@pytest.mark.asyncio
async def test_notice_states_collection_use_and_recipients(svc):
    html = await _render(svc[0])
    assert "What we collect from Sam" in html
    assert "questions Sam asks" in html
    assert "Who else receives it" in html
    assert "never" in html and "train or develop AI models" in html
    assert "separate consent" in html
    assert "Your consent is required" in html
    assert "180 days" in html


@pytest.mark.asyncio
async def test_notice_links_privacy_policy_when_configured(svc):
    html = await _render(svc[0])
    assert 'href="https://snflwr.example/privacy"' in html


@pytest.mark.asyncio
async def test_notice_omits_link_when_policy_unpublished(svc):
    service, cfg = svc
    cfg.PRIVACY_POLICY_URL = ""
    html = await _render(service)
    assert "privacy notice:" not in html


@pytest.mark.asyncio
async def test_unbacked_claims_are_gone(svc):
    html = await _render(svc[0])
    for claim in (
        "COPPA Compliant",
        "Offline Operation",
        "no cloud sharing",
        "4-layer",
    ):
        assert claim not in html
