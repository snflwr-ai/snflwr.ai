# snflwr.ai License Server

The only first-party cloud component of the snflwr.ai billing system. It:

- consumes **Lemon Squeezy** (Merchant of Record) webhooks and tracks subscription status,
- authenticates account holders by **email one-time code**, and
- **signs** short-lived Ed25519 license tokens that the self-hosted tutor verifies **offline**.

**Privacy:** this service stores **only** a billing email + subscription status. It holds
**no student data, ever**. See `docs/superpowers/specs/2026-06-19-billing-design.md`.

## Components

| File | Responsibility |
|---|---|
| `app/main.py` | FastAPI app factory + `/health` |
| `app/config.py` | env-driven settings |
| `app/keygen.py` | Ed25519 keypair generator (Phase-0 setup) |
| `app/tokens.py` | token codec — **sign** + the shared verify contract |
| `app/db.py`, `app/models.py` | SQLAlchemy engine + tables |
| `app/store.py` | data access (subscription upsert, one-time codes) |
| `app/webhooks.py` | Lemon Squeezy receiver (`POST /webhooks/mor`) |
| `app/auth.py` | `POST /auth/start`, `POST /auth/verify` |
| `app/license_api.py` | `POST /license/refresh`, `GET /license/status` |
| `app/email.py` | minimal SMTP sender for one-time codes |

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `LS_DATABASE_URL` | `sqlite+aiosqlite:///./license.db` | DB URL (use Postgres + `asyncpg` in prod) |
| `LS_SIGNING_KEY_PATH` | `./signing_key.pem` | Ed25519 **private** key (PKCS8 PEM) |
| `LS_WEBHOOK_SECRET` | _(empty)_ | Lemon Squeezy signing secret (HMAC-SHA256) |
| `LS_SESSION_TTL_SECONDS` | `3600` | sign-in session lifetime |
| `LS_CODE_TTL_SECONDS` | `600` | one-time code lifetime (10 min) |
| `LS_SMTP_HOST` / `LS_SMTP_PORT` / `LS_SMTP_USER` / `LS_SMTP_PASSWORD` / `LS_SMTP_FROM` | _(unset → dev no-op)_ | SMTP for code emails |

> **Never commit `signing_key.pem`.** It is the root of trust for every issued license.
> Keep it only in the platform secret store / KMS. `.gitignore` already excludes it.

## Phase-0 setup (one time)

```bash
# 1. Generate the keypair into a secure directory.
python -m app.keygen /secure/dir
#    -> /secure/dir/signing_key.pem  (0600, stays on the server)
#    -> /secure/dir/license_public_key.pem

# 2. Bundle the PUBLIC key into the app:
cp /secure/dir/license_public_key.pem ../config/license_public_key.pem
#    (the self-hosted app verifies tokens with this; it never sees the private key)
```

## Lemon Squeezy (test mode for Phases 1–2 — no business entity needed)

1. Create a store in **test mode**.
2. Create a product with two variants: **$9.99/mo** and **$89/yr**, each with a **10-day free trial**.
3. Set up a webhook → `https://<license-server>/webhooks/mor`.
   - Lemon Squeezy signs each request with HMAC-SHA256 of the raw body in the
     `X-Signature` header (hex). Put that signing secret in `LS_WEBHOOK_SECRET`.
   - Subscribe to: `subscription_created`, `subscription_updated`,
     `subscription_resumed`, `subscription_payment_refunded`.
4. Build the hosted-checkout URL for the app's "Subscribe" button; set it in the
   self-hosted app's `LS_CHECKOUT_URL`.

Flip to **live mode** only once the business entity + bank are registered (the same
blocker as the legal launch track) and the auto-renewal terms are in the legal docs.

## Run locally

```bash
pip install -r requirements.txt
python -m app.keygen .            # dev keypair
LS_SIGNING_KEY_PATH=./signing_key.pem uvicorn app.main:app --reload
```

## Test

```bash
pytest            # uses a throwaway sqlite test DB + an auto-generated dev key
```

## Deploy

Any container host works. Recommended: **Fly.io / Render / Cloud Run** + a managed
Postgres (`LS_DATABASE_URL=postgresql+asyncpg://...`). Requirements:

- Inject `LS_SIGNING_KEY_PATH` contents via the platform secret store / mounted secret;
  never bake the private key into the image.
- Restrict who can read the signing key (least privilege); plan for key rotation
  (publish a new public key in an app update, then start signing with the new key).
- Run behind TLS; the webhook endpoint must be publicly reachable by Lemon Squeezy.
