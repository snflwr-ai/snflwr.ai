# Billing Onboarding UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Billing tab to the existing admin SPA so an admin can subscribe / start a trial, sign in (email→code) to link a subscription, see license status, and open the customer portal.

**Architecture:** A new `billing` view inside the vanilla-JS admin SPA (`api/static/admin/admin.js`), reusing its admin login, Bearer-token `api()` helper, `setMain()`, `navItem()`, `toast()`, and `esc()`. It calls the already-built admin-only `/api/billing/*` endpoints plus one new `GET /api/billing/portal-url`. Tested by a Python+Playwright E2E that serves a static-only app and stubs `/api/*` with `page.route`.

**Tech Stack:** FastAPI + Pydantic (backend), vanilla JS (no build step), pytest, pytest-playwright (E2E, dev-only).

## Global Constraints

- Placement: a tab **inside** `api/static/admin/` — no new page, no new auth, no new build step.
- Reuse existing SPA helpers verbatim: `api(method, path, body)` (returns the `fetch` Response; callers do `.then(r => r.json())`), `setMain(html)`, `navItem(view, icon, label, extra)` (nav binds on `[data-v]`), `toast(msg, type)`, `esc(s)`, `escA(s)`, `mkInput(...)`.
- Admin login: `POST /api/admin/login {email,password}` → `{token, session:{parent_id}}`; token used as `Authorization: Bearer <token>`.
- Billing endpoints are admin-only (`Depends(require_admin)`); all live under prefix `/api/billing`.
- Fail soft: a 503 from billing (LICENSE_SERVER_URL unset) renders an info "not set up yet" state, never an error/broken console. Buttons disabled while a request is in flight.
- No secrets in the DOM: the page only ever sees `{state, allowed, plan, exp}` and `{licensed: bool}` / `{url}` / `{ok}`. The session token is stored server-side by `/signin/verify`.
- E2E test must `@pytest.mark.e2e` and **skip gracefully** when `playwright`/browser is unavailable (match `tests/test_e2e_real_stack.py`), so the default `pytest` run is unaffected.
- Pinned versions stay as in `requirements*.txt`; pytest-playwright is dev/test only, installed with `--break-system-packages` (system Python 3.12, no venv; use `python3`).

---

## File Structure

- `config.py` — add `LS_CUSTOMER_PORTAL_URL` to the licensing block of `_SystemConfig`.
- `api/routes/billing.py` — add `GET /portal-url` (admin-only).
- `tests/test_billing_routes.py` — add `portal-url` unit cases.
- `api/static/admin/admin.js` — add `billing` nav item + `loadBilling()` view (status card, subscribe/manage buttons, sign-in form).
- `api/static/admin/admin.css` — billing card + state-badge styles.
- `tests/test_billing_ui_e2e.py` — new Playwright E2E (serves static-only app, stubs `/api/*`).

---

### Task 1: Backend — `portal-url` endpoint + config

**Files:**
- Modify: `config.py` (licensing block in `_SystemConfig`, near `LS_CHECKOUT_URL`)
- Modify: `api/routes/billing.py` (add route beside `checkout_url`)
- Test: `tests/test_billing_routes.py`

**Interfaces:**
- Consumes: `system_config.LS_CUSTOMER_PORTAL_URL`, existing `require_admin` dependency on the router.
- Produces: `GET /api/billing/portal-url` → `{"url": str}` (admin-only).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_billing_routes.py`:
```python
def test_portal_url_returns_configured_url():
    from config import system_config

    client = TestClient(_make_app())
    with patch.object(system_config, "LS_CUSTOMER_PORTAL_URL", "https://portal.example/x"):
        r = client.get("/api/billing/portal-url")
    assert r.status_code == 200
    assert r.json()["url"] == "https://portal.example/x"


def test_portal_url_requires_admin():
    import api.routes.billing as billing_mod
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(billing_mod.router, prefix="/api/billing")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/billing/portal-url")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_routes.py::test_portal_url_returns_configured_url --no-cov -p no:cacheprovider -v`
Expected: FAIL — 404 (route does not exist yet).

- [ ] **Step 3: Add the config field**

In `config.py`, in the licensing block of `_SystemConfig` (right after `LS_CHECKOUT_URL`):
```python
    LS_CUSTOMER_PORTAL_URL: str = os.getenv("LS_CUSTOMER_PORTAL_URL", "")
```

- [ ] **Step 4: Add the route**

In `api/routes/billing.py`, add after the `checkout_url` handler:
```python
@router.get("/portal-url")
def portal_url():
    return {"url": system_config.LS_CUSTOMER_PORTAL_URL}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_routes.py --no-cov -p no:cacheprovider -v`
Expected: PASS (all billing route tests, including the 2 new ones).

- [ ] **Step 6: Commit**

```bash
git add config.py api/routes/billing.py tests/test_billing_routes.py
git commit -m "feat(billing): add /api/billing/portal-url (customer portal link)"
```

---

### Task 2: E2E harness + Billing tab status view

**Files:**
- Modify: `api/static/admin/admin.js` (nav item + `loadBilling` status render)
- Modify: `api/static/admin/admin.css` (billing card/badge styles)
- Test: `tests/test_billing_ui_e2e.py` (new)

**Interfaces:**
- Consumes: `api()`, `setMain()`, `navItem()`, `esc()`, `nav()`, `state.view`.
- Produces: a `billing` view reachable from the sidebar; `loadBilling()` renders a status card by `GET /api/billing/status` (states: active, trialing, grace, expired, unlicensed/none, plus a 503 "not set up" state). Stable DOM hooks for E2E: container `#billing-view`, status text `#billing-status`, and (in later tasks) buttons `#billing-subscribe`, `#billing-manage`, `#billing-signin-*`.

- [ ] **Step 1: Install E2E deps**

Run:
```bash
python3 -m pip install --break-system-packages pytest-playwright >/dev/null 2>&1
python3 -m playwright install chromium >/dev/null 2>&1
python3 -c "import pytest_playwright; print('pytest-playwright ok')"
```
Expected: `pytest-playwright ok`. (If the browser download is blocked, the E2E test will skip — that's acceptable per Global Constraints.)

- [ ] **Step 2: Write the failing E2E (status states)**

Create `tests/test_billing_ui_e2e.py`:
```python
"""Playwright E2E for the admin Billing tab.

Serves a static-only app (admin SPA assets) and stubs all /api/* calls with
page.route, so this exercises the SPA JS without the backend/DB. Skips
gracefully when playwright / chromium is unavailable (matches test_e2e_real_stack.py).
"""
import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

ADMIN_DIR = Path(__file__).resolve().parent.parent / "api" / "static" / "admin"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def base_url():
    """Serve a static-only FastAPI app exposing the admin SPA at /admin."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI()
    app.mount("/admin/static", StaticFiles(directory=str(ADMIN_DIR)), name="admin-static")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():
        return HTMLResponse((ADMIN_DIR / "index.html").read_text())

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(base_url):
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # browser not installed / sandbox
        pytest.skip(f"Chromium unavailable: {exc}")
    ctx = browser.new_context()
    page = ctx.new_page()
    # Stub admin login + overview so we land on the authenticated shell.
    page.route("**/api/admin/login", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"token": "t", "session": {"parent_id": "admin_1"}})))
    page.route("**/api/admin/stats", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({})))
    yield page
    ctx.close()
    browser.close()
    pw.stop()


def _login(page, base_url):
    page.goto(f"{base_url}/admin")
    page.fill("#login-email", "admin@x.com")
    page.fill("#login-pass", "pw")
    page.click("#login-btn")
    page.wait_for_selector(".sidebar-nav")


def _open_billing(page):
    page.click('[data-v="billing"]')
    page.wait_for_selector("#billing-view")


def test_unlicensed_shows_subscribe_and_signin(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"state": "unlicensed", "allowed": False, "plan": None, "exp": None})))
    _login(page, base_url)
    _open_billing(page)
    assert "No active subscription" in page.inner_text("#billing-status")
    assert page.is_visible("#billing-subscribe")
    assert page.is_visible("#billing-signin-email")


def test_active_shows_manage(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"state": "active", "allowed": True, "plan": "family", "exp": 9999999999})))
    _login(page, base_url)
    _open_billing(page)
    assert "active" in page.inner_text("#billing-status").lower()
    assert page.is_visible("#billing-manage")


def test_not_configured_state(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=503, content_type="application/json",
        body=json.dumps({"detail": "license server not configured"})))
    _login(page, base_url)
    _open_billing(page)
    assert "isn't set up" in page.inner_text("#billing-view").lower()
    assert not page.is_visible("#billing-subscribe")
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py -m e2e --no-cov -p no:cacheprovider -v`
Expected: FAIL (no `[data-v="billing"]` nav item / no `#billing-view`). If it SKIPS (no chromium), note that and proceed — the implementation is still required; verify manually in Task 6.

> Add `e2e` marker registration if pytest warns: it's already used by `test_e2e_real_stack.py`, so `pytest.ini` should know it. If not, add `markers = e2e: end-to-end tests` to `pytest.ini`.

- [ ] **Step 4: Add the nav item**

In `api/static/admin/admin.js`, in `renderShell()`'s Monitoring `nav-section` (after the `audit` nav item, before the closing `</div>`), add a new section:
```javascript
            '    </div>',
            '    <div class="nav-section">',
            '      <div class="nav-section-label">Account</div>',
            navItem('billing', '\u{1F4B3}', 'Billing'),
            '    </div>',
```
(Insert the new `nav-section` block immediately before the `'  </nav>',` line.)

- [ ] **Step 5: Register the view**

In `render()`'s `views` map, add:
```javascript
                audit: loadAudit,
                billing: loadBilling
```

- [ ] **Step 6: Implement `loadBilling()` (status render only)**

Add this function near the other loaders in `admin.js`:
```javascript
    function billingStateCopy(s) {
        switch (s) {
            case 'active': return { cls: 'msg-ok', title: 'Subscription active' };
            case 'trialing': return { cls: 'msg-ok', title: 'Free trial active' };
            case 'grace': return { cls: 'msg-warn', title: 'Payment issue — access continues for now' };
            case 'expired': return { cls: 'msg-error', title: 'Subscription expired' };
            default: return { cls: 'msg', title: 'No active subscription' };
        }
    }

    function billingActionsHtml(s) {
        var subscribe = '<button class="btn btn-primary" id="billing-subscribe">Subscribe / Start free trial</button>';
        var manage = '<button class="btn btn-outline" id="billing-manage">Manage subscription</button>';
        var signin =
            '<div class="billing-signin">' +
            '  <div class="billing-signin-label">Already subscribed? Sign in to link this device:</div>' +
            '  <div class="billing-signin-row">' +
            '    <input type="email" id="billing-signin-email" placeholder="billing email" autocomplete="email">' +
            '    <button class="btn btn-outline" id="billing-signin-start">Send code</button>' +
            '  </div>' +
            '  <div class="billing-code-row" id="billing-code-row" style="display:none">' +
            '    <input type="text" id="billing-signin-code" placeholder="6-digit code" inputmode="numeric">' +
            '    <button class="btn btn-primary" id="billing-signin-verify">Verify</button>' +
            '  </div>' +
            '  <div class="msg msg-error" id="billing-signin-error" style="display:none"></div>' +
            '</div>';
        if (s === 'active' || s === 'trialing') return manage;
        if (s === 'grace') return manage + ' ' + subscribe;
        if (s === 'expired') return subscribe + signin;
        return subscribe + signin;  // unlicensed / none
    }

    function renderBilling(d) {
        var copy = billingStateCopy(d.state);
        var meta = '';
        if (d.plan) meta += '<div class="billing-meta">Plan: ' + esc(d.plan) + '</div>';
        if (d.exp) {
            var when = new Date(d.exp * 1000).toLocaleDateString();
            meta += '<div class="billing-meta">Renews/expires: ' + esc(when) + '</div>';
        }
        setMain([
            '<div id="billing-view" class="page">',
            '  <h1 class="page-title">Billing</h1>',
            '  <div class="card billing-card ' + copy.cls + '">',
            '    <div id="billing-status" class="billing-status-title">' + esc(copy.title) + '</div>',
            meta,
            '  </div>',
            '  <div class="billing-actions">' + billingActionsHtml(d.state) + '</div>',
            '</div>'
        ].join('\n'));
        wireBilling(d.state);
    }

    function renderBillingNotConfigured() {
        setMain([
            '<div id="billing-view" class="page">',
            '  <h1 class="page-title">Billing</h1>',
            '  <div class="card billing-card msg">',
            '    <div id="billing-status" class="billing-status-title">Billing isn’t set up on this server yet.</div>',
            '    <div class="billing-meta">A subscription becomes available once the operator configures the license server.</div>',
            '  </div>',
            '</div>'
        ].join('\n'));
    }

    function loadBilling() {
        api('GET', '/api/billing/status')
            .then(function (r) {
                if (r.status === 503) { renderBillingNotConfigured(); return null; }
                return r.json();
            })
            .then(function (d) { if (d) renderBilling(d); })
            .catch(function () {
                setMain('<div id="billing-view" class="page"><div class="msg msg-error">Couldn’t load billing status. ' +
                    '<button class="btn btn-outline" onclick="location.reload()">Retry</button></div></div>');
            });
    }
```

> `wireBilling(state)` is defined in Tasks 3–4. To keep this task runnable, add a temporary no-op now and replace it in Task 3:
> ```javascript
>     function wireBilling() { /* buttons wired in Tasks 3-4 */ }
> ```

- [ ] **Step 7: Add CSS**

Append to `api/static/admin/admin.css`:
```css
/* Billing tab */
.billing-card { padding: 20px; margin-bottom: 16px; }
.billing-status-title { font-size: 1.1rem; font-weight: 600; }
.billing-meta { color: var(--text-muted, #667); font-size: 0.9rem; margin-top: 6px; }
.billing-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
.billing-signin { margin-top: 12px; width: 100%; }
.billing-signin-label { font-size: 0.9rem; margin-bottom: 6px; }
.billing-signin-row, .billing-code-row { display: flex; gap: 8px; margin-bottom: 8px; }
.billing-signin-row input, .billing-code-row input { flex: 1; max-width: 280px; }
```
(If `.msg-warn` is not already defined, add `.msg-warn { background:#fff7e6; color:#8a6d3b; }` near the other `.msg-*` rules.)

- [ ] **Step 8: Run E2E to verify pass**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py -m e2e --no-cov -p no:cacheprovider -v`
Expected: 3 PASS (or SKIP if chromium unavailable — then verify manually in Task 6).

- [ ] **Step 9: Commit**

```bash
git add api/static/admin/admin.js api/static/admin/admin.css tests/test_billing_ui_e2e.py
git commit -m "feat(billing-ui): Billing tab with license-status card + states"
```

---

### Task 3: Subscribe + Manage buttons

**Files:**
- Modify: `api/static/admin/admin.js` (`wireBilling`)
- Test: `tests/test_billing_ui_e2e.py` (add cases)

**Interfaces:**
- Consumes: `api()`, `toast()`, `#billing-subscribe`, `#billing-manage` (rendered in Task 2).
- Produces: clicking Subscribe opens `GET /api/billing/checkout-url`'s `url` in a new tab; Manage opens `GET /api/billing/portal-url`'s `url`. Empty url → disabled button + hint toast.

- [ ] **Step 1: Write failing E2E cases**

Append to `tests/test_billing_ui_e2e.py`:
```python
def test_subscribe_opens_checkout(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"state": "unlicensed", "allowed": False, "plan": None, "exp": None})))
    page.route("**/api/billing/checkout-url", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"url": "https://buy.example/checkout"})))
    _login(page, base_url)
    _open_billing(page)
    with page.context.expect_page() as new_tab_info:
        page.click("#billing-subscribe")
    new_tab = new_tab_info.value
    assert "buy.example" in new_tab.url


def test_manage_opens_portal(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"state": "active", "allowed": True, "plan": "family", "exp": 9999999999})))
    page.route("**/api/billing/portal-url", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"url": "https://portal.example/me"})))
    _login(page, base_url)
    _open_billing(page)
    with page.context.expect_page() as new_tab_info:
        page.click("#billing-manage")
    assert "portal.example" in new_tab_info.value.url
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py::test_subscribe_opens_checkout -m e2e --no-cov -p no:cacheprovider -v`
Expected: FAIL — no new tab opens (button not wired).

- [ ] **Step 3: Implement the button wiring**

Replace the temporary `wireBilling` no-op in `admin.js` with:
```javascript
    function openUrlFromEndpoint(path, btn) {
        if (btn) btn.disabled = true;
        api('GET', path)
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.url) { window.open(d.url, '_blank'); }
                else { toast('Not configured yet — ask the operator.', 'error'); }
            })
            .catch(function () { toast('Couldn’t reach billing. Try again.', 'error'); })
            .then(function () { if (btn) btn.disabled = false; });
    }

    function wireBilling(state) {
        var sub = document.getElementById('billing-subscribe');
        if (sub) sub.addEventListener('click', function () {
            openUrlFromEndpoint('/api/billing/checkout-url', sub);
        });
        var man = document.getElementById('billing-manage');
        if (man) man.addEventListener('click', function () {
            openUrlFromEndpoint('/api/billing/portal-url', man);
        });
        wireBillingSignin(state);  // defined in Task 4
    }
```

> Add a temporary `function wireBillingSignin() {}` no-op now; Task 4 replaces it.

- [ ] **Step 4: Run to verify pass**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py -m e2e --no-cov -p no:cacheprovider -v`
Expected: status + subscribe + manage tests PASS (or SKIP if no chromium).

- [ ] **Step 5: Commit**

```bash
git add api/static/admin/admin.js tests/test_billing_ui_e2e.py
git commit -m "feat(billing-ui): wire Subscribe (checkout) + Manage (portal) buttons"
```

---

### Task 4: Sign-in flow (email → code → verify)

**Files:**
- Modify: `api/static/admin/admin.js` (`wireBillingSignin`)
- Test: `tests/test_billing_ui_e2e.py` (add cases)

**Interfaces:**
- Consumes: `api()`, `#billing-signin-email`, `#billing-signin-start`, `#billing-code-row`, `#billing-signin-code`, `#billing-signin-verify`, `#billing-signin-error` (rendered in Task 2).
- Produces: `POST /api/billing/signin/start {email}` reveals the code row; `POST /api/billing/signin/verify {email, code}` on 200 re-renders billing (via `loadBilling()`) + toast; on 401 shows inline error.

- [ ] **Step 1: Write failing E2E cases**

Append to `tests/test_billing_ui_e2e.py`:
```python
def test_signin_happy_path(page, base_url):
    # First status call: unlicensed; after verify, status returns active.
    states = iter([
        {"state": "unlicensed", "allowed": False, "plan": None, "exp": None},
        {"state": "active", "allowed": True, "plan": "family", "exp": 9999999999},
    ])
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(next(states))))
    page.route("**/api/billing/signin/start", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"ok": True})))
    page.route("**/api/billing/signin/verify", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"ok": True, "licensed": True})))
    _login(page, base_url)
    _open_billing(page)
    page.fill("#billing-signin-email", "p@x.com")
    page.click("#billing-signin-start")
    page.wait_for_selector("#billing-code-row:visible")
    page.fill("#billing-signin-code", "123456")
    page.click("#billing-signin-verify")
    page.wait_for_function("document.querySelector('#billing-status') && "
                           "/active/i.test(document.querySelector('#billing-status').innerText)")


def test_signin_bad_code_shows_error(page, base_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"state": "unlicensed", "allowed": False, "plan": None, "exp": None})))
    page.route("**/api/billing/signin/start", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"ok": True})))
    page.route("**/api/billing/signin/verify", lambda r: r.fulfill(
        status=401, content_type="application/json", body=json.dumps({"detail": "invalid or expired code"})))
    _login(page, base_url)
    _open_billing(page)
    page.fill("#billing-signin-email", "p@x.com")
    page.click("#billing-signin-start")
    page.wait_for_selector("#billing-code-row:visible")
    page.fill("#billing-signin-code", "000000")
    page.click("#billing-signin-verify")
    page.wait_for_selector("#billing-signin-error:visible")
    assert "invalid" in page.inner_text("#billing-signin-error").lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py::test_signin_happy_path -m e2e --no-cov -p no:cacheprovider -v`
Expected: FAIL — code row never appears (sign-in not wired).

- [ ] **Step 3: Implement sign-in wiring**

Replace the temporary `wireBillingSignin` no-op in `admin.js` with:
```javascript
    function wireBillingSignin(state) {
        var startBtn = document.getElementById('billing-signin-start');
        var emailEl = document.getElementById('billing-signin-email');
        var codeRow = document.getElementById('billing-code-row');
        var verifyBtn = document.getElementById('billing-signin-verify');
        var codeEl = document.getElementById('billing-signin-code');
        var errEl = document.getElementById('billing-signin-error');
        if (!startBtn) return;

        function showErr(msg) { errEl.textContent = msg; errEl.style.display = ''; }
        function clearErr() { errEl.textContent = ''; errEl.style.display = 'none'; }

        startBtn.addEventListener('click', function () {
            var email = (emailEl.value || '').trim();
            if (!email) { showErr('Enter your billing email.'); return; }
            clearErr();
            startBtn.disabled = true;
            api('POST', '/api/billing/signin/start', { email: email })
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'Could not send code'); });
                    codeRow.style.display = '';
                    toast('Code sent — check your email.', 'success');
                })
                .catch(function (ex) { showErr(ex.message); })
                .then(function () { startBtn.disabled = false; });
        });

        verifyBtn.addEventListener('click', function () {
            var email = (emailEl.value || '').trim();
            var code = (codeEl.value || '').trim();
            if (!code) { showErr('Enter the code from your email.'); return; }
            clearErr();
            verifyBtn.disabled = true;
            api('POST', '/api/billing/signin/verify', { email: email, code: code })
                .then(function (r) {
                    if (r.status === 401) throw new Error('Invalid or expired code.');
                    if (!r.ok) throw new Error('Sign-in failed. Try again.');
                    return r.json();
                })
                .then(function (d) {
                    toast(d.licensed ? 'Signed in — subscription active.' : 'Signed in.', 'success');
                    loadBilling();  // re-render status
                })
                .catch(function (ex) { showErr(ex.message); verifyBtn.disabled = false; });
        });
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py -m e2e --no-cov -p no:cacheprovider -v`
Expected: all E2E PASS (or SKIP if no chromium).

- [ ] **Step 5: Commit**

```bash
git add api/static/admin/admin.js tests/test_billing_ui_e2e.py
git commit -m "feat(billing-ui): wire email-code sign-in flow"
```

---

### Task 5: Final verification

**Files:** none (verification).

- [ ] **Step 1: Backend billing tests**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_routes.py --no-cov -p no:cacheprovider -v`
Expected: all PASS.

- [ ] **Step 2: E2E suite**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_ui_e2e.py -m e2e --no-cov -p no:cacheprovider -v`
Expected: all PASS, or all SKIP if chromium unavailable.

- [ ] **Step 3: Manual smoke (if E2E skipped or for confidence)**

Serve a static-only app and click through:
```bash
cd ~/Repos/snflwr.ai && python3 -c "
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
d = Path('api/static/admin')
app = FastAPI()
app.mount('/admin/static', StaticFiles(directory=str(d)), name='s')
@app.get('/admin', response_class=HTMLResponse)
def p(): return HTMLResponse((d/'index.html').read_text())
uvicorn.run(app, host='127.0.0.1', port=8011)
"
```
Open `http://127.0.0.1:8011/admin` — confirm the Billing nav item appears (note: API calls 404 against this static-only server, so this only verifies the nav item + page chrome; full flows are covered by the E2E with stubs).

- [ ] **Step 4: Regression — no broken admin suite**

Run: `cd ~/Repos/snflwr.ai && python3 -m pytest tests/test_billing_routes.py tests/test_license_gate.py tests/test_licensing.py --no-cov -p no:cacheprovider -q`
Expected: all PASS.

- [ ] **Step 5: Commit any final tweaks + use verification-before-completion**

Invoke `superpowers:verification-before-completion` to confirm claims against real output, then `superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**
- §2 placement (admin SPA tab) → Tasks 2–4. ✅
- §5 backend `portal-url` + config → Task 1. ✅
- §5 frontend nav + `loadBilling` → Task 2. ✅
- §6 states table (active/trialing/grace/expired/unlicensed/none/503) → Task 2 `billingStateCopy` + `renderBillingNotConfigured`. ✅
- §6 Subscribe/Manage → Task 3. ✅
- §6 Sign-in flow → Task 4. ✅
- §7 fail-soft, no-secrets, in-flight disable → 503 branch (Task 2), button `.disabled` (Tasks 3–4), page only sees status JSON. ✅
- §8 backend unit (`portal-url`) → Task 1; Playwright E2E with `page.route` stubs, `@pytest.mark.e2e`, skip-if-absent → Tasks 2–4. ✅
- §9 pytest-playwright dev-only install → Task 2 Step 1. ✅

**Placeholder scan:** No TBD/TODO. The two temporary no-ops (`wireBilling`, `wireBillingSignin`) are explicitly introduced and explicitly replaced in later tasks (so each task stays runnable) — not placeholders. ✅

**Type/selector consistency:** DOM hooks consistent across tasks — `#billing-view`, `#billing-status`, `#billing-subscribe`, `#billing-manage`, `#billing-signin-email`, `#billing-signin-start`, `#billing-code-row`, `#billing-signin-code`, `#billing-signin-verify`, `#billing-signin-error`; nav binds on `data-v="billing"` (matches existing `navItem`). `api()` returns the Response (callers `.json()` it) — used consistently. ✅

**Adapt-points flagged for the implementer:** confirm `.msg-ok`/`.msg-warn`/`.msg-error`/`.card`/`.page`/`.page-title` class names exist in `admin.css` (grep first; add `.msg-warn` if missing per Task 2 Step 7); confirm the `e2e` marker is registered in `pytest.ini`.
