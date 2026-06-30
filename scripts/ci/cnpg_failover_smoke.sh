#!/usr/bin/env bash
# Proves CNPG failover: apply a 3-instance cluster, write via -rw, kill the
# primary, assert a standby is promoted and a write through -rw still succeeds.
set -euo pipefail

NS=cnpg-test
CLUSTER=snflwr-pg-ci
RW="${CLUSTER}-rw"
PSQL_IMG="ghcr.io/cloudnative-pg/postgresql:16.4"

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
  kubectl -n "$NS" run psql-$RANDOM --rm -i --restart=Never --image="$PSQL_IMG" \
    --env="PGPASSWORD=ci_pg_pw" -- \
    psql "host=$RW user=snflwr dbname=snflwr_db" -tAc "$1"
}

echo "Writing probe data via $RW..."
run_sql "CREATE TABLE IF NOT EXISTS ha_probe(id int primary key, v text);"
run_sql "INSERT INTO ha_probe VALUES (1,'before') ON CONFLICT (id) DO UPDATE SET v='before';"

OLD_PRIMARY=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}')
echo "Killing primary pod: $OLD_PRIMARY"
kubectl -n "$NS" delete pod "$OLD_PRIMARY" --grace-period=0 --force

echo "Waiting for failover (new primary + writable -rw)..."
for i in $(seq 1 30); do
  NEW_PRIMARY=$(kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [ -n "$NEW_PRIMARY" ] && [ "$NEW_PRIMARY" != "$OLD_PRIMARY" ]; then
    if run_sql "INSERT INTO ha_probe VALUES (2,'after') ON CONFLICT (id) DO UPDATE SET v='after';" \
       && [ "$(run_sql "SELECT v FROM ha_probe WHERE id=2;")" = "after" ]; then
      echo "FAILOVER OK: new primary=$NEW_PRIMARY, -rw writable"
      exit 0
    fi
  fi
  sleep 5
done
echo "FAILOVER FAILED: no promoted primary / -rw not writable in time"
kubectl -n "$NS" get pods -l "cnpg.io/cluster=$CLUSTER" -o wide
exit 1
