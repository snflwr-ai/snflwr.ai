"""App-side billing endpoints.

Thin proxy between the local onboarding UI and the cloud License Server, plus
local license-state reporting. Admin-only: in a self-hosted family deploy the
parent/admin manages the subscription. Holds no card data (Lemon Squeezy hosts
checkout) and no student data.
"""
import time
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from config import system_config
from core import licensing
from api.middleware.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


class StartReq(BaseModel):
    email: EmailStr


class VerifyReq(BaseModel):
    email: EmailStr
    code: str


def _ls_base() -> str:
    if not system_config.LICENSE_SERVER_URL:
        raise HTTPException(status_code=503, detail="license server not configured")
    return system_config.LICENSE_SERVER_URL.rstrip("/")


@router.post("/signin/start")
def signin_start(req: StartReq):
    with httpx.Client(timeout=10.0) as c:
        r = c.post(_ls_base() + "/auth/start", json={"email": str(req.email)}, timeout=10.0)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="could not send code")
    return {"ok": True}


@router.post("/signin/verify")
def signin_verify(req: VerifyReq):
    with httpx.Client(timeout=10.0) as c:
        r = c.post(_ls_base() + "/auth/verify",
                   json={"email": str(req.email), "code": req.code}, timeout=10.0)
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid or expired code")
    licensing.store_session(r.json()["session"])
    licensed = licensing.refresh_once()
    return {"ok": True, "licensed": bool(licensed)}


@router.get("/status")
def billing_status():
    st = licensing.current_state(int(time.time()))
    return {"state": st.state, "allowed": st.allowed, "plan": st.plan, "exp": st.exp}


@router.get("/checkout-url")
def checkout_url():
    return {"url": system_config.LS_CHECKOUT_URL}
