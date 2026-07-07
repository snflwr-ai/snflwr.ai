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
    # Exact match (not startswith): asserts the precise CNPG API version and
    # avoids CodeQL's URL-substring-sanitization false positive on the domain.
    assert cluster["apiVersion"] == "postgresql.cnpg.io/v1"
    assert cluster["metadata"]["name"] == "snflwr-pg"
    assert cluster["spec"]["instances"] == 3


def test_pitr_backup_is_optin_not_a_broken_default():
    cluster = next(d for d in _docs(MANIFEST) if d["kind"] == "Cluster")
    # Off-cluster PITR is OPT-IN. The default cluster must NOT ship an active
    # backup block or ScheduledBackup: a placeholder bucket makes CNPG silently
    # fail WAL archiving (reads as "PITR is on"). HA is preserved via 3 instances
    # + local WAL storage; off-cluster archiving needs operator S3 creds.
    assert "backup" not in cluster["spec"]
    assert "walStorage" in cluster["spec"]
    assert "ScheduledBackup" not in {d["kind"] for d in _docs(MANIFEST)}

    raw = MANIFEST.read_text()
    # The old footgun placeholder must be gone...
    assert "CHANGE-ME" not in raw
    # ...but the enablement scaffold stays documented (commented barmanObjectStore
    # block) so an operator can turn PITR on by uncommenting + filling in creds.
    assert "barmanObjectStore" in raw
    assert "OPT-IN" in raw


def test_default_configmap_does_not_repoint_to_cnpg():
    cm = next(d for d in _docs(CONFIGMAP) if d.get("kind") == "ConfigMap")
    # CNPG repoint must be opt-in (commented): the default install keeps postgres-service.
    assert cm["data"]["POSTGRES_HOST"] == "postgres-service"


def test_default_postgres_deployment_untouched():
    assert (K8S / "postgres-deployment.yaml").exists()
