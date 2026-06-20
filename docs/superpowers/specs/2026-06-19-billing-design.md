---
title: Billing — Self-Hosted Subscription Licensing (Design Spec)
date: 2026-06-19
status: implemented (Phases 1-2) — plan at docs/superpowers/plans/2026-06-19-billing-implementation.md; Phases 3-4 pending
---

# Billing: Self-Hosted Subscription Licensing

## 1. Context & problem

snflwr.ai is a K-12 AI tutor that runs **entirely on the customer's own hardware**
(Family/USB offline, Home Server, School/Enterprise — all self-hosted). There is
**no existing billing code** and the web presence is a marketing-only Next.js site
(no customer accounts/portal). We want to monetize without breaking the core
"runs offline, your data never leaves your network" promise.

This is hard because the software runs on hardware we don't control and sometimes
fully offline, so we can't meter usage server-side or rely on constant
connectivity.

## 2. Decisions (locked)

| Decision | Choice |
|---|---|
| Monetization model | **Self-hosted + subscription license** (stays on customer hardware) |
| Free tier | **None — paid-only** (subscription required to use the tutor; trial included) |
| Enforcement | **Ed25519-signed license token**, verified **offline** each launch; periodic online refresh + offline **grace** window |
| Payments / tax | **Merchant of Record** (Paddle or Lemon Squeezy) handles checkout + subscriptions + global sales-tax/VAT; **our own license server** issues the offline signed tokens |
| Market (v1) | **B2C families first** (B2B/school per-seat licensing is later, out of scope) |

## 3. Goals / non-goals

**Goals**
- Require an active subscription to use the tutor, enforced on customer hardware.
- Preserve privacy: the billing system holds **no student data**.
- Work offline between refreshes (honor the USB/offline deployment mode after one online activation).
- Offload sales-tax/VAT compliance to a Merchant of Record.
- Fail safe: licensing problems gate tutoring but never crash the app; a missing/corrupt token = unlicensed, not an error.

**Non-goals (v1)**
- B2B/school per-seat licensing & purchase orders.
- Multiple paid tiers / metered/usage-based billing (paid-only binary).
- In-app card entry (use MoR hosted checkout → zero PCI scope).
- Proration/upgrade-downgrade UI (MoR customer portal handles it).
- Indefinite airgapped licensing (a Phase-later device-bound mode can extend grace).

## 4. Architecture

Three components; only the License Server is new first-party cloud infra.

```
┌─ Merchant of Record ─┐   webhooks    ┌─ License Server (NEW, cloud) ─┐
│ Paddle / LemonSqueezy │ ───────────► │ • records subscription status  │
│ checkout, subs, TAX   │              │ • account auth (email + code)  │
└───────────▲───────────┘              │ • SIGNS short-lived license    │
            │ pays                     │   tokens (private key here)    │
       (browser)                       │ • holds NO student data        │
            │                          └───────────────▲────────────────┘
   ┌────────┴───────────────────────────────────┐      │ sign-in / refresh (online)
   │  Self-hosted app (customer hardware)        │──────┘
   │  • core/licensing.py — verify token OFFLINE │
   │    (bundled public key)                     │
   │  • refresh task (~14d when online)          │
   │  • GATE in ollama_proxy.py /api/chat        │
   └─────────────────────────────────────────────┘
```

### 4.1 Merchant of Record (third-party, hosted)
Paddle or Lemon Squeezy (chosen in Phase 0). Hosts checkout + subscription
lifecycle and is the legal seller of record → handles global sales-tax/VAT
registration + remittance. Emits webhooks: subscription created / renewed /
canceled / past_due / refunded.

### 4.2 License Server (new — the only first-party cloud component)
Small service (FastAPI or serverless) + a tiny Postgres. Responsibilities:
- Consume MoR webhooks (verify signature) → maintain `subscriptions`.
- Privacy-minimal account auth: email → one-time code → short session.
- Issue **Ed25519-signed license tokens** (JWT-style) from subscription status.
- Issue **trial** tokens (one per email/device).
- Hold secrets: MoR webhook secret, signing **private key** (KMS/secret store).
- Store: billing email + subscription status only. **Never student data.**

### 4.3 Self-hosted app changes (existing FastAPI / Open WebUI stack)
- `core/licensing.py`: store token; **verify signature offline** with the
  bundled public key on each launch/session; evaluate state; run the refresh task.
- **Gate** in `api/routes/ollama_proxy.py` `proxy_chat`, beside the safety gate.
- Onboarding gains a **Subscribe / Sign-in** step.
- Config: license-server URL, bundled public key, grace policy.

## 5. License token

Ed25519-signed JWT, e.g.:
```json
{ "sub": "acct_8f3…", "plan": "family", "status": "active",
  "iat": 1750200000, "exp": 1752792000,      // ~30-day life
  "grace_days": 14, "device_id": "optional-fingerprint" }
```
Signed by the license server's private key; verified offline by the bundled
public key. Short life (30d) + grace (14d) bounds how long a canceled
subscription keeps working.

## 6. Flows

**Activation (happy path)**
1. First run → onboarding shows **Start trial / Subscribe** → opens MoR checkout in browser.
2. User pays → MoR webhook → license server marks subscription `active`.
3. In-app **Sign in**: email → one-time code → license server verifies active → returns signed token.
4. App stores token, verifies offline → unlocked.

**Refresh (silent, online)** — every ~14d a background task calls
`/license/refresh`; on success swaps in a fresh 30-day token. Offline → keep
current token until `exp + grace`.

**Gate** (the enforcement point):
```
on /api/chat (student):
  state = evaluate_license()        # offline, from stored token
  active | trialing | grace  → forward to safety pipeline → model
  expired (grace exhausted)  → return "Subscription needed…" (NO model call)
  never-activated            → onboarding blocks before chat is reachable
admins are never gated; only TUTORING is gated — settings/dashboard/billing stay usable when lapsed.
```

**Lifecycle → gate state**

| Event (MoR webhook) | License server | App behavior |
|---|---|---|
| Subscribe / renew | `active`, new period_end | refresh issues token → unlocked |
| Trial start | `trialing`, 10-day token | unlocked, trial banner |
| Payment failed (dunning) | `past_due` | works during MoR retry window + grace; "update payment" banner |
| Canceled | `canceled`, keep period_end | works until period_end **+ grace**, then gate |
| Refund / chargeback | `revoked` | next refresh issues no token → gate after exp+grace |

**Offline / USB:** one online **activation** required; then runs offline until
`exp+grace`; must reconnect to refresh. Indefinite airgapped use is impossible
with a subscription (inherent) — a Phase-later device-bound mode can lengthen
grace for known-offline schools.

## 7. Security & edge cases
- **Asymmetric signing** (Ed25519): app verifies, never forges; private key only on the server.
- **Clock tamper:** moving the clock back can extend grace — acceptable risk for a family tutor (not hard DRM); optionally stamp `last_server_seen` and refuse large backward jumps.
- **Sharing:** optional `device_id` binding + max-activations cap per subscription.
- **Fail-safe:** missing/corrupt token = unlicensed (gate), never a crash; license-server downtime never breaks an already-licensed user (offline verification).

## 8. Data model

**License server**
- `accounts(account_id, email, created_at)`
- `subscriptions(account_id, mor_subscription_id, plan, status, current_period_end, updated_at)`
- `auth_codes(email, code_hash, expires_at)` (one-time codes)
- `activations(account_id, device_id, first_seen, last_seen)` (for caps/binding)

**Self-hosted app**
- A single stored signed token (file under the encrypted data dir or a config row) + cached `last_server_seen`.

## 9. API surface (license server)
- `POST /auth/start` — `{email}` → emails one-time code.
- `POST /auth/verify` — `{email, code}` → session.
- `POST /license/refresh` — session → fresh signed token (or 402 if no active sub/trial).
- `GET  /license/status` — session → status JSON (for UI).
- `POST /webhooks/mor` — MoR webhook receiver (signature-verified).

## 10. Phasing & effort

**Phase 0 — Decisions & setup** *(days; mostly non-eng)*
Pick MoR (Paddle vs Lemon Squeezy); create products/prices (monthly + annual) +
trial config; generate the Ed25519 keypair; choose license-server hosting.
⚠️ Register business entity + bank (MoR onboarding requires it).

**Phase 1 — License server** *(~1–1.5 wks eng)*
Webhook handler, account auth (email+code), token + trial issuance, secrets,
deploy + basic monitoring. Tests: webhook lifecycle, issuance, auth.

**Phase 2 — Self-hosted client + gate** *(~1–1.5 wks eng)*
`core/licensing.py` (store/verify-offline/evaluate/refresh); gate in
`ollama_proxy.py` (admin bypass, fail-safe); onboarding Subscribe/Sign-in;
config (server URL + bundled public key + grace). Tests: token verify
(valid/expired/grace/bad-sig), gate, refresh online/offline, fail-safe.

**Phase 3 — Copy, legal & positioning** *(~2–3 days eng + legal)*
README/website "free, fully offline" → "subscription, runs offline." ToS/Privacy:
pricing, **auto-renewal terms** (CA auto-renewal / FTC click-to-cancel), refund
policy, MoR + license server as **named subprocessors** with exactly what they
hold (billing email + sub status, no student data) — folds into the existing
lawyer-review subprocessor item.

**Phase 4 — Polish** *(~3–5 days)*
Dunning banners, trial countdown, optional device-binding/max-activations,
optional plan→Budget/Standard/Premium model-tier mapping.

**→ ~3–4 weeks eng for shippable v1 (Phases 1–3).**

## 11. Sequencing & dependencies
- 🔴 **Entity registration gates go-live** (MoR onboarding + bank) — the same
  blocker as the legal launch track. Auto-renewal terms must also be in the
  (currently DRAFT) legal docs.
- ✅ **But Phases 1–2 can be built now against MoR sandbox/test mode** (no entity
  needed) and flipped live when the entity + legal land. Billing engineering runs
  **in parallel** with the legal track.

## 12. Risks
- New cloud service to run/secure — it holds the **signing private key**; protect it (KMS, least privilege, rotation).
- Inherent offline-activation friction (paid + offline).
- ~5% MoR fee + vendor dependency.

## 13. Testing strategy
- **License client (unit):** token verification (valid / expired / grace / bad-signature / wrong-key), state evaluation, gate decision, refresh success/failure, offline grace, fail-safe on missing/corrupt token.
- **License server (unit/integration):** webhook handling (created/renewed/canceled/past_due/refunded), token + trial issuance, auth flow, signature correctness.
- **End-to-end:** activate → gate-when-expired → renew → unlocked; a student `/api/chat` is blocked when unlicensed (mirrors the existing safety-gate tests).

## 14. Pricing (recommended)

> Decided: **10-day free trial** (per product owner). Pricing below is a
> market-research recommendation — see caveat at the end.

**Recommendation: $9.99/mo or $89/yr** (annual ≈ $7.42/mo, ~26% off).

**Market anchors (consumer K-12 AI tutoring / EdTech):**
- **Khanmigo (closest comp):** ~$4/mo or ~$44/yr, up to 10 child accounts, free for teachers. Deliberately near impulse-buy; its earlier ~$10/mo drew "too steep" complaints. (Source: khanmigo.ai/pricing — primary.)
- **MagicSchool Plus:** $12.99/mo or $99.96/yr (~$8.33/mo) — but teacher-leaning.
- **Math apps:** Photomath/Mathway ~$9.99/mo; Wolfram Alpha ~$7.25/mo; Socratic free.
- **Category benchmarks:** Education-app **median ~$9.99–12.99/mo** and **~$38–45/yr** (RevenueCat 2026 / Adapty); Education has the **highest annual median of any app category**, and 59–66% of plans are annual — annual is where the revenue/retention is.

**Why not match Khanmigo's $4:** that is a nonprofit-subsidized floor we can't and shouldn't chase. snflwr.ai's audience **self-selects on privacy, not price** (privacy-first, offline, self-hosted, "your data never leaves your device"), and the self-host setup friction already filters out price-shoppers. Price the **differentiation**, land at the category median, and push annual.

**Trade-offs**
- **Higher ($12.99/mo, $99/yr):** more revenue per user + signals premium/privacy value; risks slowing an already-narrow (technical/privacy) audience. Viable if positioned as a premium privacy product.
- **Lower ($6.99/mo, $59/yr):** wider funnel; but erodes the premium signal and margins (and MoR takes ~5%).

**On the 10-day trial:** lands in the **better-converting 7–14-day band** (industry data: shorter trials convert ~20% better than 30-day trials, which cancel ~51%), while still giving families enough runway to set up the self-hosted app and evaluate. Pair with a **card-on-file (opt-out) trial** (~2.5× higher conversion vs no-card) and a **pre-charge reminder** email a couple of days before trial end. EdTech trial-to-paid runs ~25% (Adapty/First Page Sage) — well above the ~8% cross-SaaS median.

⚠️ **Caveat:** the deep-research run's adversarial verification was rate-limited, so these figures are source-derived but **not harness-verified**. Sanity-check the live competitor prices (Khanmigo, MagicSchool) before locking the number.

## 14b. Open questions (resolve in Phase 0)
- **MoR choice:** Paddle vs Lemon Squeezy (fees, offline-license fit, EU/US coverage, API ergonomics).
- **Final price point** within the recommended range (default **$9.99/mo, $89/yr**).
- **Device/seat policy:** how many activations per family subscription (Khanmigo allows up to 10 child profiles — consider matching generosity).
- **Trial card capture:** card-on-file (higher conversion) vs no-card (lower friction).
- **License-server hosting:** Fly / Render / Cloud Run + managed Postgres.
