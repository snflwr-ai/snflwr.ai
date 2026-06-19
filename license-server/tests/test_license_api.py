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
