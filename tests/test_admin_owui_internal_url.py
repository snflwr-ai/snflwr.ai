"""Admin server-to-server OWUI calls must use OPEN_WEBUI_INTERNAL_URL, and the
compose api services must export it."""

import inspect

from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from api.server import app

client = TestClient(app)


def _admin_session():
    from core.authentication import AuthSession

    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="admin-token",
        email="admin@test.com",
    )


def test_admin_server_modules_use_internal_url():
    """auth.py + profiles.py + families.py must use the INTERNAL url for
    server-to-server OWUI calls, not the browser-facing OPEN_WEBUI_URL."""
    import api.routes.admin.auth as a
    import api.routes.admin.profiles as p
    import api.routes.admin.families as f

    for mod in (a, p, f):
        src = inspect.getsource(mod)
        assert "OPEN_WEBUI_INTERNAL_URL" in src, f"{mod.__name__} should use internal url"
        assert "system_config.OPEN_WEBUI_URL" not in src, (
            f"{mod.__name__} still uses browser-facing OPEN_WEBUI_URL for a server call"
        )


def test_families_passes_internal_url_to_provision(monkeypatch):
    from core.family_provisioning import FamilyResult

    monkeypatch.setenv("OPEN_WEBUI_INTERNAL_URL", "http://sentinel:9999")

    with (
        patch("api.routes.admin.families.provision_family") as mock_pf,
        patch("api.middleware.auth.auth_manager") as mock_am,
        patch(
            "api.middleware.csrf.validate_csrf_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        mock_am.validate_session.return_value = (True, _admin_session())
        mock_pf.return_value = FamilyResult(
            parent_id="p",
            parent_username="a@b.c",
            parent_temp_password="T",
            children=[],
        )
        r = client.post(
            "/api/admin/families",
            json={"parent": {"name": "F", "email": "a@b.c"}, "children": []},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert r.status_code == 200, r.text
    assert mock_pf.called, "provision_family was not called"
    # provision_family(db, open_webui_url, owui_token, ...) — url is 2nd positional
    args, kwargs = mock_pf.call_args
    passed = kwargs.get("open_webui_url", args[1] if len(args) > 1 else None)
    assert passed == "http://sentinel:9999"
