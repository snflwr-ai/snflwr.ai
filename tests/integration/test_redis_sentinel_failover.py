"""Proves Sentinel failover + app reconnect end-to-end against real containers.

Brings up 1 master + 2 replicas + 3 sentinels (docker-compose.sentinel-ci.yml),
connects via the app's Sentinel client, kills the master, and asserts a replica
is promoted and the client transparently reconnects to the new master.
"""

import os
import subprocess
import time

import pytest

pytest.importorskip("redis")
pytestmark = pytest.mark.integration

COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker/compose/docker-compose.sentinel-ci.yml",
]
PASSWORD = "ci_redis_pw"


@pytest.fixture(scope="module")
def sentinel_stack():
    env = {**os.environ, "REDIS_PASSWORD": PASSWORD}
    subprocess.run([*COMPOSE, "up", "-d"], check=True, env=env)
    try:
        # Wait for sentinels to agree on a master (bounded).
        deadline = time.time() + 60
        while time.time() < deadline:
            out = subprocess.run(
                [
                    *COMPOSE,
                    "exec",
                    "-T",
                    "sentinel-1",
                    "redis-cli",
                    "-p",
                    "26379",
                    "-a",
                    PASSWORD,
                    "--no-auth-warning",
                    "sentinel",
                    "get-master-addr-by-name",
                    "mymaster",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            if out.returncode == 0 and out.stdout.strip():
                break
            time.sleep(2)
        else:
            raise RuntimeError("Sentinels never reported a master")
        yield env
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], env=env)


def _cache(monkeypatch):
    from config import system_config

    monkeypatch.setattr(system_config, "REDIS_ENABLED", True)
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_ENABLED", True)
    monkeypatch.setattr(system_config, "REDIS_SENTINEL_MASTER", "mymaster")
    monkeypatch.setattr(system_config, "REDIS_PASSWORD", PASSWORD)
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv(
        "REDIS_SENTINEL_HOSTS", "localhost:26379,localhost:26380,localhost:26381"
    )
    from utils.cache import RedisCache

    return RedisCache(
        enabled=True,
        use_sentinel=True,
        sentinel_master="mymaster",
        sentinel_hosts=[
            ("localhost", 26379),
            ("localhost", 26380),
            ("localhost", 26381),
        ],
        password=PASSWORD,
    )


def test_sentinel_failover_reconnects(sentinel_stack, monkeypatch):
    env = sentinel_stack
    cache = _cache(monkeypatch)
    client = cache.get_client()
    assert client is not None
    client.set("ha_probe", "before")
    assert client.get("ha_probe") == "before"

    # Kill the master; Sentinel should promote a replica within ~5-10s.
    subprocess.run([*COMPOSE, "kill", "redis-master"], check=True, env=env)

    deadline = time.time() + 60
    last_err = None
    while time.time() < deadline:
        try:
            # master_for() transparently re-resolves the promoted master.
            cache.get_client().set("ha_probe", "after")
            if cache.get_client().get("ha_probe") == "after":
                return  # reconnected to the new master
        except Exception as e:  # noqa: BLE001 - retry until failover completes
            last_err = e
        time.sleep(2)
    raise AssertionError(f"client did not reconnect after failover: {last_err}")
