# Billing — Self-Hosted Subscription Licensing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require an active Lemon Squeezy subscription to use the tutor, enforced offline on customer hardware via an Ed25519-signed license token, with a new first-party License Server that issues those tokens.

**Architecture:** Two independently testable subsystems bound by one token-format contract. (A) A new `license-server/` FastAPI service consumes Lemon Squeezy webhooks, authenticates accounts by email one-time code, and **signs** short-lived license tokens with an Ed25519 private key. (B) The existing self-hosted app gains `core/licensing.py` which **verifies** those tokens offline with a bundled public key, a background refresh task, and a license **gate** in `api/routes/ollama_proxy.py:proxy_chat` mirroring the existing safety gate. Phases 1–2 are built against Lemon Squeezy **test mode** — no business entity required until go-live.

**Tech Stack:** Python 3.11, FastAPI, `cryptography` (Ed25519, already pinned `==48.0.1`), `httpx` (already pinned `==0.28.1`), SQLAlchemy 2.0 + aiosqlite/asyncpg (license-server only), pytest. No `pyjwt` — token codec is a ~30-line custom Ed25519-signed compact format implemented identically on both sides.

## Global Constraints

- **MoR:** Lemon Squeezy (test mode for all of Phases 1–2). Webhook signature = HMAC-SHA256 of the raw request body with the store webhook secret, sent in the `X-Signature` header (hex).
- **Signing:** Ed25519 only. Private key lives **only** on the License Server. The app ships the **public** key and never holds the private key.
- **Token format (CONTRACT — identical both sides):** `b64url(payload_json_bytes) + "." + b64url(signature_bytes)`. `b64url` is base64-urlsafe **without** padding. `payload_json_bytes` is `json.dumps(payload, separators=(",",":"), sort_keys=True).encode()`. Signature is Ed25519 over `payload_json_bytes` (the pre-base64 bytes).
- **Token payload fields:** `{"sub": str, "plan": "family", "status": "active"|"trialing", "iat": int, "exp": int, "grace_days": int, "device_id": str|null}`. Times are integer Unix seconds (UTC).
- **Token lifetimes:** paid token `exp = iat + 30*86400`; trial token `exp = iat + 10*86400`; `grace_days = 14`.
- **Pricing (display/config only):** $9.99/mo, $89/yr. 10-day trial.
- **Fail-safe (NON-NEGOTIABLE):** any licensing error (missing/corrupt/expired/bad-sig token, server down) → state is *unlicensed/gated*, **never** a 500 or crash. Admins are never gated. Only `/api/chat` tutoring is gated; settings/dashboard/billing stay usable when lapsed.
- **Privacy:** the License Server stores billing email + subscription status only. **Never** student data.
- **Secrets:** never print key material or webhook secrets to logs/output. Check presence only.
- **No new deps in the main app** beyond what is already pinned. `license-server/` has its own `requirements.txt`.

---

## File Structure

**License Server (new — `license-server/`):**
- `license-server/app/__init__.py` — package marker
- `license-server/app/config.py` — env-driven settings (DB URL, signing key path, LS webhook secret, SMTP)
- `license-server/app/tokens.py` — Ed25519 **sign** + the shared codec (encode/decode/verify)
- `license-server/app/keygen.py` — CLI to generate the Ed25519 keypair (Phase 0 setup)
- `license-server/app/db.py` — SQLAlchemy engine/session, table metadata
- `license-server/app/models.py` — `accounts`, `subscriptions`, `auth_codes`, `activations`
- `license-server/app/store.py` — data-access functions (upsert subscription, account-by-email, code CRUD)
- `license-server/app/webhooks.py` — Lemon Squeezy receiver + signature verify + event→status mapping
- `license-server/app/auth.py` — `/auth/start`, `/auth/verify` (email one-time code → session)
- `license-server/app/license_api.py` — `/license/refresh`, `/license/status` (issue/return token)
- `license-server/app/email.py` — minimal SMTP sender for one-time codes
- `license-server/app/main.py` — FastAPI app wiring all routers + `/health`
- `license-server/requirements.txt` — fastapi, uvicorn, cryptography, sqlalchemy, aiosqlite, asyncpg, httpx, pydantic, python-multipart
- `license-server/tests/` — pytest suite (sqlite-backed)
- `license-server/README.md` — deploy notes (Fly/Render/Cloud Run + managed Postgres)

**Self-hosted app (existing):**
- Create: `core/licensing.py` — token **store**, offline **verify**, **state** evaluation, **refresh** client
- Create: `api/routes/billing.py` — app-side proxy endpoints onboarding calls (`/api/billing/...`)
- Create: `config/license_public_key.pem` — bundled Ed25519 public key (placeholder until Phase 0 keygen)
- Modify: `config.py` — add `_SystemConfig` license fields
- Modify: `api/routes/ollama_proxy.py:proxy_chat` — insert license gate after admin bypass
- Modify: `api/server.py` — register `billing.router`; start refresh task in `lifespan`
- Create: `tasks/license_refresh.py` — periodic refresh task body
- Test: `tests/test_licensing.py`, `tests/test_license_gate.py`, `tests/test_billing_routes.py`

> **Scope note:** Subsystem A (Tasks 1–8) and Subsystem B (Tasks 9–15) are each independently testable and deployable. They share only the **Token format contract** above. You can build, test, and ship A entirely before B. Phase 3 (legal/copy) and Phase 4 (polish: dunning banners, device-binding) from the spec are **out of scope** for this plan — tracked separately.

---

# Subsystem A — License Server (`license-server/`)

### Task 1: Server scaffold, config, health endpoint

**Files:**
- Create: `license-server/requirements.txt`
- Create: `license-server/app/__init__.py`
- Create: `license-server/app/config.py`
- Create: `license-server/app/main.py`
- Create: `license-server/tests/__init__.py`
- Create: `license-server/tests/conftest.py`
- Test: `license-server/tests/test_health.py`

**Interfaces:**
- Produces: `app.config.settings` (object with `.DATABASE_URL: str`, `.SIGNING_KEY_PATH: str`, `.LS_WEBHOOK_SECRET: str`, `.SESSION_TTL_SECONDS: int`, `.CODE_TTL_SECONDS: int`); `app.main.app` (FastAPI instance); `app.main.create_app() -> FastAPI`.

- [ ] **Step 1: Write `license-server/requirements.txt`**

```
fastapi==0.135.1
uvicorn[standard]==0.34.3
cryptography==48.0.1
SQLAlchemy==2.0.36
aiosqlite==0.20.0
asyncpg==0.30.0
httpx==0.28.1
pydantic==2.12.5
python-multipart==0.0.18
pytest==8.3.4
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Write the failing health test**

`license-server/tests/test_health.py`:
```python
from fastapi.testclient import TestClient
from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: Run it, verify it fails**

Run: `cd license-server && python -m pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 4: Write config**

`license-server/app/config.py`:
```python
import os
from dataclasses import dataclass


@dataclass
class _Settings:
    DATABASE_URL: str = os.getenv("LS_DATABASE_URL", "sqlite+aiosqlite:///./license.db")
    SIGNING_KEY_PATH: str = os.getenv("LS_SIGNING_KEY_PATH", "./signing_key.pem")
    LS_WEBHOOK_SECRET: str = os.getenv("LS_WEBHOOK_SECRET", "")
    SESSION_TTL_SECONDS: int = int(os.getenv("LS_SESSION_TTL_SECONDS", "3600"))
    CODE_TTL_SECONDS: int = int(os.getenv("LS_CODE_TTL_SECONDS", "600"))


settings = _Settings()
```

- [ ] **Step 5: Write app factory + health**

`license-server/app/__init__.py`: empty.
`license-server/app/main.py`:
```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="snflwr.ai License Server")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 6: Write `tests/__init__.py` (empty) and `tests/conftest.py`**

`license-server/tests/conftest.py`:
```python
import os
import sys

# Make `app` importable when running pytest from license-server/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 7: Run test, verify pass**

Run: `cd license-server && python -m pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add license-server/
git commit -m "feat(license-server): scaffold FastAPI app with health endpoint"
```

---

### Task 2: Ed25519 keypair generator (Phase 0 setup)

**Files:**
- Create: `license-server/app/keygen.py`
- Test: `license-server/tests/test_keygen.py`

**Interfaces:**
- Produces: `app.keygen.generate_keypair() -> tuple[bytes, bytes]` returning `(private_pem, public_pem)`; `app.keygen.main(out_dir: str) -> None` writing `signing_key.pem` (0600) + `license_public_key.pem`.

- [ ] **Step 1: Write failing test**

`license-server/tests/test_keygen.py`:
```python
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from app.keygen import generate_keypair


def test_generate_keypair_roundtrip():
    priv_pem, pub_pem = generate_keypair()
    priv = load_pem_private_key(priv_pem, password=None)
    pub = load_pem_public_key(pub_pem)
    sig = priv.sign(b"hello")
    pub.verify(sig, b"hello")  # raises if mismatch
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_keygen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.keygen'`

- [ ] **Step 3: Implement**

`license-server/app/keygen.py`:
```python
import os
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def generate_keypair() -> tuple[bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def main(out_dir: str = ".") -> None:
    priv_pem, pub_pem = generate_keypair()
    priv_path = os.path.join(out_dir, "signing_key.pem")
    pub_path = os.path.join(out_dir, "license_public_key.pem")
    with open(priv_path, "wb") as f:
        f.write(priv_pem)
    os.chmod(priv_path, 0o600)
    with open(pub_path, "wb") as f:
        f.write(pub_pem)
    print(f"Wrote {priv_path} (0600) and {pub_path}")
    print("Bundle license_public_key.pem into the app at config/license_public_key.pem")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
```

- [ ] **Step 4: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_keygen.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add license-server/app/keygen.py license-server/tests/test_keygen.py
git commit -m "feat(license-server): Ed25519 keypair generator"
```

---

### Task 3: Token codec — encode, sign, decode, verify (the contract)

**Files:**
- Create: `license-server/app/tokens.py`
- Test: `license-server/tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `app.tokens.encode_token(payload: dict, private_key) -> str`
  - `app.tokens.verify_token(token: str, public_key) -> dict` — returns payload; raises `app.tokens.TokenError` on malformed/bad-signature (does **not** check exp).
  - `app.tokens.TokenError(Exception)`
  - `app.tokens.load_private_key(path: str)`, `app.tokens.load_public_key(path: str)`
- This codec is the CONTRACT. `core/licensing.py` in Subsystem B re-implements `verify_token` byte-identically.

- [ ] **Step 1: Write failing tests**

`license-server/tests/test_tokens.py`:
```python
import pytest
from app.keygen import generate_keypair
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from app import tokens


def _keys():
    priv_pem, pub_pem = generate_keypair()
    return load_pem_private_key(priv_pem, None), load_pem_public_key(pub_pem)


def test_encode_verify_roundtrip():
    priv, pub = _keys()
    payload = {"sub": "acct_1", "plan": "family", "status": "active",
               "iat": 1750200000, "exp": 1752792000, "grace_days": 14, "device_id": None}
    tok = tokens.encode_token(payload, priv)
    assert tokens.verify_token(tok, pub) == payload


def test_tampered_payload_rejected():
    priv, pub = _keys()
    tok = tokens.encode_token({"sub": "a", "iat": 1}, priv)
    head, sig = tok.split(".")
    bad = tokens.encode_token({"sub": "b", "iat": 1}, priv).split(".")[0] + "." + sig
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(bad, pub)


def test_wrong_key_rejected():
    priv, _ = _keys()
    _, other_pub = _keys()
    tok = tokens.encode_token({"sub": "a", "iat": 1}, priv)
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(tok, other_pub)


def test_malformed_rejected():
    _, pub = _keys()
    with pytest.raises(tokens.TokenError):
        tokens.verify_token("garbage-no-dot", pub)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tokens'`

- [ ] **Step 3: Implement**

`license-server/app/tokens.py`:
```python
import base64
import json
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key


class TokenError(Exception):
    pass


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_token(payload: dict, private_key) -> str:
    body = _canonical(payload)
    sig = private_key.sign(body)
    return _b64u_encode(body) + "." + _b64u_encode(sig)


def verify_token(token: str, public_key) -> dict:
    try:
        body_b64, sig_b64 = token.split(".")
        body = _b64u_decode(body_b64)
        sig = _b64u_decode(sig_b64)
    except (ValueError, Exception) as exc:  # noqa: BLE001 - malformed in any way
        raise TokenError("malformed token") from exc
    try:
        public_key.verify(sig, body)
    except InvalidSignature as exc:
        raise TokenError("bad signature") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TokenError("bad payload") from exc


def load_private_key(path: str):
    with open(path, "rb") as f:
        return load_pem_private_key(f.read(), password=None)


def load_public_key(path: str):
    with open(path, "rb") as f:
        return load_pem_public_key(f.read())
```

- [ ] **Step 4: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_tokens.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add license-server/app/tokens.py license-server/tests/test_tokens.py
git commit -m "feat(license-server): Ed25519 token codec (sign/verify contract)"
```

---

### Task 4: Database models + engine

**Files:**
- Create: `license-server/app/db.py`
- Create: `license-server/app/models.py`
- Test: `license-server/tests/test_models.py`

**Interfaces:**
- Produces:
  - `app.db.Base` (DeclarativeBase), `app.db.make_engine(url: str)`, `app.db.make_session_factory(engine)`, `app.db.init_models(engine)` (async, creates tables).
  - `app.models.Account(account_id: str pk, email: str unique, created_at: int)`
  - `app.models.Subscription(account_id: str pk fk, ls_subscription_id: str, plan: str, status: str, current_period_end: int, updated_at: int)`
  - `app.models.AuthCode(email: str pk, code_hash: str, expires_at: int)`
  - `app.models.Activation(account_id: str, device_id: str, first_seen: int, last_seen: int)` composite pk (account_id, device_id)

- [ ] **Step 1: Write failing test**

`license-server/tests/test_models.py`:
```python
import pytest
from sqlalchemy import select
from app import db, models


@pytest.mark.asyncio
async def test_create_and_read_account():
    engine = db.make_engine("sqlite+aiosqlite:///:memory:")
    await db.init_models(engine)
    Session = db.make_session_factory(engine)
    async with Session() as s:
        s.add(models.Account(account_id="acct_1", email="a@b.com", created_at=1))
        await s.commit()
    async with Session() as s:
        row = (await s.execute(select(models.Account))).scalar_one()
        assert row.email == "a@b.com"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement `db.py`**

`license-server/app/db.py`:
```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    return create_async_engine(url, future=True)


def make_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_models(engine) -> None:
    import app.models  # noqa: F401 - ensure models register on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 4: Implement `models.py`**

`license-server/app/models.py`:
```python
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[int] = mapped_column(Integer)


class Subscription(Base):
    __tablename__ = "subscriptions"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), primary_key=True)
    ls_subscription_id: Mapped[str] = mapped_column(String, index=True)
    plan: Mapped[str] = mapped_column(String, default="family")
    status: Mapped[str] = mapped_column(String)
    current_period_end: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer)


class AuthCode(Base):
    __tablename__ = "auth_codes"
    email: Mapped[str] = mapped_column(String, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String)
    expires_at: Mapped[int] = mapped_column(Integer)


class Activation(Base):
    __tablename__ = "activations"
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen: Mapped[int] = mapped_column(Integer)
    last_seen: Mapped[int] = mapped_column(Integer)
```

- [ ] **Step 5: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add license-server/app/db.py license-server/app/models.py license-server/tests/test_models.py
git commit -m "feat(license-server): SQLAlchemy models (accounts/subscriptions/auth_codes/activations)"
```

---

### Task 5: Store layer — subscription upsert + account/code helpers

**Files:**
- Create: `license-server/app/store.py`
- Test: `license-server/tests/test_store.py`

**Interfaces:**
- Produces (all `async`, take an `AsyncSession`):
  - `upsert_subscription(s, *, email, ls_subscription_id, plan, status, current_period_end, now) -> Account` — creates Account if absent (account_id = `"acct_" + sha256(email)[:24]`), upserts Subscription.
  - `get_account_by_email(s, email) -> Account | None`
  - `get_subscription(s, account_id) -> Subscription | None`
  - `set_auth_code(s, email, code_hash, expires_at) -> None`
  - `pop_auth_code(s, email) -> AuthCode | None` (reads + deletes; one-time use)
  - `account_id_for_email(email: str) -> str` (pure helper)

- [ ] **Step 1: Write failing tests**

`license-server/tests/test_store.py`:
```python
import pytest
from app import db, store, models
from sqlalchemy import select


@pytest.fixture
async def session():
    engine = db.make_engine("sqlite+aiosqlite:///:memory:")
    await db.init_models(engine)
    Session = db.make_session_factory(engine)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(session):
    acct = await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="sub_1", plan="family",
        status="active", current_period_end=100, now=1)
    await session.commit()
    assert acct.email == "p@x.com"
    # second upsert same email updates status
    await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="sub_1", plan="family",
        status="canceled", current_period_end=200, now=2)
    await session.commit()
    sub = await store.get_subscription(session, acct.account_id)
    assert sub.status == "canceled"
    assert sub.current_period_end == 200
    # only one account row
    rows = (await session.execute(select(models.Account))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_auth_code_is_one_time(session):
    await store.set_auth_code(session, "p@x.com", "hash123", 999)
    await session.commit()
    got = await store.pop_auth_code(session, "p@x.com")
    await session.commit()
    assert got.code_hash == "hash123"
    assert await store.pop_auth_code(session, "p@x.com") is None
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Implement**

`license-server/app/store.py`:
```python
import hashlib
from sqlalchemy import select, delete
from app import models


def account_id_for_email(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return "acct_" + digest[:24]


async def get_account_by_email(s, email: str):
    return (await s.execute(
        select(models.Account).where(models.Account.email == email.strip().lower())
    )).scalar_one_or_none()


async def get_subscription(s, account_id: str):
    return (await s.execute(
        select(models.Subscription).where(models.Subscription.account_id == account_id)
    )).scalar_one_or_none()


async def upsert_subscription(s, *, email, ls_subscription_id, plan, status,
                              current_period_end, now):
    email = email.strip().lower()
    account = await get_account_by_email(s, email)
    if account is None:
        account = models.Account(
            account_id=account_id_for_email(email), email=email, created_at=now)
        s.add(account)
    sub = await get_subscription(s, account.account_id)
    if sub is None:
        sub = models.Subscription(account_id=account.account_id)
        s.add(sub)
    sub.ls_subscription_id = ls_subscription_id
    sub.plan = plan
    sub.status = status
    sub.current_period_end = current_period_end
    sub.updated_at = now
    return account


async def set_auth_code(s, email: str, code_hash: str, expires_at: int) -> None:
    email = email.strip().lower()
    await s.execute(delete(models.AuthCode).where(models.AuthCode.email == email))
    s.add(models.AuthCode(email=email, code_hash=code_hash, expires_at=expires_at))


async def pop_auth_code(s, email: str):
    email = email.strip().lower()
    row = (await s.execute(
        select(models.AuthCode).where(models.AuthCode.email == email)
    )).scalar_one_or_none()
    if row is not None:
        await s.execute(delete(models.AuthCode).where(models.AuthCode.email == email))
    return row
```

- [ ] **Step 4: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add license-server/app/store.py license-server/tests/test_store.py
git commit -m "feat(license-server): store layer (subscription upsert, one-time codes)"
```

---

### Task 6: Lemon Squeezy webhook receiver

**Files:**
- Create: `license-server/app/webhooks.py`
- Test: `license-server/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `store.upsert_subscription`, `db` session dependency.
- Produces:
  - `app.webhooks.verify_signature(raw_body: bytes, signature_hex: str, secret: str) -> bool` (HMAC-SHA256, constant-time compare).
  - `app.webhooks.map_event(event_name: str, attributes: dict) -> dict` → `{"status": ..., "plan": "family", "current_period_end": int}`. Mapping: `subscription_created`/`subscription_updated`/`subscription_resumed` with attr `status` in (`active`,`on_trial`,`past_due`,`cancelled`,`expired`,`unpaid`) → our status (`on_trial`→`trialing`, `cancelled`→`canceled`, `expired`/`unpaid`→`revoked`, else passthrough); `subscription_payment_refunded` → `revoked`.
  - `app.webhooks.router` (APIRouter) exposing `POST /webhooks/mor`.

- [ ] **Step 1: Write failing tests**

`license-server/tests/test_webhooks.py`:
```python
import hashlib
import hmac
import json
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_webhooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.webhooks'`

- [ ] **Step 3: Implement**

`license-server/app/webhooks.py`:
```python
import hashlib
import hmac
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
    import json
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
```

> Note: `datetime.now(timezone.utc)` is allowed in service runtime code (this is not a workflow script). Tests inject fixed times via the store, not via the router.

- [ ] **Step 4: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_webhooks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add license-server/app/webhooks.py license-server/tests/test_webhooks.py
git commit -m "feat(license-server): Lemon Squeezy webhook receiver + status mapping"
```

---

### Task 7: Email one-time-code auth (`/auth/start`, `/auth/verify`)

**Files:**
- Create: `license-server/app/email.py`
- Create: `license-server/app/auth.py`
- Test: `license-server/tests/test_auth.py`

**Interfaces:**
- Consumes: `store.set_auth_code`, `store.pop_auth_code`, `store.get_account_by_email`.
- Produces:
  - `app.email.send_code(to_email: str, code: str) -> None` (SMTP; no-op + log if `LS_SMTP_HOST` unset).
  - `app.auth.hash_code(code: str) -> str` (sha256 hex).
  - `app.auth.new_session_token(account_id: str) -> str` and `app.auth.read_session_token(token: str) -> str | None` — signed session using the **same Ed25519 token codec** (payload `{"acct": id, "exp": int}`), so no extra secret.
  - `app.auth.router` exposing `POST /auth/start` `{email}` → `{ok:true}`; `POST /auth/verify` `{email, code}` → `{session: <token>}` or 401.
  - `app.auth.get_session_account(authorization: str) -> str` FastAPI dependency raising 401 — reused by Task 8.

- [ ] **Step 1: Write failing tests** (inject code generation + session signing via monkeypatch of `app.auth._gen_code`)

`license-server/tests/test_auth.py`:
```python
import pytest
from fastapi.testclient import TestClient
from app import auth, store, db
import app.webhooks as webhooks  # ensures router engine importable


@pytest.fixture
def client(monkeypatch, tmp_path):
    # in-memory db shared via a single engine
    monkeypatch.setattr(auth, "_gen_code", lambda: "123456")
    sent = {}
    monkeypatch.setattr(auth.email, "send_code", lambda to, code: sent.update({to: code}))
    from app.main import create_app
    app = create_app()
    return TestClient(app), sent


def test_start_then_verify_returns_session(client, monkeypatch):
    c, sent = client
    r = c.post("/auth/start", json={"email": "p@x.com"})
    assert r.status_code == 200
    assert sent["p@x.com"] == "123456"
    r2 = c.post("/auth/verify", json={"email": "p@x.com", "code": "123456"})
    assert r2.status_code == 200
    token = r2.json()["session"]
    assert auth.read_session_token(token) == store.account_id_for_email("p@x.com")


def test_verify_wrong_code_401(client):
    c, _ = client
    c.post("/auth/start", json={"email": "p@x.com"})
    r = c.post("/auth/verify", json={"email": "p@x.com", "code": "000000"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Implement `email.py`**

`license-server/app/email.py`:
```python
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_code(to_email: str, code: str) -> None:
    host = os.getenv("LS_SMTP_HOST", "")
    if not host:
        logger.warning("LS_SMTP_HOST unset — not sending code to %s (dev mode)", to_email)
        return
    msg = EmailMessage()
    msg["Subject"] = "Your snflwr.ai sign-in code"
    msg["From"] = os.getenv("LS_SMTP_FROM", "noreply@snflwr.ai")
    msg["To"] = to_email
    msg.set_content(f"Your sign-in code is {code}. It expires in 10 minutes.")
    port = int(os.getenv("LS_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as srv:
        srv.starttls()
        user = os.getenv("LS_SMTP_USER", "")
        if user:
            srv.login(user, os.getenv("LS_SMTP_PASSWORD", ""))
        srv.send_message(msg)
```

- [ ] **Step 4: Implement `auth.py`**

`license-server/app/auth.py`:
```python
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
```

- [ ] **Step 5: Wire routers into `main.py`** (add to `create_app`)

In `license-server/app/main.py`, inside `create_app()` before `return app`:
```python
    from app import webhooks, auth, license_api
    app.include_router(webhooks.router)
    app.include_router(auth.router)
    app.include_router(license_api.router)
```
> `license_api` is created in Task 8 — to keep this task green, add only `webhooks` and `auth` now; add `license_api` in Task 8's wiring step. (If executing strictly task-by-task, include `webhooks` and `auth` here.)

Also ensure a signing key exists for tests: add to `tests/conftest.py`:
```python
import os
from app.keygen import main as keygen_main
from app.config import settings as _settings

_key_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.exists(_settings.SIGNING_KEY_PATH):
    keygen_main(_key_dir)
    _settings.SIGNING_KEY_PATH = os.path.join(_key_dir, "signing_key.pem")
```
Add `signing_key.pem`, `license_public_key.pem`, `*.db` to `license-server/.gitignore`.

- [ ] **Step 6: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add license-server/app/email.py license-server/app/auth.py license-server/app/main.py license-server/tests/test_auth.py license-server/tests/conftest.py license-server/.gitignore
git commit -m "feat(license-server): email one-time-code auth + signed sessions"
```

---

### Task 8: License issuance (`/license/refresh`, `/license/status`) + trial

**Files:**
- Create: `license-server/app/license_api.py`
- Modify: `license-server/app/main.py` (include `license_api.router`)
- Test: `license-server/tests/test_license_api.py`

**Interfaces:**
- Consumes: `auth.get_session_account`, `store.get_subscription`, `tokens.encode_token`, signing key.
- Produces:
  - `app.license_api.issue_license_token(account_id, plan, status, now, *, trial: bool) -> str` — paid `exp=now+30d`, trial `exp=now+10d`, `grace_days=14`.
  - `POST /license/refresh` (Bearer session) → `{token}` if subscription status in (`active`,`trialing`,`past_due`) and `current_period_end` in future (or status active/trialing); `402` otherwise.
  - `GET /license/status` (Bearer session) → `{status, current_period_end, plan}`.

- [ ] **Step 1: Write failing tests**

`license-server/tests/test_license_api.py`:
```python
import time
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app import auth, store, license_api, tokens
from app.config import settings


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


@pytest.mark.asyncio
async def test_refresh_402_without_subscription():
    c = TestClient(create_app())
    tok = _session_for("nobody@x.com")
    r = c.post("/license/refresh", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 402


@pytest.mark.asyncio
async def test_refresh_issues_token_for_active(monkeypatch):
    # seed an active subscription
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd license-server && python -m pytest tests/test_license_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.license_api'`

- [ ] **Step 3: Implement**

`license-server/app/license_api.py`:
```python
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
```

- [ ] **Step 4: Wire router** — in `main.py create_app()` add `app.include_router(license_api.router)` (import alongside webhooks/auth).

- [ ] **Step 5: Run, verify pass**

Run: `cd license-server && python -m pytest tests/test_license_api.py -v`
Expected: PASS

- [ ] **Step 6: Run the whole server suite + commit**

Run: `cd license-server && python -m pytest -v`
Expected: all PASS
```bash
git add license-server/app/license_api.py license-server/app/main.py license-server/tests/test_license_api.py
git commit -m "feat(license-server): license token issuance (refresh/status, trial)"
```

---

# Subsystem B — Self-hosted app: client, gate, onboarding

### Task 9: `core/licensing.py` — offline verify + state evaluation

**Files:**
- Create: `core/licensing.py`
- Create: `config/license_public_key.pem` (placeholder; replaced by Phase-0 keygen output)
- Test: `tests/test_licensing.py`

**Interfaces:**
- Produces:
  - `core.licensing.LicenseState` — frozen dataclass: `state: str` (one of `active`,`trialing`,`grace`,`expired`,`unlicensed`), `allowed: bool`, `plan: str | None`, `exp: int | None`, `reason: str`.
  - `core.licensing.verify_token(token: str, public_key) -> dict` — **byte-identical** to license-server `app.tokens.verify_token`; raises `LicenseError`.
  - `core.licensing.evaluate(token: str | None, public_key, now: int) -> LicenseState` — pure; never raises.
  - `core.licensing.LicenseError(Exception)`
- Consumes nothing from Subsystem A at runtime (only the token format contract).

- [ ] **Step 1: Write failing tests** (generate a keypair in-test to sign sample tokens)

`tests/test_licensing.py`:
```python
import base64
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core import licensing


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_token(priv, payload):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64u(body) + "." + _b64u(priv.sign(body))


def _setup():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def test_active_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=500)
    assert st.allowed and st.state == "active"


def test_trialing_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "trialing",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=500)
    assert st.allowed and st.state == "trialing"


def test_in_grace_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=1000 + 5 * 86400)  # past exp, within 14d grace
    assert st.allowed and st.state == "grace"


def test_grace_exhausted_gated():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=1000 + 20 * 86400)
    assert not st.allowed and st.state == "expired"


def test_missing_token_unlicensed():
    _, pub = _setup()
    st = licensing.evaluate(None, pub, now=0)
    assert not st.allowed and st.state == "unlicensed"


def test_corrupt_token_unlicensed():
    _, pub = _setup()
    st = licensing.evaluate("not.a.valid.token", pub, now=0)
    assert not st.allowed and st.state == "unlicensed"


def test_bad_signature_unlicensed():
    priv, _ = _setup()
    _, other_pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, other_pub, now=500)
    assert not st.allowed and st.state == "unlicensed"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.licensing'`

- [ ] **Step 3: Implement**

`core/licensing.py`:
```python
"""Offline license verification + state evaluation for the self-hosted tutor.

Mirrors the license-server token codec (see docs/superpowers/specs/2026-06-19-billing-design.md).
NEVER raises out of evaluate(): any problem -> unlicensed (fail-safe gate, never a crash).
"""
import base64
import json
import logging

logger = logging.getLogger(__name__)


class LicenseError(Exception):
    pass


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_token(token: str, public_key) -> dict:
    from cryptography.exceptions import InvalidSignature
    try:
        body_b64, sig_b64 = token.split(".")
        body = _b64u_decode(body_b64)
        sig = _b64u_decode(sig_b64)
    except Exception as exc:
        raise LicenseError("malformed token") from exc
    try:
        public_key.verify(sig, body)
    except InvalidSignature as exc:
        raise LicenseError("bad signature") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LicenseError("bad payload") from exc


from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseState:
    state: str          # active | trialing | grace | expired | unlicensed
    allowed: bool
    plan: "str | None"
    exp: "int | None"
    reason: str


def _unlicensed(reason: str) -> LicenseState:
    return LicenseState(state="unlicensed", allowed=False, plan=None, exp=None, reason=reason)


def evaluate(token, public_key, now: int) -> LicenseState:
    if not token:
        return _unlicensed("no token")
    try:
        payload = verify_token(token, public_key)
    except LicenseError as exc:
        logger.info("License token rejected: %s", exc)
        return _unlicensed(str(exc))
    exp = int(payload.get("exp", 0))
    grace_secs = int(payload.get("grace_days", 0)) * 86400
    plan = payload.get("plan")
    status = payload.get("status", "")
    if now <= exp:
        state = "trialing" if status == "trialing" else "active"
        return LicenseState(state=state, allowed=True, plan=plan, exp=exp, reason="valid")
    if now <= exp + grace_secs:
        return LicenseState(state="grace", allowed=True, plan=plan, exp=exp, reason="in grace")
    return LicenseState(state="expired", allowed=False, plan=plan, exp=exp, reason="grace exhausted")
```

- [ ] **Step 4: Create placeholder public key**

Generate a throwaway key now so the file exists; Phase 0 replaces it with the real one.
Run:
```bash
cd ~/Repos/snflwr.ai && python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
pub = Ed25519PrivateKey.generate().public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo)
open('config/license_public_key.pem','wb').write(pub)
print('placeholder public key written')
"
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add core/licensing.py config/license_public_key.pem tests/test_licensing.py
git commit -m "feat(licensing): offline token verify + state evaluation (fail-safe)"
```

---

### Task 10: Token storage + config fields

**Files:**
- Modify: `config.py` (`_SystemConfig`)
- Modify: `core/licensing.py` (add store/load + public-key loader + singleton helper)
- Test: `tests/test_licensing.py` (append)

**Interfaces:**
- Consumes: `config.system_config` (the existing `_SystemConfig` singleton — confirm the export name in `config.py`).
- Produces:
  - `core.licensing.store_token(token: str) -> None` / `load_token() -> str | None` — writes to `<APP_DATA_DIR>/license.token` (0600).
  - `core.licensing.load_public_key() -> object` — loads bundled PEM from `config.system_config.LICENSE_PUBLIC_KEY_PATH`.
  - `core.licensing.current_state(now: int) -> LicenseState` — loads token + key + evaluates (the one call the gate uses).
  - Config fields: `LICENSE_SERVER_URL: str`, `LICENSE_PUBLIC_KEY_PATH: str`, `LICENSE_REFRESH_INTERVAL_SECONDS: int`, `LICENSE_ENFORCED: bool`.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_licensing.py`:
```python
def test_store_and_load_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    licensing.store_token("abc.def")
    assert licensing.load_token() == "abc.def"


def test_load_token_missing_returns_none(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    assert licensing.load_token() is None
```

- [ ] **Step 2: Run, verify fail**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py::test_store_and_load_token -v`
Expected: FAIL — `AttributeError: module 'core.licensing' has no attribute 'store_token'`

- [ ] **Step 3: Add config fields**

In `config.py`, inside `_SystemConfig` (near the other URL/path fields), add:
```python
    # --- Licensing / billing ---
    LICENSE_SERVER_URL: str = os.getenv("LICENSE_SERVER_URL", "")
    LICENSE_PUBLIC_KEY_PATH: str = os.getenv(
        "LICENSE_PUBLIC_KEY_PATH", "./config/license_public_key.pem")
    LICENSE_REFRESH_INTERVAL_SECONDS: int = int(
        os.getenv("LICENSE_REFRESH_INTERVAL_SECONDS", str(14 * 86400)))
    LICENSE_ENFORCED: bool = os.getenv("LICENSE_ENFORCED", "true").lower() == "true"
```
> First confirm the singleton's exported name. Run `grep -n "system_config\|_SystemConfig()" config.py`. Use that name in `core/licensing.py` imports below.

- [ ] **Step 4: Add storage + key loader + current_state to `core/licensing.py`**

Append to `core/licensing.py`:
```python
import os


def _token_path():
    from config import system_config
    return os.path.join(str(system_config.APP_DATA_DIR), "license.token")


def store_token(token: str) -> None:
    path = _token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_token():
    path = _token_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


_public_key_cache = None


def load_public_key():
    global _public_key_cache
    if _public_key_cache is None:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from config import system_config
        with open(system_config.LICENSE_PUBLIC_KEY_PATH, "rb") as f:
            _public_key_cache = load_pem_public_key(f.read())
    return _public_key_cache


def current_state(now: int) -> LicenseState:
    try:
        pub = load_public_key()
    except Exception as exc:  # missing/corrupt bundled key -> fail safe
        logger.error("Could not load license public key: %s", exc)
        return _unlicensed("public key unavailable")
    return evaluate(load_token(), pub, now)
```

- [ ] **Step 5: Run, verify pass**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config.py core/licensing.py tests/test_licensing.py
git commit -m "feat(licensing): token storage, key loader, config fields, current_state()"
```

---

### Task 11: License gate in `proxy_chat`

**Files:**
- Modify: `api/routes/ollama_proxy.py` (`proxy_chat`, after admin bypass, before student safety path)
- Test: `tests/test_license_gate.py`

**Interfaces:**
- Consumes: `core.licensing.current_state`, existing `_ollama_block_response`, `_get_user_from_headers`.
- Produces: gate behavior — admins never gated; students with `allowed=False` get a non-model "subscription needed" block response; `allowed=True` falls through to the existing safety pipeline. Disabled entirely when `config.system_config.LICENSE_ENFORCED is False`.

- [ ] **Step 1: Read the exact admin-bypass region**

Run: `grep -n "role == \"admin\"\|Student path\|_get_user_from_headers\|_ollama_block_response" api/routes/ollama_proxy.py`
Confirm the line right after the admin `if role == "admin":` block ends and the `# Student path` comment begins (~line 357).

- [ ] **Step 2: Write failing test**

`tests/test_license_gate.py`:
```python
import time
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    # Force enforcement on and a deterministic unlicensed state.
    from config import system_config
    monkeypatch.setattr(system_config, "LICENSE_ENFORCED", True)
    from core import licensing
    monkeypatch.setattr(
        licensing, "current_state",
        lambda now: licensing.LicenseState("unlicensed", False, None, None, "no token"))
    from api.server import app
    return TestClient(app)


def _student_headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


def test_unlicensed_student_blocked_no_model_call(app_client, monkeypatch):
    # If the gate works, the safety pipeline / ollama is never reached.
    called = {"safety": False}
    import safety.pipeline as sp
    monkeypatch.setattr(sp.safety_pipeline, "check_input",
                        lambda **k: called.__setitem__("safety", True))
    resp = app_client.post("/api/chat", json={"model": "m", "messages": [
        {"role": "user", "content": "hi"}], "stream": False},
        headers=_student_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "subscription" in json.dumps(body).lower()
    assert called["safety"] is False  # never reached the model path


def test_licensed_student_passes_gate(app_client, monkeypatch):
    from core import licensing
    monkeypatch.setattr(
        licensing, "current_state",
        lambda now: licensing.LicenseState("active", True, "family", 9999999999, "valid"))
    # safety pipeline allows, ollama mocked to a canned response
    import safety.pipeline as sp

    class _OK:
        is_safe = True
        modified_content = None
        category = None
    monkeypatch.setattr(sp.safety_pipeline, "check_input", lambda **k: _OK())
    # ... existing tests already mock ollama elsewhere; assert we get past the gate
    resp = app_client.post("/api/chat", json={"model": "m", "messages": [
        {"role": "user", "content": "hi"}], "stream": False},
        headers=_student_headers())
    # Past the gate => not the subscription block message
    assert "subscription needed" not in resp.text.lower()
```
> Note: align header names + role string with what `_get_user_from_headers` actually reads (Step 1 grep). Adjust `_student_headers()` to match. The licensed-path test only needs to prove the gate is passed, not full ollama behavior.

- [ ] **Step 3: Run, verify fail**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_license_gate.py::test_unlicensed_student_blocked_no_model_call -v`
Expected: FAIL — block message absent (gate not yet inserted) / safety reached.

- [ ] **Step 4: Insert the gate**

In `api/routes/ollama_proxy.py`, immediately after the admin-bypass block returns and before `# Student path — run safety pipeline` (~line 357), add:
```python
    # License gate — students must hold a valid subscription/trial token.
    # Fail-safe: any licensing problem => gated, never a crash. Admins already returned above.
    from config import system_config
    if system_config.LICENSE_ENFORCED:
        import time as _time
        from core import licensing
        lic = licensing.current_state(int(_time.time()))
        if not lic.allowed:
            logger.info("License gate blocked student %s (state=%s)", user_id, lic.state)
            msg = ("A snflwr.ai subscription is needed to use the tutor. "
                   "Open Settings → Billing to subscribe or sign in.")
            return JSONResponse(content=_ollama_block_response(model, msg))
```

- [ ] **Step 5: Run, verify pass**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_license_gate.py -v`
Expected: PASS

- [ ] **Step 6: Run the proxy's existing tests to confirm no regression**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/ -k "ollama or proxy or chat or safety_gate" -v`
Expected: PASS (existing admin/safety tests still green — gate is gated behind `LICENSE_ENFORCED` and admins return earlier).

- [ ] **Step 7: Commit**

```bash
git add api/routes/ollama_proxy.py tests/test_license_gate.py
git commit -m "feat(licensing): gate /api/chat behind active subscription (fail-safe, admin bypass)"
```

---

### Task 12: Refresh client + background task

**Files:**
- Modify: `core/licensing.py` (add `refresh_once`)
- Create: `tasks/license_refresh.py`
- Modify: `api/server.py` (start task in `lifespan`)
- Test: `tests/test_licensing.py` (append refresh tests)

**Interfaces:**
- Consumes: `httpx`, `config.system_config.LICENSE_SERVER_URL`, a stored session token (written by Task 13's sign-in), `store_token`.
- Produces:
  - `core.licensing.load_session() -> str | None` / `store_session(token: str)` — `<APP_DATA_DIR>/license.session` (0600).
  - `core.licensing.refresh_once(client=None, now=None) -> bool` — POST `LICENSE_SERVER_URL/license/refresh` with `Authorization: Bearer <session>`; on 200 store new token + return True; on 402/network error return False (keep existing token). Never raises.
  - `tasks.license_refresh.run_refresh_loop(stop_event)` — async loop calling `refresh_once` every `LICENSE_REFRESH_INTERVAL_SECONDS`.

- [ ] **Step 1: Append failing test** (mock httpx)

Append to `tests/test_licensing.py`:
```python
def test_refresh_once_stores_new_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(system_config, "LICENSE_SERVER_URL", "https://ls.test")
    licensing.store_session("sess-token")

    class _Resp:
        status_code = 200
        def json(self):
            return {"token": "new.token"}

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, timeout=None):
            assert headers["Authorization"] == "Bearer sess-token"
            return _Resp()

    monkeypatch.setattr(licensing.httpx, "Client", _Client)
    assert licensing.refresh_once() is True
    assert licensing.load_token() == "new.token"


def test_refresh_once_offline_keeps_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(system_config, "LICENSE_SERVER_URL", "https://ls.test")
    licensing.store_session("sess-token")
    licensing.store_token("old.token")

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            raise licensing.httpx.ConnectError("offline")

    monkeypatch.setattr(licensing.httpx, "Client", _Client)
    assert licensing.refresh_once() is False
    assert licensing.load_token() == "old.token"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py::test_refresh_once_stores_new_token -v`
Expected: FAIL — `AttributeError: ... has no attribute 'store_session'` / `httpx`.

- [ ] **Step 3: Implement refresh in `core/licensing.py`**

Append:
```python
import httpx


def _session_path():
    from config import system_config
    return os.path.join(str(system_config.APP_DATA_DIR), "license.session")


def store_session(token: str) -> None:
    path = _session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_session():
    path = _session_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def refresh_once(client=None, now=None) -> bool:
    from config import system_config
    base = system_config.LICENSE_SERVER_URL
    session = load_session()
    if not base or not session:
        return False
    try:
        owns = client is None
        client = client or httpx.Client(timeout=10.0)
        try:
            resp = client.post(
                base.rstrip("/") + "/license/refresh",
                headers={"Authorization": f"Bearer {session}"}, timeout=10.0)
        finally:
            if owns:
                client.close()
        if resp.status_code == 200:
            store_token(resp.json()["token"])
            return True
        logger.info("License refresh returned %s", resp.status_code)
        return False
    except Exception as exc:  # network / parse — keep existing token
        logger.info("License refresh failed (offline?): %s", exc)
        return False
```
> The test patches `licensing.httpx.Client`, so leave the `client=None` branch constructing `httpx.Client`. (The `with`-based test stub also satisfies `.close()` via `__exit__`; if using the context form, adjust to `with httpx.Client(...) as client:` — keep one style consistent with the test.)

- [ ] **Step 4: Implement the loop task**

`tasks/license_refresh.py`:
```python
import asyncio
import logging
from config import system_config
from core import licensing

logger = logging.getLogger(__name__)


async def run_refresh_loop(stop_event: asyncio.Event) -> None:
    interval = max(3600, system_config.LICENSE_REFRESH_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(licensing.refresh_once)
        except Exception as exc:  # never let the loop die
            logger.warning("license refresh loop iteration failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 5: Wire into `lifespan` in `api/server.py`**

In the `lifespan` startup section (after other `asyncio.create_task(...)` calls, ~line 337), add:
```python
        # License refresh background task (only when a license server is configured)
        if system_config.LICENSE_SERVER_URL and system_config.LICENSE_ENFORCED:
            import asyncio as _asyncio
            from tasks.license_refresh import run_refresh_loop
            app.state.license_stop = _asyncio.Event()
            app.state.license_task = _asyncio.create_task(
                run_refresh_loop(app.state.license_stop))
```
And in the shutdown half of `lifespan` (after `yield`), add:
```python
        _lic_stop = getattr(app.state, "license_stop", None)
        if _lic_stop is not None:
            _lic_stop.set()
```
> Confirm `system_config` is imported in `server.py` (it is, per startup validation). Match the existing variable name from `grep -n "system_config\|import config" api/server.py`.

- [ ] **Step 6: Run, verify pass**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/licensing.py tasks/license_refresh.py api/server.py tests/test_licensing.py
git commit -m "feat(licensing): online refresh client + background refresh task"
```

---

### Task 13: App-side billing routes (Subscribe / Sign-in proxy)

**Files:**
- Create: `api/routes/billing.py`
- Modify: `api/server.py` (`include_router`)
- Test: `tests/test_billing_routes.py`

**Interfaces:**
- Consumes: `httpx`, `config.system_config.LICENSE_SERVER_URL`, `core.licensing.store_session`, `core.licensing.store_token`, `core.licensing.current_state`.
- Produces (router prefix `/api/billing`, admin-only via existing auth dependency pattern — confirm how other admin routes guard):
  - `POST /api/billing/signin/start` `{email}` → proxies to `LICENSE_SERVER_URL/auth/start`.
  - `POST /api/billing/signin/verify` `{email, code}` → proxies `/auth/verify`; on success **stores the session locally** + immediately calls `licensing.refresh_once()`; returns `{ok, licensed: bool}`.
  - `GET /api/billing/status` → `{state, plan, exp}` from `licensing.current_state(now)`.
  - `GET /api/billing/checkout-url` → returns the configured Lemon Squeezy hosted checkout URL (`LS_CHECKOUT_URL` config) for the onboarding "Subscribe" button.

- [ ] **Step 1: Add `LS_CHECKOUT_URL` config**

In `config.py` `_SystemConfig` licensing block:
```python
    LS_CHECKOUT_URL: str = os.getenv("LS_CHECKOUT_URL", "")
```

- [ ] **Step 2: Write failing tests** (mock httpx + licensing)

`tests/test_billing_routes.py`:
```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "LICENSE_SERVER_URL", "https://ls.test")
    from api.server import app
    return TestClient(app)


def _admin_headers():
    # Match the project's admin auth. If routes use session cookies/middleware,
    # adapt to the helper other admin-route tests use (grep tests/test_admin_routes*).
    return {"X-OpenWebUI-User-Role": "admin", "X-OpenWebUI-User-Id": "admin_1"}


def test_status_returns_state(client, monkeypatch):
    from core import licensing
    monkeypatch.setattr(licensing, "current_state",
                        lambda now: licensing.LicenseState("active", True, "family", 123, "valid"))
    r = client.get("/api/billing/status", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json()["state"] == "active"


def test_signin_verify_stores_session(client, monkeypatch):
    from core import licensing
    stored = {}
    monkeypatch.setattr(licensing, "store_session", lambda t: stored.update(session=t))
    monkeypatch.setattr(licensing, "refresh_once", lambda: True)

    class _Resp:
        status_code = 200
        def json(self): return {"session": "sess-xyz"}

    import api.routes.billing as billing

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None, timeout=None): return _Resp()

    monkeypatch.setattr(billing.httpx, "Client", _Client)
    r = client.post("/api/billing/signin/verify",
                    json={"email": "p@x.com", "code": "123456"}, headers=_admin_headers())
    assert r.status_code == 200
    assert stored["session"] == "sess-xyz"
    assert r.json()["licensed"] is True
```

- [ ] **Step 3: Run, verify fail**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_billing_routes.py -v`
Expected: FAIL — route 404 / module missing.

- [ ] **Step 4: Implement `billing.py`**

`api/routes/billing.py`:
```python
import time
import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from config import system_config
from core import licensing

logger = logging.getLogger(__name__)
router = APIRouter()


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
```

- [ ] **Step 5: Wire router**

In `api/server.py` near the other `include_router` calls:
```python
from api.routes import billing
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])
```
> **Admin-only:** these routes must be admin-guarded (per spec: only admins manage billing). Confirm how admin routes enforce this (`grep -n "Depends\|admin" api/routes/admin.py | head`) and apply the same dependency to `billing.router` (e.g. `APIRouter(dependencies=[Depends(require_admin)])`). Update `_admin_headers()` in the test to whatever that guard reads.

- [ ] **Step 6: Run, verify pass**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_billing_routes.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add api/routes/billing.py api/server.py config.py tests/test_billing_routes.py
git commit -m "feat(billing): app-side Subscribe/Sign-in proxy routes (admin-only)"
```

---

### Task 14: License-server README + deploy notes

**Files:**
- Create: `license-server/README.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Write README**

`license-server/README.md` covering: purpose (issues offline tokens, holds NO student data), env vars (`LS_DATABASE_URL`, `LS_SIGNING_KEY_PATH`, `LS_WEBHOOK_SECRET`, `LS_SMTP_*`, `LS_SESSION_TTL_SECONDS`, `LS_CODE_TTL_SECONDS`), Phase-0 keygen (`python -m app.keygen /secure/dir` → copy `license_public_key.pem` to app `config/`), Lemon Squeezy **test mode** setup (create store, products $9.99/mo + $89/yr, 10-day trial, webhook → `/webhooks/mor` with `X-Signature` HMAC secret), local run (`uvicorn app.main:app`), test (`pytest`), and hosting options (Fly/Render/Cloud Run + managed Postgres; protect the signing key via the platform secret store / KMS). State explicitly: **never commit `signing_key.pem`**.

- [ ] **Step 2: Commit**

```bash
git add license-server/README.md
git commit -m "docs(license-server): deploy + Lemon Squeezy test-mode setup notes"
```

---

### Task 15: Full-suite verification + branch wrap-up

**Files:** none (verification).

- [ ] **Step 1: Run the license-server suite**

Run: `cd ~/Repos/snflwr.ai/license-server && python -m pytest -v`
Expected: all PASS.

- [ ] **Step 2: Run the new app-side tests**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/test_licensing.py tests/test_license_gate.py tests/test_billing_routes.py -v`
Expected: all PASS.

- [ ] **Step 3: Run the broader app suite for regressions**

Run: `cd ~/Repos/snflwr.ai && python -m pytest tests/ -q`
Expected: no new failures vs. the pre-change baseline (note any pre-existing failures separately).

- [ ] **Step 4: Confirm fail-safe + secret hygiene by inspection**

- `git status` shows no `signing_key.pem`, no `*.session`, no `*.token`, no real keys committed.
- `core/licensing.evaluate` and `current_state` cannot raise (all paths return a `LicenseState`).
- Gate is behind `LICENSE_ENFORCED` and admins return before it.

- [ ] **Step 5: Use the verification-before-completion skill, then finishing-a-development-branch**

Invoke `superpowers:verification-before-completion` to confirm claims against real command output, then `superpowers:finishing-a-development-branch` to decide merge/PR for `feat/billing-design-spec` (or a fresh `feat/billing-implementation` branch).

---

## Self-Review (completed during authoring)

**Spec coverage:**
- §4.2 License Server → Tasks 1–8, 14. ✅
- §4.3 app changes (`core/licensing.py`, gate, onboarding, config) → Tasks 9–13. ✅
- §5 token format → Task 3 (sign) + Task 9 (verify), contract in Global Constraints. ✅
- §6 flows: activation → Task 13; refresh online/offline → Task 12; gate states → Tasks 9+11; lifecycle webhook→status → Task 6. ✅
- §7 security: asymmetric signing (Tasks 2/3/9), fail-safe (Tasks 9/11), signature-verified webhooks (Task 6); clock-tamper + device-binding are spec-noted Phase-4 polish → **out of scope** (documented). ✅
- §8 data model → Task 4. §9 API surface → Tasks 6,7,8. ✅
- §13 testing strategy → every task is TDD; E2E gate-when-expired → Tasks 9/11/15. ✅
- §10 phasing: Phase 0 keygen → Task 2; Phase 1 → Tasks 1–8,14; Phase 2 → Tasks 9–13. Phase 3 (legal/copy) + Phase 4 (polish) intentionally **out of scope**. ✅

**Out-of-scope (tracked, not built here):** Phase 3 legal/copy/subprocessor docs; Phase 4 dunning banners, trial countdown UI, device-binding/max-activations, plan→model-tier mapping; the actual Open WebUI onboarding *UI* (this plan ships the backing `/api/billing` endpoints; the front-end onboarding step that calls them is a follow-up once the OWUI onboarding function location is settled).

**Placeholder scan:** no TBD/TODO/"handle edge cases" — every code step has concrete code. ✅

**Type consistency:** `verify_token` signature identical in `app.tokens` and `core.licensing`; `LicenseState` fields used consistently (`state`/`allowed`/`plan`/`exp`/`reason`); `current_state(now)`, `refresh_once()`, `store_token`/`store_session` names consistent across Tasks 10/12/13. ✅

**Known adapt-points flagged for the implementer** (require a quick grep to match existing conventions, called out inline): exact `system_config` export name; `_get_user_from_headers` header/role names; admin-guard dependency for billing routes; `lifespan` startup/shutdown variable names. These are existing-codebase integration seams, not gaps.
