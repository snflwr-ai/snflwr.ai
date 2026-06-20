"""App-side billing routes (admin-only proxy to the License Server)."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app():
    """Minimal app mounting only the billing router, admin auth bypassed."""
    import api.routes.billing as billing_mod
    from api.middleware.auth import require_admin
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(billing_mod.router, prefix="/api/billing")
    app.dependency_overrides[require_admin] = lambda: AuthSession(
        user_id="admin_1", role="admin", session_token="t", email="admin@snflwr.ai")
    return app


def test_status_returns_state():
    from config import system_config
    from core import licensing

    client = TestClient(_make_app())
    with (
        patch.object(system_config, "LICENSE_SERVER_URL", "https://ls.test"),
        patch("core.licensing.current_state",
              return_value=licensing.LicenseState("active", True, "family", 123, "valid")),
    ):
        r = client.get("/api/billing/status")
    assert r.status_code == 200
    assert r.json()["state"] == "active"


def test_signin_verify_stores_session():
    from config import system_config
    import api.routes.billing as billing

    stored = {}
    client = TestClient(_make_app())

    class _Resp:
        status_code = 200

        def json(self):
            return {"session": "sess-xyz"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, timeout=None):
            return _Resp()

    with (
        patch.object(system_config, "LICENSE_SERVER_URL", "https://ls.test"),
        patch.object(billing.httpx, "Client", _Client),
        patch("core.licensing.store_session", lambda t: stored.update(session=t)),
        patch("core.licensing.refresh_once", lambda: True),
    ):
        r = client.post("/api/billing/signin/verify",
                        json={"email": "p@x.com", "code": "123456"})

    assert r.status_code == 200
    assert stored["session"] == "sess-xyz"
    assert r.json()["licensed"] is True


def test_checkout_url_returns_configured_url():
    from config import system_config

    client = TestClient(_make_app())
    with patch.object(system_config, "LS_CHECKOUT_URL", "https://buy.example/checkout"):
        r = client.get("/api/billing/checkout-url")
    assert r.status_code == 200
    assert r.json()["url"] == "https://buy.example/checkout"


def test_requires_admin_when_not_overridden():
    """Without the override, the real require_admin guard should reject."""
    import api.routes.billing as billing_mod

    app = FastAPI()
    app.include_router(billing_mod.router, prefix="/api/billing")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/billing/checkout-url")
    assert r.status_code in (401, 403)


def test_status_reports_configured_flag():
    from config import system_config
    from core import licensing

    client = TestClient(_make_app())
    with (
        patch("core.licensing.current_state",
              return_value=licensing.LicenseState("unlicensed", False, None, None, "no token")),
        patch.object(system_config, "LICENSE_SERVER_URL", ""),
    ):
        assert client.get("/api/billing/status").json()["configured"] is False
    with (
        patch("core.licensing.current_state",
              return_value=licensing.LicenseState("unlicensed", False, None, None, "no token")),
        patch.object(system_config, "LICENSE_SERVER_URL", "https://ls.test"),
    ):
        assert client.get("/api/billing/status").json()["configured"] is True


def test_portal_url_returns_configured_url():
    from config import system_config

    client = TestClient(_make_app())
    with patch.object(system_config, "LS_CUSTOMER_PORTAL_URL", "https://portal.example/x"):
        r = client.get("/api/billing/portal-url")
    assert r.status_code == 200
    assert r.json()["url"] == "https://portal.example/x"


def test_portal_url_requires_admin():
    import api.routes.billing as billing_mod

    app = FastAPI()
    app.include_router(billing_mod.router, prefix="/api/billing")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/billing/portal-url")
    assert r.status_code in (401, 403)
