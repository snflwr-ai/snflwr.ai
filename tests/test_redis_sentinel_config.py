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
