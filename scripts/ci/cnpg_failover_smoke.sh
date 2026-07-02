#!/usr/bin/env bash
# Proves CNPG HA topology + availability through primary-pod destruction.
#
# Asserts:
#   1. TOPOLOGY: cluster forms with exactly 1 primary + 2 streaming standbys.
#   2. AVAILABILITY: writes through the -rw service survive force-deletion of
#      the primary pod (database stays/returns writable within 240s).
#
# Does NOT script an instance promotion.  kubectl cnpg promote is unreliable
# in kind (targetPrimary does not move consistently) — topology + write
# availability through pod loss is the deterministic, observable guarantee.
set -euo pipefail

NS=cnpg-test
CLUSTER=snflwr-pg-ci
RW="${CLUSTER}-rw"
PSQL_IMG="ghcr.io/cloudnative-pg/postgresql:16.4"
PSQL_N=0

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" apply -f tests/ci/cnpg-cluster.yaml

echo "Waiting for cluster to reach 3 ready instances..."
for i in $(seq 1 60); do
  ready=$(kubectl -n "$NS" get cluster "$CLUSTER" -o jsonpath='{.status.readyInstances}' 2>/dev/null || echo "")
  echo "  readyInstances=$ready"
  [ "$ready" = "3" ] && break
  sleep 10
done
[ "$(kubectl -n "$NS" get cluster "$CLUSTER" -o jsonpath='{.status.readyInstances}')" = "3" ] \
  || { echo "cluster never reached 3 ready instances"; kubectl -n "$NS" describe cluster "$CLUSTER"; exit 1; }

run_sql() {  # $1 = SQL
  PSQL_N=$((PSQL_N+1))
  kubectl -n "$NS" run "psql-${PSQL_N}" --rm -i --restart=Never --image="$PSQL_IMG" \
    --env="PGPASSWORD=ci_pg_pw" -- \
    psql "host=$RW user=snflwr dbname=snflwr_db" -tAc "$1"
}

# ── 1. Topology assertion ────────────────────────────────────────────────────
echo ""
echo "=== TOPOLOGY CHECK ==="
kubectl cnpg status -n "$NS" "$CLUSTER" || true

PRIMARIES=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" --no-headers 2>/dev/null | wc -l)
REPLICAS=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=replica" --no-headers 2>/dev/null | wc -l)
[ "$PRIMARIES" -eq 1 ] && [ "$REPLICAS" -eq 2 ] || {
  echo "BAD TOPOLOGY: primaries=$PRIMARIES replicas=$REPLICAS (expected 1 + 2)"
  kubectl cnpg status -n "$NS" "$CLUSTER" || true
  exit 1
}
echo "TOPOLOGY OK: 1 primary + 2 streaming standbys"

# ── 2. Pre-kill write via -rw ────────────────────────────────────────────────
echo ""
echo "=== PRE-KILL WRITE ==="
run_sql "CREATE TABLE IF NOT EXISTS ha_probe(id int primary key, v text);"
run_sql "INSERT INTO ha_probe VALUES (1,'before') ON CONFLICT (id) DO UPDATE SET v='before';"
VERIFY=$(run_sql "SELECT v FROM ha_probe WHERE id=1;")
[ "$VERIFY" = "before" ] || { echo "Pre-kill read-back failed: got '$VERIFY'"; exit 1; }
echo "Pre-kill write OK: id=1 v=before"

# ── 3. Fault injection ───────────────────────────────────────────────────────
echo ""
echo "=== FAULT INJECTION ==="
OLD_PRIMARY=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}')
echo "Force-deleting primary pod: $OLD_PRIMARY"
kubectl -n "$NS" delete pod "$OLD_PRIMARY" --grace-period=0 --force

# ── 4. Availability assertion ────────────────────────────────────────────────
echo ""
echo "=== AVAILABILITY CHECK (40 x 6s = 240s max) ==="
for i in $(seq 1 40); do
  echo "  attempt $i: testing -rw writability..."
  # run_sql failures are expected during recovery — don't let them abort under set -e
  if OUT=$(run_sql "INSERT INTO ha_probe VALUES (2,'after') ON CONFLICT (id) DO UPDATE SET v='after';" 2>/dev/null) || true; then
    if OUT=$(run_sql "SELECT v FROM ha_probe WHERE id=2;" 2>/dev/null) && [ "$OUT" = "after" ]; then
      echo "AVAILABILITY OK: -rw writable after primary pod ($OLD_PRIMARY) destroyed"
      exit 0
    fi
  fi
  sleep 6
done

# ── Timeout diagnostics ──────────────────────────────────────────────────────
echo ""
echo "=== TIMEOUT DIAGNOSTICS ==="
kubectl cnpg status -n "$NS" "$CLUSTER" || true
kubectl -n "$NS" get cluster "$CLUSTER" -o jsonpath='{.status.readyInstances} {.status.currentPrimary} {.status.phase}'; echo
kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER" -o wide
echo "AVAILABILITY FAILED: -rw not writable within timeout after primary loss"
exit 1
