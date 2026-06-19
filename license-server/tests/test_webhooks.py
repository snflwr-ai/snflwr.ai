import hashlib
import hmac
import pytest
from app import webhooks


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_good_and_bad():
    body = b'{"x":1}'
    sig = _sign(body, "s3cr3t")
    assert webhooks.verify_signature(body, sig, "s3cr3t") is True
    assert webhooks.verify_signature(body, sig, "wrong") is False
    assert webhooks.verify_signature(body, "nothex!!", "s3cr3t") is False


@pytest.mark.parametrize("ls_status,expected", [
    ("active", "active"), ("on_trial", "trialing"),
    ("past_due", "past_due"), ("cancelled", "canceled"),
    ("expired", "revoked"), ("unpaid", "revoked"),
])
def test_map_event_status(ls_status, expected):
    out = webhooks.map_event("subscription_updated",
                             {"status": ls_status, "ends_at": None, "renews_at": "2030-01-01T00:00:00Z"})
    assert out["status"] == expected
    assert out["plan"] == "family"


def test_map_event_refund():
    out = webhooks.map_event("subscription_payment_refunded", {"status": "active"})
    assert out["status"] == "revoked"
