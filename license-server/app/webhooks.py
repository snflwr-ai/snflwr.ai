import hashlib
import hmac
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, HTTPException
from app.config import settings
from app import db, store

router = APIRouter()

_STATUS_MAP = {
    "active": "active",
    "on_trial": "trialing",
    "past_due": "past_due",
    "cancelled": "canceled",
    "expired": "revoked",
    "unpaid": "revoked",
}


def verify_signature(raw_body: bytes, signature_hex: str, secret: str) -> bool:
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, signature_hex.strip())
    except Exception:
        return False


def _parse_period_end(attributes: dict) -> int:
    raw = attributes.get("renews_at") or attributes.get("ends_at")
    if not raw:
        return 0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp())
    except Exception:
        return 0


def map_event(event_name: str, attributes: dict) -> dict:
    if event_name == "subscription_payment_refunded":
        status = "revoked"
    else:
        ls_status = (attributes.get("status") or "").lower()
        status = _STATUS_MAP.get(ls_status, "revoked")
    return {"status": status, "plan": "family",
            "current_period_end": _parse_period_end(attributes)}


# Engine/session created once at import for the running service.
_engine = db.make_engine(settings.DATABASE_URL)
_Session = db.make_session_factory(_engine)


@router.post("/webhooks/mor")
async def receive_webhook(request: Request, x_signature: str = Header(default="")):
    raw = await request.body()
    if not verify_signature(raw, x_signature, settings.LS_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = json.loads(raw)
    event_name = payload.get("meta", {}).get("event_name", "")
    data = payload.get("data", {})
    attributes = data.get("attributes", {})
    email = (attributes.get("user_email") or "").strip().lower()
    ls_sub_id = str(data.get("id", ""))
    if not email or not event_name.startswith("subscription"):
        return {"ok": True, "ignored": True}
    mapped = map_event(event_name, attributes)
    now = int(datetime.now(timezone.utc).timestamp())
    async with _Session() as s:
        await store.upsert_subscription(
            s, email=email, ls_subscription_id=ls_sub_id, plan=mapped["plan"],
            status=mapped["status"], current_period_end=mapped["current_period_end"], now=now)
        await s.commit()
    return {"ok": True}
