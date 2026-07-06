#!/usr/bin/env bash
# Run the Open WebUI config-seed Job AND the mandatory follow-up rollout-restart
# as one step. OWUI caches its config (incl. the seeded snflwr-api proxy bearer)
# at boot, so it must be restarted after the seed — this wrapper makes that
# impossible to forget. No in-cluster RBAC is granted; the operator's own
# kubeconfig drives the restart.
#
# Usage: scripts/owui_k8s_seed_and_restart.sh [namespace]   (default: snflwr-ai)
set -euo pipefail

NS="${1:-snflwr-ai}"
HERE="$(cd "$(dirname "$0")" && pwd)"
JOB_MANIFEST="${HERE}/../enterprise/k8s/open-webui-config-seed-job.yaml"

echo "==> Seeding Open WebUI proxy-bearer config (namespace: ${NS})"
# Re-runnable: clear any prior completed Job first (Jobs are immutable).
kubectl -n "$NS" delete job open-webui-config-seed --ignore-not-found >/dev/null 2>&1 || true
kubectl -n "$NS" apply -f "$JOB_MANIFEST"
kubectl -n "$NS" wait --for=condition=complete job/open-webui-config-seed --timeout=300s

echo "==> Restarting Open WebUI so it reloads the seeded config"
kubectl -n "$NS" rollout restart deployment/open-webui
kubectl -n "$NS" rollout status deployment/open-webui --timeout=300s

echo "==> Done — Open WebUI restarted with the seeded proxy bearer."
