import time
from fastapi import APIRouter, Depends, HTTPException
from app.config import settings
from app import store, db, tokens, auth

router = APIRouter()

_engine = db.make_engine(settings.DATABASE_URL)
_Session = db.make_session_factory(_engine)

_ENTITLED = {"active", "trialing", "past_due"}


def issue_license_token(account_id, plan, status, now, *, trial: bool) -> str:
    priv, _ = auth._keys()
    life = 10 * 86400 if trial else 30 * 86400
    return tokens.encode_token({
        "sub": account_id, "plan": plan,
        "status": "trialing" if trial else status,
        "iat": int(now), "exp": int(now) + life,
        "grace_days": 14, "device_id": None,
    }, priv)


@router.post("/license/refresh")
async def refresh(account_id: str = Depends(auth.get_session_account)):
    async with _Session() as s:
        sub = await store.get_subscription(s, account_id)
    if sub is None or sub.status not in _ENTITLED:
        raise HTTPException(status_code=402, detail="no active subscription")
    trial = sub.status == "trialing"
    token = issue_license_token(account_id, sub.plan, sub.status, time.time(), trial=trial)
    return {"token": token}


@router.get("/license/status")
async def status(account_id: str = Depends(auth.get_session_account)):
    async with _Session() as s:
        sub = await store.get_subscription(s, account_id)
    if sub is None:
        return {"status": "none", "current_period_end": 0, "plan": None}
    return {"status": sub.status, "current_period_end": sub.current_period_end, "plan": sub.plan}
