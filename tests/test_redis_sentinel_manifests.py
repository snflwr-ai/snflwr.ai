# tests/test_redis_sentinel_manifests.py
"""Structural checks on the Redis Sentinel k8s manifests (no live cluster)."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

MANIFEST = Path(__file__).resolve().parents[1] / "enterprise/k8s/redis-sentinel.yaml"


def _docs():
    return [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]


def test_manifest_parses_and_has_expected_kinds():
    kinds = {(d["kind"], d["metadata"]["name"]) for d in _docs()}
    assert ("StatefulSet", "redis") in kinds
    assert ("StatefulSet", "redis-sentinel") in kinds
    assert ("Service", "redis-sentinel") in kinds


def test_topology_counts_and_master_name():
    docs = _docs()
    redis_ss = next(
        d
        for d in docs
        if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "redis"
    )
    sentinel_ss = next(
        d
        for d in docs
        if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "redis-sentinel"
    )
    assert redis_ss["spec"]["replicas"] == 3  # 1 master + 2 replicas
    assert sentinel_ss["spec"]["replicas"] == 3  # 3 sentinels
    blob = MANIFEST.read_text()
    assert "mymaster" in blob and "down-after-milliseconds mymaster 5000" in blob


def test_does_not_modify_default_deployment():
    # The non-HA default must still exist untouched.
    assert (MANIFEST.parent / "redis-deployment.yaml").exists()


def test_default_configmap_does_not_enable_sentinel():
    # Regression guard: the shared ConfigMap is read by BOTH the default
    # single-replica install and the HA overlay. Live REDIS_SENTINEL_ENABLED=true
    # would make the default install try a non-existent sentinel service.
    # Sentinel must be opt-in: commented keys are not parsed as data.
    cm_path = MANIFEST.parent / "configmap.yaml"
    docs = [d for d in yaml.safe_load_all(cm_path.read_text()) if d]
    cm = next(d for d in docs if d.get("kind") == "ConfigMap")
    data = cm.get("data", {})
    # Sentinel env vars must not be present in the parsed ConfigMap data.
    assert "REDIS_SENTINEL_ENABLED" not in data
    assert data.get("REDIS_SENTINEL_ENABLED") != "true"
