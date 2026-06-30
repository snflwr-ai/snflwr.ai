# tests/test_postgres_cnpg_manifests.py
"""Structural checks on the CloudNativePG HA manifests (no live cluster)."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

K8S = Path(__file__).resolve().parents[1] / "enterprise/k8s"
MANIFEST = K8S / "postgres-cnpg.yaml"
CONFIGMAP = K8S / "configmap.yaml"


def _docs(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_cluster_has_three_instances():
    cluster = next(d for d in _docs(MANIFEST) if d["kind"] == "Cluster")
    assert cluster["apiVersion"].startswith("postgresql.cnpg.io/")
    assert cluster["metadata"]["name"] == "snflwr-pg"
    assert cluster["spec"]["instances"] == 3


def test_pitr_backup_configured():
    cluster = next(d for d in _docs(MANIFEST) if d["kind"] == "Cluster")
    # barmanObjectStore = continuous WAL archiving -> PITR
    assert "barmanObjectStore" in cluster["spec"]["backup"]
    kinds = {d["kind"] for d in _docs(MANIFEST)}
    assert "ScheduledBackup" in kinds


def test_default_configmap_does_not_repoint_to_cnpg():
    cm = next(d for d in _docs(CONFIGMAP) if d.get("kind") == "ConfigMap")
    # CNPG repoint must be opt-in (commented): the default install keeps postgres-service.
    assert cm["data"]["POSTGRES_HOST"] == "postgres-service"


def test_default_postgres_deployment_untouched():
    assert (K8S / "postgres-deployment.yaml").exists()
