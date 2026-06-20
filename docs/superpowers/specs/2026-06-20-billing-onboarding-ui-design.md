---
title: Billing Onboarding UI — Subscribe / Sign-in (Design Spec)
date: 2026-06-20
status: approved (design) — pending implementation plan
---

# Billing Onboarding UI

## 1. Context & problem

Billing Phases 1–2 are merged: a License Server issues Ed25519 offline tokens,
and `core/licensing.py` + the `proxy_chat` gate enforce them (default off). The
admin-only API at `/api/billing/*` exists (`signin/start`, `signin/verify`,
`status`, `checkout-url`). **But there is no UI** — a parent/admin has no way to
subscribe, sign in, or see license status. The student gate message even says
"Open Settings → Billing," a destination that does not yet exist.

This spec adds that destination as a **Billing tab in the existing admin SPA**.

## 2. Decisions (locked)

| Decision | Choice |
|---|---|
| Placement | **Tab in the existing admin SPA** (`api/static/admin/`), not a standalone page or OWUI function |
| Auth | Reuse the admin SPA's existing admin login + Bearer token (`state.token`) + CSRF; calls go through the existing `api()` helper |
| Scope | Subscribe/Start-trial · Sign-in (email→code) · License-status display · Manage-subscription link |
| Testing | **Python + Playwright E2E** (`@pytest.mark.e2e`, skips when deps/stack absent) — no JS unit-test toolchain added |

## 3. Goals / non-goals

**Goals**
- Give the admin a single place to subscribe, link an existing subscription, see
  status, and manage their plan — consistent with the existing admin UI.
- Reuse the admin SPA's auth and helpers; no new auth, no new page, no new build step.
- Fail soft: when the License Server isn't configured (pre-go-live default), the
  tab renders a clear read-only "not set up yet" state rather than erroring.
- Never expose secrets to the page (the session token is stored server-side by
  `/signin/verify`; the browser only sees status booleans/strings).

**Non-goals (v1)**
- No in-app card entry (Lemon Squeezy hosted checkout handles all payment/PCI).
- No new JS framework or build step; the SPA stays vanilla JS.
- No student/parent-facing surface beyond the existing gate message → admin tab.
- No JS unit-test framework (Vitest/Jest) — Python+Playwright E2E only.

## 4. Architecture

The admin SPA (`api/static/admin/admin.js`) is an IIFE with a `state` object, an
`api(method, path, body)` fetch helper (adds `Authorization: Bearer state.token`
+ CSRF), a `nav(view)` function, and a `render()` that dispatches `state.view`
through a `views` map to loader functions (e.g. `loadOverview`). We add one more
view.

```
admin SPA (/admin)
  ├─ login (existing)  → state.token
  ├─ sidebar nav (renderShell)  ── + "Billing" item
  └─ views map (render)          ── + billing: loadBilling
                                       │  api('GET','/api/billing/status')      → status card
                                       │  api('GET','/api/billing/checkout-url')→ open checkout (new tab)
                                       │  api('POST','/api/billing/signin/start'){email}
                                       │  api('POST','/api/billing/signin/verify'){email,code}
                                       │  api('GET','/api/billing/portal-url')  → open portal (new tab)
                                       └─ all admin-only, via existing Bearer+CSRF
```

Only one tiny backend addition: a `portal-url` endpoint + config value for the
manage-subscription link. Everything else is front-end wiring over existing APIs.

## 5. Components / files

**Backend (small additions):**
- `config.py` — add `LS_CUSTOMER_PORTAL_URL: str = os.getenv("LS_CUSTOMER_PORTAL_URL", "")` in the licensing block.
- `api/routes/billing.py` — add `GET /portal-url` (admin-only, mirrors `checkout-url`): returns `{"url": system_config.LS_CUSTOMER_PORTAL_URL}`.

**Frontend:**
- `api/static/admin/admin.js`:
  - Add `billing: loadBilling` to the `views` map in `render()`.
  - Add a "Billing" `<button class="nav-item" data-view="billing">` to the sidebar in `renderShell()` (matching existing nav items).
  - Implement `loadBilling()`: fetch status, render the status card + action buttons + sign-in form; wire handlers using the existing `api()` helper, `toast()`, and `esc()`.
- `api/static/admin/admin.css` — billing card + state-badge styles, reusing existing CSS tokens/classes where possible.
- `api/static/admin/index.html` — only if the nav is authored in HTML; it is JS-driven (`renderShell`), so likely no change.

**Tests:**
- `tests/test_billing_routes.py` — add a case for `GET /api/billing/portal-url` (admin-only + returns configured URL), mirroring the existing `checkout-url` test.
- `tests/test_billing_ui_e2e.py` (new) — Playwright E2E, see §8.

## 6. UI states & flows

`loadBilling()` calls `GET /api/billing/status` and renders by `state`:

| `status.state` | Card | Actions shown |
|---|---|---|
| `active` / `trialing` | "Subscription active" (+ plan, expiry; "trial" label if trialing) | Manage subscription |
| `grace` | "Payment issue — access continues until <date>" (warning) | Manage subscription, Subscribe |
| `expired` | "Subscription expired" (error) | Subscribe, Sign in |
| `unlicensed` / `none` | "No active subscription" | Subscribe / Start free trial, Sign in |
| server returns **503** (`LICENSE_SERVER_URL` unset) | "Billing isn't set up on this server yet" (info, read-only) | none (buttons hidden) |

**Subscribe / Start trial:** `GET /api/billing/checkout-url` → if `url` non-empty,
`window.open(url, '_blank')`; else hide the button with a hint. (Checkout's
product carries the 10-day trial, so one button covers both.)

**Sign-in (returning / re-install / new device):** inline form — email → `POST
/signin/start` → on 200 reveal a code input → `POST /signin/verify {email, code}`
→ on 200 `{licensed}` re-render status + toast; on 401 inline "invalid or
expired code"; on 5xx/network "couldn't reach billing, try again."

**Manage subscription:** `GET /api/billing/portal-url` → `window.open` the LS
customer portal; hidden if the URL is empty.

## 7. Error handling

- Fail soft, matching the gate philosophy: any billing problem is a message, never
  a broken admin console. A 503 from the API → "not set up yet," not an error toast.
- The `api()` helper throws on non-2xx; `loadBilling` wraps calls in try/catch (or
  `.catch`) and renders inline messages / toasts. Status fetch failure → "couldn't
  load billing status" with a retry button.
- No secrets in the DOM. The page never receives the license token or session; it
  only sees `{state, allowed, plan, exp}` and `{licensed: bool}`.
- Buttons disabled while a request is in flight (prevents double-submit on
  `signin/start` and double checkout opens).

## 8. Testing strategy

**Backend unit (`tests/test_billing_routes.py`):** add `portal-url` — returns the
configured URL; admin-only (rejects without the override). Mirrors `checkout-url`.

**Playwright E2E (`tests/test_billing_ui_e2e.py`, `@pytest.mark.e2e`):**
- Follows the existing E2E convention: **skips gracefully** if `playwright` /
  browser / a servable app isn't available (like `test_e2e_real_stack.py` skips
  when the stack is down) — never breaks the default `pytest` run or CI without infra.
- Serves the FastAPI app (uvicorn subprocess fixture on a test port) and drives
  `/admin` with Chromium.
- Uses Playwright **request interception** (`page.route`) to stub `/api/billing/*`
  responses, so the test exercises the real SPA JS without a live License Server:
  - status `unlicensed` → asserts "Subscribe / Start free trial" + "Sign in" shown.
  - status `active` → asserts active card + "Manage subscription" shown.
  - status endpoint 503 → asserts "isn't set up on this server yet," buttons hidden.
  - sign-in happy path: stub `signin/start`→200 and `signin/verify`→`{ok,licensed:true}`,
    drive email→code→verify, assert status re-renders to active.
  - sign-in bad code: stub `signin/verify`→401, assert inline error shown.
- Admin login is stubbed/seeded the same way the suite already authenticates (or
  the login POST is intercepted) so the test starts at the authenticated shell.

**Manual verification** during the pen-test/verify step: load the running app,
click through Subscribe (opens checkout), the sign-in form, and the status states.

## 9. Dependencies

- `pytest-playwright` + `playwright install chromium` (dev/test only; installed via
  `--break-system-packages` like the other dev deps). Not added to app runtime
  requirements. The E2E test skips if absent, so this is not a hard dev dependency.

## 10. Out of scope
- Phase 0 (entity, real keypair, LS live products), Phase 3 (legal/copy), Phase 4
  polish (dunning banners, trial countdown) — tracked in the billing spec.
- A student/parent self-service surface beyond the admin tab.
