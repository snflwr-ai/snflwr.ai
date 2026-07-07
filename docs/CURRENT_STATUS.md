---
title: Current Status (canonical)
last_updated: 2026-07-07
---

# snflwr.ai — Current Status

> This is the single source of truth for launch-readiness. (Older point-in-time
> "production readiness" report docs were removed — they overstated readiness;
> this file and `PRODUCTION_READINESS_LOCAL_DEPLOYMENT.md` supersede them.)

## TL;DR

**Engineering is strong — an A-grade build across safety, security, code, and
ops. The product is NOT launch-ready, and the blockers are legal and business,
not code.** It cannot yet be legally sold to schools or parents.

| Area | State |
|---|---|
| Engineering / codebase | Strong — ~3,700 tests, 85% coverage gate, 3.10–3.12 × PG/Redis CI matrix, mypy/pylint/bandit enforcing, versioned migrations |
| Safety architecture | Strong — layered pipeline **verified live in prod** (deterministic stages + `llama-guard3:8b`); crisis escalation; fail-closed; crisis incidents persist even for profile-less sessions |
| Deployment / ops | Strong — off-host scheduled backups, guarded upgrades + auto-rollback, GPU self-heal, CNPG Postgres + Redis Sentinel HA (opt-in), DR drill in CI |
| Tutoring quality | Good (B+) — judged composite ~89–91; a live, fail-open guidance-enforcer withholds answers on homework-integrity turns |
| Legal / compliance | **Blocked** — all 3 legal docs are DRAFT; no business entity registered |
| Business / SaaS | **Blocked** — billing built but off; no entity; no pricing live |
| **Launch-ready?** | **No** — gated by legal + business (D+), not code |

## Compliance reality

The codebase implements **COPPA/FERPA-supporting architecture** (parental
consent flow, data minimization, retention cleanup, encryption at rest, audit
trails, and the required AI-content + crisis/988 disclosures surfaced in the
chat UI and dashboard). That is **not the same as being legally compliant or
certified.** Legal compliance is blocked because:

- The Terms of Service, Privacy Policy, and Data Processing Agreement in `legal/`
  are all **DRAFT — NOT IN EFFECT**.
- **No business entity is registered**, so COPPA § 312.4(d)(1)'s required
  operator name, mailing address, and phone cannot be published.

See `legal/LAWYER_REVIEW_CHECKLIST.md` for the full blocker list (8 operator
pre-publication blockers + 12 counsel-review items).

## Critical path to launch

1. **Register a business entity** (the keystone — unblocks the COPPA operator
   disclosures, governing law, billing go-live, and `LICENSE_ENFORCED`).
2. **Counsel finalizes** the three legal documents (~2 weeks).
3. **Turn on billing** (built — Lemon Squeezy + offline license tokens — but
   `LICENSE_ENFORCED` defaults off until go-live).
4. Configure `ADMIN_EMAIL` + SMTP so safety/parent alerts actually send. *(The
   required UI disclosures are already surfaced — see
   `docs/compliance/REQUIRED_DISCLOSURES.md`.)*

## What changed recently

**2026-07** — build hardened to ~A across the board. The four build-side HIGH
items closed & merged: CI quality gates now enforce (mypy/pylint/bandit),
versioned migration runner (replaces ad-hoc SQL), and the Postgres+Redis single
points of failure eliminated via CloudNativePG + Redis Sentinel HA (opt-in
overlays). Child-safety MEDs closed (fail-closed profile gate + rate limiter;
classifier fails closed on input **and** output; crisis incidents persist even
for profile-less sessions). Security MEDs closed (INTERNAL_API_KEY rotation;
Sentry PII scrubbing). Parent onboarding + child identity linking, and Open WebUI
promoted to a first-class enterprise k8s workload (CNPG-backed PII, NetworkPolicy
isolation, TLS ingress). CNPG off-cluster PITR made a documented opt-in (was a
`CHANGE-ME` footgun). New **pedagogy guidance-enforcer** (fail-open, off by
default) that withholds the answer on homework-integrity turns. The live home box
was redeployed to `main` and the ML safety classifier confirmed active in prod.

**2026-06** — backbone → `gemma4:e4b`; guarded upgrade framework; semantic safety
classifier; crisis escalation; database actually encrypted at rest (was silently
plaintext); per-child COPPA consent gate on both routes; opt-in `gemma4:31b`
high-end tier (GPU ≥26GB); hold-back streaming; self-healing GPU watchdog.

See `CHANGELOG.md` and `docs/architecture/REQUEST_FLOW_AND_SAFETY.md`.
