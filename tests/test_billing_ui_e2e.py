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

pytestmark = pytest.mark.e2e

ADMIN_DIR = Path(__file__).resolve().parent.parent / "api" / "static" / "admin"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def app_url():
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
def page(app_url):
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # browser not installed / sandbox
        pytest.skip(f"Chromium unavailable: {exc}")
    ctx = browser.new_context()
    pg = ctx.new_page()
    # Stub admin login + overview so we land on the authenticated shell.
    pg.route("**/api/admin/login", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"token": "t", "session": {"parent_id": "admin_1"}})))
    pg.route("**/api/admin/stats", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({})))
    yield pg
    ctx.close()
    browser.close()
    pw.stop()


def _status(state, configured=True, plan=None, exp=None):
    return json.dumps({"state": state, "allowed": state in ("active", "trialing", "grace"),
                       "plan": plan, "exp": exp, "configured": configured})


def _login(page, app_url):
    page.goto(f"{app_url}/admin")
    page.fill("#login-email", "admin@x.com")
    page.fill("#login-pass", "pw")
    page.click("#login-btn")
    page.wait_for_selector(".sidebar-nav")


def _open_billing(page):
    page.click('[data-v="billing"]')
    page.wait_for_selector("#billing-view")


def test_unlicensed_shows_subscribe_and_signin(page, app_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json", body=_status("unlicensed")))
    _login(page, app_url)
    _open_billing(page)
    assert "No active subscription" in page.inner_text("#billing-status")
    assert page.is_visible("#billing-subscribe")
    assert page.is_visible("#billing-signin-email")


def test_active_shows_manage(page, app_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=_status("active", plan="family", exp=9999999999)))
    _login(page, app_url)
    _open_billing(page)
    assert "active" in page.inner_text("#billing-status").lower()
    assert page.is_visible("#billing-manage")


def test_not_configured_state(page, app_url):
    page.route("**/api/billing/status", lambda r: r.fulfill(
        status=200, content_type="application/json", body=_status("unlicensed", configured=False)))
    _login(page, app_url)
    _open_billing(page)
    assert "isn" in page.inner_text("#billing-view").lower()  # "isn't set up"
    assert not page.is_visible("#billing-subscribe")
