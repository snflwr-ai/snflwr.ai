---
title: Current Status (canonical)
last_updated: 2026-06-18
---

# snflwr.ai — Current Status

> This is the single source of truth for launch-readiness. (Older point-in-time
> "production readiness" report docs were removed — they overstated readiness;
> this file and `PRODUCTION_READINESS_LOCAL_DEPLOYMENT.md` supersede them.)

## TL;DR

**Engineering is strong. The product is NOT launch-ready — and the blockers are
legal and business, not code.** It cannot yet be legally sold to schools or
parents.

| Area | State |
|---|---|
| Engineering / codebase | Strong — 3,500+ tests, CI, encrypted at rest (SQLCipher) |
| Safety architecture | Strong — layered pipeline + llama-guard classifier, crisis escalation, fail-closed |
| Deployment / ops | Good — guarded upgrades + auto-rollback; monitoring configs exist for enterprise |
| Legal / compliance | **Blocked** — all 3 legal docs are DRAFT; no business entity registered |
| Business / SaaS | **Blocked** — no billing/payments; no entity; no pricing |
| **Launch-ready?** | **No** — gated by legal + business |

## Compliance reality

The codebase implements **COPPA/FERPA-supporting architecture** (parental
consent flow, data minimization, retention cleanup, encryption at rest, audit
trails). That is **not the same as being legally compliant or certified.** Legal
compliance is blocked because:

- The Terms of Service, Privacy Policy, and Data Processing Agreement in `legal/`
  are all **DRAFT — NOT IN EFFECT**.
- **No business entity is registered**, so COPPA § 312.4(d)(1)'s required
  operator name, mailing address, and phone cannot be published.

See `legal/LAWYER_REVIEW_CHECKLIST.md` for the full blocker list (8 operator
pre-publication blockers + 12 counsel-review items).

## Critical path to launch

1. **Register a business entity** (the keystone — unblocks the COPPA operator
   disclosures, governing law, etc.).
2. **Counsel finalizes** the three legal documents (~2 weeks).
3. **Build billing** (Stripe/payments don't exist).
4. **Surface required disclosures in the UI** (`docs/compliance/REQUIRED_DISCLOSURES.md`)
   and configure `ADMIN_EMAIL` + SMTP so safety/parent alerts actually send.

## What changed recently (2026-06)

Backbone → `gemma4:e4b`; guarded upgrade framework; semantic safety classifier
enabled; crisis escalation wired into the proxy; database actually encrypted at
rest (was silently plaintext). See `CHANGELOG.md` and
`docs/architecture/REQUEST_FLOW_AND_SAFETY.md`.
