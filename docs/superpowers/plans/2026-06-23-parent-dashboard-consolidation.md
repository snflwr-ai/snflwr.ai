# Parent Dashboard Consolidation & Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the Tkinter desktop parent dashboard in favor of the web SPA, modularize and redesign that SPA to be safety-first + activity-first, mobile-first, and accessible — with a zero-dependency JS test layer for the safety-critical logic.

**Architecture:** One web dashboard served at `/dashboard` from `api/static/dashboard/`. The desktop launcher opens that URL in the system browser instead of building a Tk window. The SPA is split from one 44KB `dashboard.js` into focused native ES modules (`core/`, `views/`, `components/`) loaded via `<script type="module">`. View logic that matters lives in pure, DOM-free functions unit-tested with Node's built-in `node:test`. No build step, no runtime/dev npm dependencies, no charting library.

**Tech Stack:** Python 3.12 / FastAPI (unchanged backend), vanilla JavaScript ES modules (no framework, no bundler), CSS with design tokens, `node:test` (built into Node) for JS unit tests, pytest for Python.

## Global Constraints

- **Buildless:** ship native ES modules; no bundler/minifier; no npm dependencies and no `npm install` step. A single zero-dependency `api/static/dashboard/package.json` containing exactly `{"type": "module"}` is permitted and required — Node treats `.js` as CommonJS otherwise, so `node --test` needs this marker to load the ES modules (`.mjs` was rejected to avoid Starlette static MIME issues in the browser). This is a module-system marker, not a dependency manifest or build config. (Refines spec decision 3.)
- **No new JS libraries:** no framework, no charting library. (Spec decisions 2, 4.)
- **CSP-strict:** keep `script-src 'self'`; no inline `<script>`; modules served same-origin from `/dashboard/static/`. (Spec: Code structure.)
- **XSS-safe DOM:** never assign untrusted data to `innerHTML`; build DOM via `document.createElement` / `textContent`, or escape via `escHtml`/`escAttr`. (Spec: Code structure.)
- **Behavior-preserving data layer:** identical endpoints, auth, and CSRF flow as the current SPA. (Spec: Non-Goals, Data flow.)
- **Black 26.3.1** formats Python (`api/ core/ safety/ storage/ utils/`); Pylint `--fail-under=5.0`; coverage ratchet via pytest.ini.
- **Endpoints available:** `POST /api/auth/login`, `POST /api/auth/logout`; `GET/POST/PATCH/DELETE /api/profiles/...`, `GET /api/profiles/parent/{id}`; `GET /api/safety/alerts/...`, `GET /api/safety/incidents/...`, `POST /api/safety/alerts/...`; `GET /api/analytics/usage/{profile_id}`, `GET /api/analytics/activity/{profile_id}`.
- **Dashboard URL:** `http://{system_config.API_HOST}:{system_config.API_PORT}/dashboard`.

---

## File Structure

**Removed:**
- `ui/parent_dashboard.py` (1538-line Tkinter god-file) — deleted.

**Modified:**
- `ui/launcher.py` — `_launch_parent_dashboard` opens the dashboard URL in a browser.
- `tests/test_ui_smoke.py` — drop Tkinter `ParentDashboard` tests; assert launcher opens the URL.
- `api/static/dashboard/index.html` — load `app.js` as a module; add `<noscript>`.
- `api/static/dashboard/dashboard.css` — replaced by tokenized, mobile-first styles.
- `.github/workflows/ci.yml` — add a `node --test` step for the dashboard JS tests.

**Created (SPA modules under `api/static/dashboard/`):**
- `core/dom.js` — `escHtml`, `escAttr`, small safe-DOM helpers.
- `core/session.js` — Bearer-token/session state backed by `sessionStorage` (`sf_token`/`sf_parent_id`/`sf_email`): `getToken`, `getParentId`, `getEmail`, `setSession`, `clearSession`, `isAuthenticated`.
- `core/api.js` — `apiRequest`, `getCsrfToken`, `setUnauthorizedHandler`, error normalization. **Preserves the original auth flow:** sends `Authorization: Bearer <token>` from `core/session.js`; CSRF header on POST/PATCH/DELETE/PUT; on 401 clears session + invokes the unauthorized handler.
- `core/router.js` — `parseRoute`, navigation/mount.
- `core/safety.js` — **pure** `deriveSafetyState(alerts, incidents)` (safety-critical).
- `core/format.js` — **pure** activity aggregation + date/time formatting.
- `views/login.js`, `views/overview.js`, `views/safety.js`, `views/activity.js`, `views/children.js`, `views/settings.js`.
- `components/nav.js`, `components/card.js`, `components/banner.js`, `components/skeleton.js`, `components/svgChart.js` (accessible SVG bars/sparklines).
- `app.js` — entry; wires router + views.
- `tokens.css` — design tokens.

**Created (JS tests, colocated):**
- `api/static/dashboard/tests/dom.test.js`
- `api/static/dashboard/tests/safety.test.js`
- `api/static/dashboard/tests/router.test.js`
- `api/static/dashboard/tests/format.test.js`

> **Note on the current `dashboard.js`:** it is the reference for behavior. Extract logic from it into the modules above; do not delete `dashboard.js` until `app.js` + modules fully replace it (Task 9 removes it).

---

## Task 1: Retire Tkinter dashboard; launcher opens the web dashboard

**Files:**
- Delete: `ui/parent_dashboard.py`
- Modify: `ui/launcher.py` (`_launch_parent_dashboard`, ~line 1061-1075)
- Test: `tests/test_ui_smoke.py`

**Interfaces:**
- Produces: `ui.launcher.ParentLauncher._launch_parent_dashboard(self, session_data: dict) -> None` now calls `webbrowser.open(url)` where `url = f"http://{system_config.API_HOST}:{system_config.API_PORT}/dashboard"`.

- [ ] **Step 1: Read the current launcher method**

Run: `sed -n '1055,1085p' ui/launcher.py` and note how `ParentDashboard` is imported and constructed, and what `session_data` holds.

- [ ] **Step 2: Write the failing test**

Replace the Tkinter `ParentDashboard` smoke tests in `tests/test_ui_smoke.py` that import `from ui.parent_dashboard import ParentDashboard` with a launcher test:

```python
def test_launch_parent_dashboard_opens_browser(monkeypatch):
    """The desktop launcher opens the web dashboard URL, not a Tk window."""
    import ui.launcher as launcher_mod
    from config import system_config

    opened = {}
    monkeypatch.setattr(launcher_mod.webbrowser, "open", lambda url: opened.setdefault("url", url) or True)

    # Build a launcher instance without running Tk (construct minimally / use the
    # existing fixture pattern in this file for a mocked launcher).
    launcher = launcher_mod.ParentLauncher.__new__(launcher_mod.ParentLauncher)
    launcher._launch_parent_dashboard({"parent_id": "p1"})

    expected = f"http://{system_config.API_HOST}:{system_config.API_PORT}/dashboard"
    assert opened["url"] == expected
```

(If `ParentLauncher` needs attributes for this method, set only those the method
touches, mirroring the file's existing smoke-test setup.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_ui_smoke.py::test_launch_parent_dashboard_opens_browser -p no:cacheprovider --no-cov -q`
Expected: FAIL (method still builds a Tk `ParentDashboard`; `webbrowser` may be unimported).

- [ ] **Step 4: Implement the launcher change**

In `ui/launcher.py`, add `import webbrowser` near the top imports, and replace the body of `_launch_parent_dashboard`:

```python
    def _launch_parent_dashboard(self, session_data: dict):
        """Open the web parent dashboard in the system browser.

        The dashboard is served by the API at /dashboard and is reachable from
        any device on the network; we no longer ship a separate desktop GUI.
        """
        from config import system_config

        url = f"http://{system_config.API_HOST}:{system_config.API_PORT}/dashboard"
        try:
            webbrowser.open(url)
            logger.info("Opened parent dashboard in browser: %s", url)
        except Exception as e:  # best-effort; show the URL if the browser fails
            logger.error("Could not open browser for dashboard (%s): %s", url, e)
```

- [ ] **Step 5: Delete the Tkinter dashboard and scrub references**

Run: `git rm ui/parent_dashboard.py`
Run: `grep -rnE "parent_dashboard|ParentDashboard" --include="*.py" . | grep -v safety/parent_dashboard | grep -v __pycache__`
Resolve every remaining hit (imports in `ui/launcher.py`, any `__init__` exports). The Flask `tests/test_parent_dashboard_flask.py` and `safety/parent_dashboard.py` are unrelated — leave them.

- [ ] **Step 6: Run the launcher + UI smoke tests**

Run: `python3 -m pytest tests/test_ui_smoke.py -p no:cacheprovider --no-cov -q`
Expected: PASS (no import errors from the deleted module).

- [ ] **Step 7: Commit**

```bash
git add ui/launcher.py tests/test_ui_smoke.py
git rm ui/parent_dashboard.py 2>/dev/null; git add -A ui/
git commit -m "refactor(ui): retire Tkinter parent dashboard; launcher opens web dashboard"
```

---

## Task 2: `core/dom.js` safe-DOM helpers + tests

**Files:**
- Create: `api/static/dashboard/core/dom.js`
- Test: `api/static/dashboard/tests/dom.test.js`

**Interfaces:**
- Produces: `escHtml(s: string): string`, `escAttr(s: string): string`, `el(tag, attrs?, children?): HTMLElement` (safe element builder — sets text via `textContent`, attrs via `setAttribute`, never `innerHTML`).

- [ ] **Step 1: Write the failing test**

```javascript
// api/static/dashboard/tests/dom.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { escHtml, escAttr } from '../core/dom.js';

test('escHtml escapes angle brackets and ampersands', () => {
  assert.equal(escHtml('<script>&"'), '&lt;script&gt;&amp;"');
});

test('escAttr escapes quotes for attribute context', () => {
  assert.equal(escAttr('"x" & <y>'), '&quot;x&quot; &amp; &lt;y&gt;');
});

test('escHtml coerces non-strings', () => {
  assert.equal(escHtml(42), '42');
  assert.equal(escHtml(null), 'null');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test api/static/dashboard/tests/dom.test.js`
Expected: FAIL (module not found / functions undefined).

- [ ] **Step 3: Implement `core/dom.js`**

Implement string-based escaping (no DOM dependency, so it runs under `node:test`). Match the entities used in the test. `el()` may use `document` — keep it below the pure functions and out of the unit tests.

```javascript
// api/static/dashboard/core/dom.js
export function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function escAttr(str) {
  return escHtml(str).replace(/"/g, '&quot;');
}

// Browser-only safe element builder (not unit-tested; uses document).
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') node.textContent = v;
    else if (k === 'html') throw new Error('el(): raw html is forbidden; use text');
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}
```

> Note: the original `dashboard.js` `escHtml` used `createTextNode().innerHTML`, which also escapes `"`-into-context differently. We standardize on the string version above so it is testable in Node; the test asserts the exact expected output. Verify no view relies on `"` being escaped by `escHtml` (attributes must use `escAttr`).

- [ ] **Step 4: Run to verify it passes**

Run: `node --test api/static/dashboard/tests/dom.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add api/static/dashboard/core/dom.js api/static/dashboard/tests/dom.test.js
git commit -m "feat(dashboard): safe-DOM helpers (core/dom.js) with node:test"
```

---

## Task 3: `core/safety.js` — safety-state derivation (safety-critical) + tests

**Files:**
- Create: `api/static/dashboard/core/safety.js`
- Test: `api/static/dashboard/tests/safety.test.js`

**Interfaces:**
- Produces: `deriveSafetyState(alerts: object[], incidents: object[]): { level: 'clear'|'attention'|'crisis', attentionCount: number, hasCrisis: boolean }`.
  - `level === 'clear'` ONLY when there are zero unresolved alerts AND zero unresolved incidents AND no crisis.
  - An item is unresolved when `resolved`/`is_resolved`/`resolved_at` is falsy (handle all three shapes; `resolved_at` is unresolved when null/empty).
  - `hasCrisis` is true if any item has `severity === 'crisis'` or `category`/`type` containing `'crisis'`/`'escalation'` (case-insensitive). Crisis forces `level: 'crisis'` regardless of resolution.

- [ ] **Step 1: Write the failing test (the dangerous bug class first)**

```javascript
// api/static/dashboard/tests/safety.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deriveSafetyState } from '../core/safety.js';

test('empty inputs => clear', () => {
  const s = deriveSafetyState([], []);
  assert.equal(s.level, 'clear');
  assert.equal(s.attentionCount, 0);
  assert.equal(s.hasCrisis, false);
});

test('all resolved => clear (every resolution shape)', () => {
  const alerts = [{ resolved: true }, { is_resolved: true }, { resolved_at: '2026-06-01T00:00:00Z' }];
  assert.equal(deriveSafetyState(alerts, []).level, 'clear');
});

test('one unresolved alert => attention with count', () => {
  const s = deriveSafetyState([{ resolved: false }, { resolved: true }], []);
  assert.equal(s.level, 'attention');
  assert.equal(s.attentionCount, 1);
});

test('unresolved incident counts too', () => {
  const s = deriveSafetyState([], [{ resolved_at: null }]);
  assert.equal(s.level, 'attention');
  assert.equal(s.attentionCount, 1);
});

test('crisis forces crisis level even if resolved', () => {
  const s = deriveSafetyState([], [{ resolved: true, severity: 'crisis' }]);
  assert.equal(s.level, 'crisis');
  assert.equal(s.hasCrisis, true);
});

test('crisis detected via category/type substring (case-insensitive)', () => {
  assert.equal(deriveSafetyState([{ category: 'Crisis_Escalation', resolved: false }], []).hasCrisis, true);
  assert.equal(deriveSafetyState([{ type: 'ESCALATION', resolved: false }], []).hasCrisis, true);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test api/static/dashboard/tests/safety.test.js`
Expected: FAIL (function undefined).

- [ ] **Step 3: Implement `core/safety.js`**

```javascript
// api/static/dashboard/core/safety.js
function isUnresolved(item) {
  if (item.resolved === true || item.is_resolved === true) return false;
  if (item.resolved_at) return false; // non-empty timestamp => resolved
  return true;
}

function isCrisis(item) {
  if (String(item.severity || '').toLowerCase() === 'crisis') return true;
  const hay = `${item.category || ''} ${item.type || ''}`.toLowerCase();
  return hay.includes('crisis') || hay.includes('escalation');
}

export function deriveSafetyState(alerts = [], incidents = []) {
  const all = [...alerts, ...incidents];
  const attentionCount = all.filter(isUnresolved).length;
  const hasCrisis = all.some(isCrisis);
  let level = 'clear';
  if (hasCrisis) level = 'crisis';
  else if (attentionCount > 0) level = 'attention';
  return { level, attentionCount, hasCrisis };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test api/static/dashboard/tests/safety.test.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add api/static/dashboard/core/safety.js api/static/dashboard/tests/safety.test.js
git commit -m "feat(dashboard): safety-state derivation (fail-safe to attention) + tests"
```

---

## Task 4: `core/router.js` + `core/format.js` (pure) + tests

**Files:**
- Create: `api/static/dashboard/core/router.js`, `api/static/dashboard/core/format.js`
- Test: `api/static/dashboard/tests/router.test.js`, `api/static/dashboard/tests/format.test.js`

**Interfaces:**
- Produces: `parseRoute(hash: string): { view: string, params: object }` — `'#/safety?child=p1'` → `{ view: 'safety', params: { child: 'p1' } }`; empty/`'#/'` → `{ view: 'overview', params: {} }`.
- Produces: `formatDuration(minutes: number): string` (e.g. `90` → `'1h 30m'`, `0` → `'0m'`); `aggregateActivity(sessions: object[]): { totalSessions, totalQuestions, totalMinutes }` summing `questions_asked` and `duration_minutes`.

- [ ] **Step 1: Write failing tests**

```javascript
// api/static/dashboard/tests/router.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseRoute } from '../core/router.js';

test('empty hash => overview', () => {
  assert.deepEqual(parseRoute(''), { view: 'overview', params: {} });
  assert.deepEqual(parseRoute('#/'), { view: 'overview', params: {} });
});
test('view with query params', () => {
  assert.deepEqual(parseRoute('#/safety?child=p1'), { view: 'safety', params: { child: 'p1' } });
});
```

```javascript
// api/static/dashboard/tests/format.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatDuration, aggregateActivity } from '../core/format.js';

test('formatDuration', () => {
  assert.equal(formatDuration(0), '0m');
  assert.equal(formatDuration(90), '1h 30m');
  assert.equal(formatDuration(60), '1h 0m');
});
test('aggregateActivity sums fields', () => {
  const got = aggregateActivity([
    { questions_asked: 3, duration_minutes: 10 },
    { questions_asked: 2, duration_minutes: 5 },
  ]);
  assert.deepEqual(got, { totalSessions: 2, totalQuestions: 5, totalMinutes: 15 });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test api/static/dashboard/tests/router.test.js api/static/dashboard/tests/format.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement `core/router.js` and `core/format.js`**

```javascript
// api/static/dashboard/core/router.js
export function parseRoute(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '');
  if (!raw) return { view: 'overview', params: {} };
  const [view, query = ''] = raw.split('?');
  const params = {};
  for (const pair of query.split('&')) {
    if (!pair) continue;
    const [k, v = ''] = pair.split('=');
    params[decodeURIComponent(k)] = decodeURIComponent(v);
  }
  return { view: view || 'overview', params };
}
```

```javascript
// api/static/dashboard/core/format.js
export function formatDuration(minutes) {
  const m = Math.max(0, Math.round(Number(minutes) || 0));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function aggregateActivity(sessions = []) {
  return sessions.reduce(
    (acc, s) => ({
      totalSessions: acc.totalSessions + 1,
      totalQuestions: acc.totalQuestions + (Number(s.questions_asked) || 0),
      totalMinutes: acc.totalMinutes + (Number(s.duration_minutes) || 0),
    }),
    { totalSessions: 0, totalQuestions: 0, totalMinutes: 0 }
  );
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `node --test api/static/dashboard/tests/router.test.js api/static/dashboard/tests/format.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/static/dashboard/core/router.js api/static/dashboard/core/format.js api/static/dashboard/tests/router.test.js api/static/dashboard/tests/format.test.js
git commit -m "feat(dashboard): pure router + activity-format helpers with tests"
```

---

## Task 5: `core/session.js` + `core/api.js` — auth state & request client

**Files:**
- Create: `api/static/dashboard/core/session.js`, `api/static/dashboard/core/api.js`

**CRITICAL — preserve the original auth flow.** The legacy `dashboard.js` authenticates with a **Bearer token** stored in `sessionStorage` (`sf_token`), set at login from the login response's `token`, and sent on every request as `Authorization: Bearer <token>`. CSRF is sent on POST/PATCH/DELETE/PUT from the `csrf_token` cookie. On 401 it logs the user out. This MUST be preserved — cookie-only auth is NOT equivalent and would break authenticated requests.

**Interfaces:**
- Produces (`core/session.js`): `getToken(): string|null`, `getParentId(): string|null`, `getEmail(): string`, `setSession({token, parentId, email})`, `clearSession()`, `isAuthenticated(): boolean` — all backed by `sessionStorage` keys `sf_token`/`sf_parent_id`/`sf_email`.
- Produces (`core/api.js`): `getCsrfToken(): string`; `setUnauthorizedHandler(fn)`; `apiRequest(method, path, body?): Promise<any>` — sends `Authorization: Bearer <getToken()>` + JSON; `X-CSRF-Token` on POST/PATCH/DELETE/PUT; on 401 calls `clearSession()` + the registered handler and rejects; otherwise throws `{status, detail}` on non-2xx and returns parsed JSON (or `null` for 204).
- Consumed by: Task 8 login (`setSession`), Task 8 `app.js` (`setUnauthorizedHandler`, `isAuthenticated`, `getParentId`).

> Note: the original `apiRequest` returned the raw `Response`; the new views (Task 8) are written against parsed-JSON returns, so api.js parses here. The **wire behavior** (headers, auth, CSRF methods, 401 handling) is what must match — and does.

- [ ] **Step 1: Create `core/session.js`**

```javascript
// api/static/dashboard/core/session.js
const KEYS = { token: 'sf_token', parentId: 'sf_parent_id', email: 'sf_email' };

export function getToken() { return sessionStorage.getItem(KEYS.token); }
export function getParentId() { return sessionStorage.getItem(KEYS.parentId); }
export function getEmail() { return sessionStorage.getItem(KEYS.email) || ''; }

export function setSession({ token, parentId, email }) {
  sessionStorage.setItem(KEYS.token, token);
  sessionStorage.setItem(KEYS.parentId, parentId);
  sessionStorage.setItem(KEYS.email, email);
}

export function clearSession() {
  sessionStorage.removeItem(KEYS.token);
  sessionStorage.removeItem(KEYS.parentId);
  sessionStorage.removeItem(KEYS.email);
}

export function isAuthenticated() { return !!getToken(); }
```

- [ ] **Step 2: Create `core/api.js`**

```javascript
// api/static/dashboard/core/api.js
import { getToken, clearSession } from './session.js';

let _onUnauthorized = null;
export function setUnauthorizedHandler(fn) { _onUnauthorized = fn; }

export function getCsrfToken() {
  const m = document.cookie.match(/csrf_token=([^;]+)/);
  return m ? m[1] : '';
}

const CSRF_METHODS = ['POST', 'PATCH', 'DELETE', 'PUT'];

export function apiRequest(method, path, body) {
  const headers = {
    'Authorization': 'Bearer ' + (getToken() || ''),
    'Content-Type': 'application/json',
  };
  if (CSRF_METHODS.indexOf(method.toUpperCase()) !== -1) {
    headers['X-CSRF-Token'] = getCsrfToken();
  }
  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);
  return fetch(path, opts).then((resp) => {
    if (resp.status === 401) {
      clearSession();
      if (_onUnauthorized) _onUnauthorized();
      return Promise.reject(new Error('Session expired'));
    }
    if (!resp.ok) {
      return resp.json().then(
        (d) => { throw { status: resp.status, detail: d && d.detail }; },
        () => { throw { status: resp.status, detail: null }; }
      );
    }
    return resp.status === 204 ? null : resp.json();
  });
}
```

- [ ] **Step 3: Sanity-check syntax**

Run: `node --check api/static/dashboard/core/session.js && node --check api/static/dashboard/core/api.js`
Expected: no output (valid).

- [ ] **Step 4: Commit**

```bash
git add api/static/dashboard/core/session.js api/static/dashboard/core/api.js
git commit -m "feat(dashboard): session state + api client preserving Bearer-token auth flow"
```

---

## Task 6: Design tokens + responsive CSS foundation

**Files:**
- Create: `api/static/dashboard/tokens.css`
- Rewrite: `api/static/dashboard/dashboard.css`

**Interfaces:**
- Produces: CSS custom properties (color/spacing/type/radius/shadow), a mobile-first layout (app shell, bottom/side nav, cards, banner, skeleton), focus-visible styles, and `.visually-hidden` for accessible SVG fallbacks.

**REQUIRED SUB-SKILL for this task:** Use `frontend-design` to generate the visual design (tokens + component styles). Constraints to pass it: child-safety product → calm, trustworthy, high-contrast; mobile-first; WCAG AA contrast; visible focus states; no external fonts/CDNs (CSP `style-src 'self'`); design tokens as CSS custom properties.

- [ ] **Step 1: Generate tokens** — define `:root` custom properties in `tokens.css` (palette incl. semantic `--color-safe`, `--color-attention`, `--color-crisis`; spacing scale; type scale; radius; shadow). Include a `.visually-hidden` utility and `:focus-visible` outline.
- [ ] **Step 2: Rewrite `dashboard.css`** mobile-first consuming the tokens: app shell, nav (bottom bar on narrow, sidebar ≥ breakpoint), cards/stat-cards, safety banner variants (`.banner--clear/--attention/--crisis`), activity chart container, skeleton loaders, forms/dialogs. No layout shift; large tap targets (min 44px).
- [ ] **Step 3: Verify** no `@import` of remote URLs, no external font links (CSP). Run: `grep -nE "http://|https://|@import url\(" api/static/dashboard/tokens.css api/static/dashboard/dashboard.css` → expect no remote references.
- [ ] **Step 4: Commit**

```bash
git add api/static/dashboard/tokens.css api/static/dashboard/dashboard.css
git commit -m "feat(dashboard): tokenized, mobile-first, accessible CSS foundation"
```

---

## Task 7: Components — `nav`, `card`, `banner`, `skeleton`, `svgChart`

**Files:**
- Create: `api/static/dashboard/components/{nav,card,banner,skeleton,svgChart}.js`

**Interfaces:**
- Consumes: `el`, `escHtml`/`escAttr` from `core/dom.js`; `deriveSafetyState` result shape from `core/safety.js`.
- Produces:
  - `renderBanner(state): HTMLElement` — `state` from `deriveSafetyState`; clear/attention/crisis variants; crisis uses `role="alert"`.
  - `renderNav(currentView, onNavigate): HTMLElement` — semantic `<nav>`, keyboard-focusable links, `aria-current` on active.
  - `statCard({ label, value, hint? }): HTMLElement`.
  - `skeleton(kind): HTMLElement`.
  - `svgChart({ series, labels, title }): HTMLElement` — accessible SVG bars: `role="img"` + `aria-label` summary AND an `.visually-hidden` `<table>` of the same data.

- [ ] **Step 1: Implement components** using only `core/dom.js` helpers (no `innerHTML` of data). For `svgChart`, build `<svg>` with `document.createElementNS`; include the offscreen data table for screen readers.
- [ ] **Step 2: Syntax-check** each: `node --check api/static/dashboard/components/<f>.js`.
- [ ] **Step 3: Commit**

```bash
git add api/static/dashboard/components/
git commit -m "feat(dashboard): accessible UI components incl. SVG chart with table fallback"
```

---

## Task 8: Views + `app.js` entry; wire router

**Files:**
- Create: `api/static/dashboard/views/{login,overview,safety,activity,children,settings}.js`, `api/static/dashboard/app.js`

**Interfaces:**
- Consumes: `core/api.js` (`apiRequest`), `core/router.js` (`parseRoute`), `core/safety.js` (`deriveSafetyState`), `core/format.js`, all `components/*`.
- Produces: each view exports `render(container, params): Promise<void>` that fetches its data and mounts DOM. `app.js` exports nothing; it boots on `DOMContentLoaded`, owns auth state, listens to `hashchange`, and mounts the view from `parseRoute`.

- [ ] **Step 1: Port login flow** — `views/login.js` mirrors current `renderLogin`/`handleLogin` (same `POST /api/auth/login`, same fields). On success, call `setSession({ token: data.token, parentId: data.session.parent_id, email })` from `core/session.js`, then navigate to `#/overview`. (Login itself does NOT use `apiRequest` — it has no token yet; call `fetch('/api/auth/login', ...)` directly like the original.)
- [ ] **Step 2: Overview view** — fetch profiles (`GET /api/profiles/parent/{id}`), per-child alerts/incidents and activity; compute `deriveSafetyState`; render banner + per-child activity summary cards + pending-action cards (COPPA consent, billing/setup).
- [ ] **Step 3: Safety / Activity / Children / Settings views** — port the corresponding logic from `dashboard.js`, using the new components. Activity uses `svgChart` with `aggregateActivity`/`formatDuration`. Children = profile CRUD (same endpoints). Settings = account + billing link + logout.
- [ ] **Step 4: `app.js`** — boot; call `setUnauthorizedHandler(logout)` where `logout` clears session (`clearSession`) and shows login; auth guard uses `isAuthenticated()` (redirect to login when false); `hashchange` → `parseRoute` → `views[view].render`. Render the nav shell once; swap `<main>` content per view. Use `getParentId()` from `core/session.js` where views need the parent id. A logout action calls `POST /api/auth/logout` (best-effort), `clearSession()`, then shows login — mirroring the original.
- [ ] **Step 5: Syntax-check** all: `for f in api/static/dashboard/views/*.js api/static/dashboard/app.js; do node --check "$f"; done` → no output.
- [ ] **Step 6: Commit**

```bash
git add api/static/dashboard/views/ api/static/dashboard/app.js
git commit -m "feat(dashboard): modular views + app entry wired to router"
```

---

## Task 9: Switch `index.html` to modules; remove legacy `dashboard.js`

**Files:**
- Modify: `api/static/dashboard/index.html`
- Delete: `api/static/dashboard/dashboard.js`

- [ ] **Step 1: Update `index.html`** to load the module entry and add a `<noscript>`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>snflwr.ai - Parent Dashboard</title>
    <link rel="stylesheet" href="/dashboard/static/tokens.css">
    <link rel="stylesheet" href="/dashboard/static/dashboard.css">
    <link rel="icon" type="image/png" href="/dashboard/static/icon.png">
</head>
<body>
    <div id="app"></div>
    <noscript>This dashboard requires JavaScript. Please enable it in your browser.</noscript>
    <script type="module" src="/dashboard/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Confirm the static mount serves nested module paths** — the app mounts `api/static/dashboard` at `/dashboard/static` (`api/server.py`). Verify `/dashboard/static/core/api.js` resolves (subdirectories are served by `StaticFiles`). If not, adjust import paths to be relative (`./core/api.js`) in `app.js` and modules.
- [ ] **Step 3: Remove the legacy file** once `app.js` covers all views: `git rm api/static/dashboard/dashboard.js`.
- [ ] **Step 4: Manual smoke** — start the app, open `/dashboard`, log in, click each nav item; verify console has no module 404s/MIME errors. Run: see `/run` or `python -m api.server` then browse `http://localhost:8000/dashboard`.
- [ ] **Step 5: Commit**

```bash
git add api/static/dashboard/index.html
git rm api/static/dashboard/dashboard.js
git commit -m "feat(dashboard): load ES module entry; remove legacy dashboard.js"
```

---

## Task 10: CI step for the JS unit tests

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a step** in the existing test job (or a small dedicated job) after Python tests:

```yaml
    - name: Dashboard JS unit tests
      run: node --test api/static/dashboard/tests/*.test.js
```

(Node is preinstalled on `ubuntu-latest` runners; no `npm install` needed.)

- [ ] **Step 2: Verify locally** the exact command CI runs:

Run: `node --test api/static/dashboard/tests/*.test.js`
Expected: all tests pass (dom, safety, router, format).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run dashboard JS unit tests with node --test"
```

---

## Task 11: Full verification

- [ ] **Step 1: JS tests** — `node --test api/static/dashboard/tests/*.test.js` → all pass.
- [ ] **Step 2: Python suite** — `python3 -m pytest tests/ -p no:cacheprovider --no-cov -m "not integration" -q` → 0 failures (confirms Tkinter removal + launcher change introduced no regressions).
- [ ] **Step 3: Black** — `python3 -m black --check ui/ api/ core/ safety/ storage/ utils/` → clean.
- [ ] **Step 4: Manual responsive/a11y pass** — phone width + desktop width; keyboard-only nav (Tab/Enter); safety banner shows attention/crisis correctly with seeded data; SVG chart's offscreen table present.
- [ ] **Step 5: Final commit if any fixups**

```bash
git add -A && git commit -m "chore(dashboard): verification fixups"
```

---

## Self-Review notes

- **Spec coverage:** consolidation (T1), modular JS incl. dashboard.js god-file (T2-T9), safety-first/activity-first UX (T6-T8), accessible SVG charts (T7), zero-dep node:test layer (T2-T4) + CI (T10), buildless/CSP/XSS constraints (Global Constraints + T6/T7/T9), behavior-preserving endpoints (T5/T8). All covered.
- **Known judgement points for the implementer:** `escHtml` standardization (T2 note — verify attribute escaping uses `escAttr`); static mount of nested module dirs (T9 Step 2 — fall back to relative imports if needed); exact `apiRequest` parity (T5 — diff against current `dashboard.js`).
