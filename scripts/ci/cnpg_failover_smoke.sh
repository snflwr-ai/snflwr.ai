#!/usr/bin/env bash
# Proves CNPG failover: apply a 3-instance cluster, write via -rw, trigger a
# deterministic switchover via 'kubectl cnpg promote', assert the primary role
# moves to the target replica and -rw remains writable.
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

echo "Writing probe data via $RW..."
run_sql "CREATE TABLE IF NOT EXISTS ha_probe(id int primary key, v text);"
run_sql "INSERT INTO ha_probe VALUES (1,'before') ON CONFLICT (id) DO UPDATE SET v='before';"

OLD_PRIMARY=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}')
echo "Current primary: $OLD_PRIMARY"

TARGET=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=replica" -o jsonpath='{.items[0].metadata.name}')
[ -n "$TARGET" ] || { echo "no replica to promote — cannot run switchover"; exit 1; }
echo "Target replica for switchover: $TARGET"

echo "Triggering switchover: kubectl cnpg promote $CLUSTER $TARGET"
kubectl cnpg promote -n "$NS" "$CLUSTER" "$TARGET"

echo "Waiting for primary role to move to $TARGET and -rw to be writable (30 x 6s = 180s max)..."
for i in $(seq 1 30); do
  CURRENT_PRIMARY=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  echo "  attempt $i: current primary pod = '${CURRENT_PRIMARY}'"
  if [ "$CURRENT_PRIMARY" = "$TARGET" ]; then
    if run_sql "INSERT INTO ha_probe VALUES (2,'after') ON CONFLICT (id) DO UPDATE SET v='after';" \
       && [ "$(run_sql "SELECT v FROM ha_probe WHERE id=2;")" = "after" ]; then
      echo "FAILOVER OK: primary moved $OLD_PRIMARY -> $TARGET, -rw writable"
      exit 0
    fi
  fi
  sleep 6
done

echo "FAILOVER FAILED: primary role did not move to $TARGET within timeout"
kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER" -o wide
exit 1
