import time
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app import auth, store, license_api, tokens


def _session_for(email):
    return auth.new_session_token(store.account_id_for_email(email))


def test_issue_token_lifetimes():
    paid = license_api.issue_license_token("acct_1", "family", "active", 1000, trial=False)
    _, pub = auth._keys()
    p = tokens.verify_token(paid, pub)
    assert p["exp"] - p["iat"] == 30 * 86400
    assert p["grace_days"] == 14
    trial = license_api.issue_license_token("acct_1", "family", "trialing", 1000, trial=True)
    t = tokens.verify_token(trial, pub)
    assert t["exp"] - t["iat"] == 10 * 86400


def test_token_exp_capped_at_period_end():
    """A paid token must not out-live current_period_end (cancel over-grant fix)."""
    _, pub = auth._keys()
    now = 1000
    period_end = now + 5 * 86400  # paid through 5 days from now, < 30-day life
    tok = license_api.issue_license_token(
        "acct_1", "family", "active", now, trial=False, current_period_end=period_end)
    p = tokens.verify_token(tok, pub)
    assert p["exp"] == period_end  # clamped, not now + 30d


def test_token_exp_not_extended_for_healthy_sub():
    """When period_end is past now+life, the clamp is a no-op (full 30 days)."""
    _, pub = auth._keys()
    now = 1000
    far = now + 365 * 86400
    tok = license_api.issue_license_token(
        "acct_1", "family", "active", now, trial=False, current_period_end=far)
    p = tokens.verify_token(tok, pub)
    assert p["exp"] == now + 30 * 86400


def test_trial_token_ignores_period_end():
    _, pub = auth._keys()
    now = 1000
    tok = license_api.issue_license_token(
        "acct_1", "family", "trialing", now, trial=True, current_period_end=now + 1)
    p = tokens.verify_token(tok, pub)
    assert p["exp"] == now + 10 * 86400


def test_refresh_402_without_subscription():
    c = TestClient(create_app())
    tok = _session_for("nobody@x.com")
    r = c.post("/license/refresh", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 402


@pytest.mark.asyncio
async def test_refresh_issues_token_for_active():
    async with license_api._Session() as s:
        await store.upsert_subscription(
            s, email="paid@x.com", ls_subscription_id="sub_9", plan="family",
            status="active", current_period_end=int(time.time()) + 999999, now=int(time.time()))
        await s.commit()
    c = TestClient(create_app())
    tok = _session_for("paid@x.com")
    r = c.post("/license/refresh", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    _, pub = auth._keys()
    payload = tokens.verify_token(r.json()["token"], pub)
    assert payload["status"] == "active"
