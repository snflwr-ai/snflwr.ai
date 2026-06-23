# Parent Dashboard Consolidation & Redesign

**Date:** 2026-06-23
**Status:** Approved (architecture + tech decisions); pending spec review
**Branch:** `refactor/split-god-files`

## Problem

snflwr.ai ships **two** parent dashboards against the same FastAPI backend:

1. `ui/parent_dashboard.py` — a 1538-line Tkinter/CustomTkinter **desktop GUI**,
   launched by `ui/launcher.py` on the host machine.
2. `api/static/dashboard/` — a vanilla-JS **web SPA** served at `/dashboard`
   (`index.html` + `dashboard.css` ~15KB + `dashboard.js` ~44KB).

This is duplicated UI and double maintenance. The Tkinter app only runs on the
host machine, but parents need to reach the dashboard **from their phone / any
device**. The web dashboard already works on localhost, LAN, and remote.

Both god-files (`ui/parent_dashboard.py` and `dashboard.js`) were flagged in the
god-file-split effort.

## Goals

- One parent dashboard, reachable from any device, optimized to be **as easy to
  use as possible**.
- Front-and-center priorities (parent-selected): **safety alerts/incidents** and
  **child activity/learning**.
- Resolve both god-files: retire the Tkinter file; modularize `dashboard.js`.
- No new runtime dependencies or build toolchain (preserve the self-contained /
  USB / offline deployment and the existing CSP-strict, XSS-safe approach).

## Non-Goals

- Remote-access networking (port-forward/tunnel) — a deployment concern, handled
  by the existing network/security stack, not this work.
- Changing backend endpoints or the auth/CSRF flow — data behavior is preserved.
- Migrating to a JS framework.

## Decisions (approved)

1. **Retire the Tkinter dashboard.** Delete `ui/parent_dashboard.py`. Change
   `ui/launcher.py:_launch_parent_dashboard` to open the web dashboard URL in the
   default browser (`webbrowser.open`). Update/trim `tests/test_ui_smoke.py`.
2. **Keep vanilla JS, no build.** Improve the existing SPA in place; do not adopt
   a framework. Load ES modules via `<script type="module">`.

## Architecture

### Consolidation
- Desktop entry point (`ui/launcher.py`) becomes a thin "open dashboard in
  browser" action pointing at `http://{API_HOST}:{API_PORT}/dashboard` (host/port
  from `system_config`).
- All parent UX lives in `api/static/dashboard/`.

### Web dashboard information architecture (safety + activity first)
- **Overview (landing):**
  - Safety status banner: all-clear vs "N items need attention"; crisis/
    escalation events surfaced prominently.
  - Per-child activity summary cards: today's sessions, questions asked, time,
    subjects.
  - Action cards (dismissible/secondary): pending COPPA consent for under-13
    children, billing/subscription status, incomplete setup.
- **Safety:** alerts + incidents timeline; filter by child; review/resolve.
- **Activity:** per-child learning detail (usage + activity over time, recent
  sessions) using `/api/analytics/usage/{profile_id}` and
  `/api/analytics/activity/{profile_id}`.
- **Children:** profile CRUD + per-child consent status.
- **Settings:** account + billing link.

### UX principles
- Mobile-first responsive (single column → multi-column at breakpoints), large
  tap targets.
- Calm, trustworthy visual tone appropriate to a child-safety product.
- Progressive disclosure: summary → detail.
- Accessibility: sufficient contrast, visible focus states, semantic landmarks
  (`<nav>`, `<main>`, headings), keyboard navigation, ARIA where needed.
- Fast perceived load: skeleton/loading states; no layout shift.

### Code structure (modularize the `dashboard.js` god-file)
Split `dashboard.js` into ES modules under `api/static/dashboard/`:
- `core/api.js` — `apiRequest`, CSRF helper, error normalization.
- `core/dom.js` — `escHtml`/`escAttr` and small safe-DOM helpers.
- `core/router.js` — hash/state navigation, view mounting.
- `views/{login,overview,safety,activity,children,settings}.js` — one view each.
- `components/{nav,card,statCard,activityItem,incidentItem,banner,skeleton}.js`.
- `app.js` — entry; wires router + views.

CSS:
- `tokens.css` — design tokens (color, spacing, type scale, radius, shadow).
- `dashboard.css` — layout + components, mobile-first, consuming tokens.

`index.html` loads `app.js` as a module. CSP: keep `script-src 'self'` (modules
are same-origin); no inline scripts. Maintain the existing XSS-safe DOM-building
(no untrusted `innerHTML`).

### Data flow (unchanged)
Same endpoints and auth/CSRF as today:
- Auth: `POST /api/auth/login`, `POST /api/auth/logout`.
- Profiles: `GET/POST/PATCH/DELETE /api/profiles/...`, `GET /api/profiles/parent/{id}`.
- Safety: `GET /api/safety/alerts/...`, `GET /api/safety/incidents/...`,
  `POST /api/safety/alerts/...`.
- Analytics: `GET /api/analytics/usage/{profile_id}`,
  `GET /api/analytics/activity/{profile_id}`.

## Testing

- Backend route/API suites stay green (no endpoint changes).
- `tests/test_ui_smoke.py`: remove the Tkinter `ParentDashboard` tests; if
  launcher behavior is tested, assert it calls `webbrowser.open` with the
  dashboard URL (patched).
- Add lightweight checks where practical for the SPA modules (pure helpers like
  `escHtml`, router parsing) — Node-free, e.g. via a tiny DOM smoke or by keeping
  logic in pure functions that can be reasoned about.
- Manual: responsive check at phone + desktop widths; keyboard-only nav;
  safety-banner all-clear vs attention states.

## Risks & mitigations

- **Removing a working component (Tkinter):** it's launched by `ui/launcher.py`
  and covered by `test_ui_smoke.py`. Mitigation: replace the launch path with a
  browser-open and update tests in the same change; verify the launcher still
  imports/builds.
- **ES module loading under strict CSP / file paths:** modules are served from
  `/dashboard/static/` (same origin) — `script-src 'self'` allows them. Verify
  the static mount serves `.js` with correct MIME.
- **Behavior drift in the SPA rewrite:** keep API calls and auth/CSRF identical;
  redesign is presentation + structure, not data semantics.

## Out-of-scope follow-ups (noted, not done here)

- Charting library vs hand-rolled SVG for activity trends — start with simple
  hand-rolled/CSS bars to avoid a dependency; revisit if richer charts are needed.
- Remote-access UX (showing the LAN URL/QR to reach the dashboard from a phone).
