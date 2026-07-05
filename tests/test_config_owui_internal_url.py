from config import system_config


def test_internal_url_defaults_to_public_when_unset(monkeypatch):
    monkeypatch.delenv("OPEN_WEBUI_INTERNAL_URL", raising=False)
    assert system_config.OPEN_WEBUI_INTERNAL_URL == system_config.OPEN_WEBUI_URL


def test_internal_url_uses_override_when_set(monkeypatch):
    monkeypatch.setenv("OPEN_WEBUI_INTERNAL_URL", "http://snflwr-frontend:8080")
    assert system_config.OPEN_WEBUI_INTERNAL_URL == "http://snflwr-frontend:8080"


def test_internal_url_is_independent_of_public(monkeypatch):
    # overriding the internal URL must not change the browser-facing OPEN_WEBUI_URL
    monkeypatch.setenv("OPEN_WEBUI_INTERNAL_URL", "http://internal:8080")
    assert system_config.OPEN_WEBUI_INTERNAL_URL == "http://internal:8080"
    assert system_config.OPEN_WEBUI_URL != "http://internal:8080"
