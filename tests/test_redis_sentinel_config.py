from config import system_config


def test_celery_broker_config_standalone(monkeypatch):
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_ENABLED", False)
    url, opts = system_config.celery_broker_config()
    assert url == system_config.REDIS_URL
    assert opts == {}


def test_celery_broker_config_sentinel_with_password(monkeypatch):
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_ENABLED", True)
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_MASTER", "mymaster")
    monkeypatch.setattr(system_config, "REDIS_PASSWORD", "secretpw")
    monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "s1:26379,s2:26379,s3:26379")
    url, opts = system_config.celery_broker_config()
    assert url == (
        "sentinel://:secretpw@s1:26379;"
        "sentinel://:secretpw@s2:26379;"
        "sentinel://:secretpw@s3:26379"
    )
    assert opts["master_name"] == "mymaster"
    assert opts["sentinel_kwargs"] == {"password": "secretpw"}


def test_celery_broker_config_sentinel_no_password(monkeypatch):
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_ENABLED", True)
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_MASTER", "mymaster")
    monkeypatch.setattr(system_config, "REDIS_PASSWORD", "")
    monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "s1:26379")
    url, opts = system_config.celery_broker_config()
    assert url == "sentinel://s1:26379"
    assert opts == {"master_name": "mymaster"}


def test_cache_get_client_returns_underlying_client():
    from utils.cache import RedisCache

    c = RedisCache.__new__(RedisCache)  # bypass __init__/connection
    sentinel_marker = object()
    c._client = sentinel_marker
    assert c.get_client() is sentinel_marker

    c._client = None
    assert c.get_client() is None


def test_get_redis_client_standalone(monkeypatch):
    import types

    import utils.celery_config as cc

    # Replace system_config wholesale so the test is robust to other tests that
    # leave the module-level system_config mocked (and avoids patching its
    # read-only REDIS_URL property on a possibly-mocked object).
    monkeypatch.setattr(
        cc,
        "system_config",
        types.SimpleNamespace(
            REDIS_SENTINEL_ENABLED=False, REDIS_URL="redis://localhost:6379/0"
        ),
    )
    sentinel_marker = object()
    monkeypatch.setattr(cc.redis, "from_url", lambda url: (sentinel_marker, url))
    client = cc.get_redis_client()
    assert client == (sentinel_marker, "redis://localhost:6379/0")


def test_get_redis_client_sentinel(monkeypatch):
    import types

    import utils.celery_config as cc
    from utils.cache import cache

    monkeypatch.setattr(
        cc, "system_config", types.SimpleNamespace(REDIS_SENTINEL_ENABLED=True)
    )
    marker = object()
    monkeypatch.setattr(cache, "get_client", lambda: marker)
    assert cc.get_redis_client() is marker
