import hashlib
import secrets
import time
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr
from app.config import settings
from app import store, db, email, tokens

router = APIRouter()

# Reuse the signing key for session tokens (signed, stateless, short-lived).
_priv = None
_pub = None


def _keys():
    global _priv, _pub
    if _priv is None:
        _priv = tokens.load_private_key(settings.SIGNING_KEY_PATH)
        _pub = _priv.public_key()
    return _priv, _pub


def _gen_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_session_token(account_id: str) -> str:
    priv, _ = _keys()
    return tokens.encode_token(
        {"acct": account_id, "exp": int(time.time()) + settings.SESSION_TTL_SECONDS}, priv)


def read_session_token(token: str):
    _, pub = _keys()
    try:
        payload = tokens.verify_token(token, pub)
    except tokens.TokenError:
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload.get("acct")


_engine = db.make_engine(settings.DATABASE_URL)
_Session = db.make_session_factory(_engine)


class StartReq(BaseModel):
    email: EmailStr


class VerifyReq(BaseModel):
    email: EmailStr
    code: str


@router.post("/auth/start")
async def auth_start(req: StartReq):
    code = _gen_code()
    async with _Session() as s:
        await store.set_auth_code(
            s, str(req.email), hash_code(code), int(time.time()) + settings.CODE_TTL_SECONDS)
        await s.commit()
    email.send_code(str(req.email), code)
    return {"ok": True}


@router.post("/auth/verify")
async def auth_verify(req: VerifyReq):
    async with _Session() as s:
        row = await store.pop_auth_code(s, str(req.email))
        await s.commit()
    if row is None or row.expires_at < int(time.time()) or row.code_hash != hash_code(req.code):
        raise HTTPException(status_code=401, detail="invalid or expired code")
    return {"session": new_session_token(store.account_id_for_email(str(req.email)))}


def get_session_account(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    acct = read_session_token(token)
    if not acct:
        raise HTTPException(status_code=401, detail="invalid session")
    return acct
