#!/usr/bin/env bash
# Migrate enterprise Open WebUI from its bundled SQLite webui.db to the dedicated
# `openwebui` Postgres database. Alembic-first + pgloader data-only.
#
#   1. Ensure the openwebui role+DB exist (idempotent; covers existing clusters
#      where the init hook never re-ran).
#   2. Stop OWUI; copy webui.db out of the container (safety copy, source never
#      mutated).
#   3. Boot a throwaway OWUI against the empty openwebui DB so Alembic builds the
#      exact schema (same image tag as the deployment => schemas match).
#   4. pgloader data-only into the Alembic schema, excluding alembic_version,
#      resetting sequences.
#   5. Verify row counts. Print next steps.
#
# Re-runnable: refuses if the target already holds OWUI data unless --force.
set -euo pipefail

OWUI_CONTAINER="${OWUI_CONTAINER:-snflwr-frontend}"
PG_CONTAINER="${POSTGRES_CONTAINER:-snflwr-db}"
OWUI_IMAGE="${OWUI_IMAGE:-ghcr.io/open-webui/open-webui:v0.9.6}"
PG_SUPERUSER="${POSTGRES_USER:-snflwr}"
PG_SUPERDB="${POSTGRES_DB:-snflwr_db}"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

die() { echo "migrate-owui: ERROR: $*" >&2; exit 1; }
log() { echo "migrate-owui: $*"; }

command -v docker >/dev/null || die "docker not found"
[ -n "${OWUI_DB_PASSWORD:-}" ] || die "OWUI_DB_PASSWORD must be set (export it or source your .env.production)"
docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || die "Postgres container '$PG_CONTAINER' not running"
docker inspect "$OWUI_CONTAINER" >/dev/null 2>&1 || die "OWUI container '$OWUI_CONTAINER' not found"

PG_NET="$(docker inspect "$PG_CONTAINER" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' | head -n1)"
[ -n "$PG_NET" ] || die "could not determine Postgres container network"
TS="$(date -u +%Y%m%d_%H%M%S)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

psql_super() { docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_SUPERUSER" -d "$PG_SUPERDB" "$@"; }

# 1. Ensure role + database exist (idempotent).
#
# NOTE: psql does NOT substitute :'var' inside a dollar-quoted DO $$...$$ block,
# so the password would reach the server uninterpreted. Use the proven
# \gset/\if top-level pattern from enterprise/init-openwebui.sh instead, where
# CREATE ROLE runs at the top level and :'owui_pw' is quoted/escaped by psql.
log "ensuring openwebui role + database exist"
psql_super --set=owui_pw="$OWUI_DB_PASSWORD" <<-'EOSQL'
	SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openwebui') AS role_missing \gset
	\if :role_missing
	    CREATE ROLE openwebui LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owui_pw';
	\endif
EOSQL
if [ "$(psql_super -tAc "SELECT 1 FROM pg_database WHERE datname='openwebui'")" != "1" ]; then
    psql_super -c "CREATE DATABASE openwebui OWNER openwebui;"
fi

# Refuse to double-load: 'user' is a core OWUI table; its presence means data exists.
HAS_DATA="$(docker exec -i "$PG_CONTAINER" psql -tAX -U openwebui -d openwebui \
    -c "SELECT to_regclass('public.user') IS NOT NULL AND EXISTS (SELECT 1 FROM public.\"user\")" 2>/dev/null || echo f)"
if [ "$HAS_DATA" = "t" ] && [ "$FORCE" -ne 1 ]; then
    die "target openwebui DB already contains data. Re-run with --force to proceed anyway."
fi

# 2. Stop OWUI, copy webui.db out (safety copy; source untouched).
#
# OWUI runs SQLite in WAL mode, and stopping the container does NOT fully
# checkpoint the WAL into webui.db. So we copy the whole db family — webui.db
# plus its -wal/-shm sidecars (when present) — and keep them side-by-side in
# $WORKDIR. SQLite then replays the WAL when the file is opened (by pgloader and
# by the verification reader), so freshly-committed rows are not lost. Copying
# only webui.db would silently drop any un-checkpointed data.
log "stopping $OWUI_CONTAINER and copying webui.db (+ WAL/SHM) out"
docker stop "$OWUI_CONTAINER" >/dev/null
docker cp "$OWUI_CONTAINER:/app/backend/data/webui.db" "$WORKDIR/webui.db"
for sidecar in webui.db-wal webui.db-shm; do
    docker cp "$OWUI_CONTAINER:/app/backend/data/$sidecar" "$WORKDIR/$sidecar" 2>/dev/null || true
done
cp "$WORKDIR/webui.db" "./webui_migrate_${TS}.db"
log "safety copy saved: ./webui_migrate_${TS}.db"

# 3. Build the schema with Alembic by booting a throwaway OWUI against the empty DB.
log "building schema via Alembic (throwaway $OWUI_IMAGE)"
docker run -d --rm --name owui-migrate-schema --network "$PG_NET" \
    -e "DATABASE_URL=postgresql://openwebui:${OWUI_DB_PASSWORD}@${PG_CONTAINER}:5432/openwebui" \
    "$OWUI_IMAGE" >/dev/null
# Wait for Alembic to stamp the version table (schema built).
for _ in $(seq 1 60); do
    if [ "$(psql_super -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version'")" = "1" ] \
       && [ "$(docker exec -i "$PG_CONTAINER" psql -tAX -U openwebui -d openwebui -c "SELECT to_regclass('public.user') IS NOT NULL")" = "t" ]; then
        break
    fi
    sleep 2
done
docker stop owui-migrate-schema >/dev/null 2>&1 || true
[ "$(docker exec -i "$PG_CONTAINER" psql -tAX -U openwebui -d openwebui -c "SELECT to_regclass('public.user') IS NOT NULL")" = "t" ] \
    || die "Alembic schema build did not complete (no 'user' table)"

# 4. pgloader data-only into the Alembic schema.
log "copying data with pgloader (data only, excluding alembic_version)"
cat > "$WORKDIR/owui.load" <<EOF
LOAD DATABASE
     FROM sqlite:///data/webui.db
     INTO postgresql://openwebui:${OWUI_DB_PASSWORD}@${PG_CONTAINER}:5432/openwebui
 WITH data only, reset sequences, create no tables, create no indexes, preserve index names
 EXCLUDING TABLE NAMES LIKE 'alembic_version'
  SET work_mem to '64MB', maintenance_work_mem to '256MB';
EOF
# Mount the db plus any WAL/SHM sidecars so pgloader's reader replays the WAL.
PGLOADER_MOUNTS=(-v "$WORKDIR/webui.db:/data/webui.db:ro" -v "$WORKDIR/owui.load:/data/owui.load:ro")
for sidecar in webui.db-wal webui.db-shm; do
    [ -f "$WORKDIR/$sidecar" ] && PGLOADER_MOUNTS+=(-v "$WORKDIR/$sidecar:/data/$sidecar:ro")
done
docker run --rm --network "$PG_NET" \
    "${PGLOADER_MOUNTS[@]}" \
    dimitri/pgloader:latest pgloader /data/owui.load

# 5. Verify a couple of row counts match. The source count is read with Python's
# sqlite3 (present in the OWUI image) so the WAL is replayed; mount the sidecars
# read-write so SQLite can checkpoint on open. The pgloader image has no sqlite3.
SRC_MOUNTS=(-v "$WORKDIR/webui.db:/data/webui.db")
for sidecar in webui.db-wal webui.db-shm; do
    [ -f "$WORKDIR/$sidecar" ] && SRC_MOUNTS+=(-v "$WORKDIR/$sidecar:/data/$sidecar")
done
SRC_USERS="$(docker run --rm --entrypoint python "${SRC_MOUNTS[@]}" "$OWUI_IMAGE" \
    -c "import sqlite3; print(sqlite3.connect('/data/webui.db').execute('SELECT COUNT(*) FROM user').fetchone()[0])" 2>/dev/null || echo '?')"
DST_USERS="$(docker exec -i "$PG_CONTAINER" psql -tAX -U openwebui -d openwebui -c 'SELECT COUNT(*) FROM "user"')"
log "row-count check — user: sqlite=$SRC_USERS postgres=$DST_USERS"

cat <<EOF

migrate-owui: DONE.
Next steps:
  1. Confirm the row counts above match.
  2. Ensure OWUI_DB_PASSWORD is set in .env.production.
  3. Redeploy so OWUI uses Postgres:
       docker compose -f docker/compose/docker-compose.yml up -d open-webui
  4. The old SQLite file is still on the open-webui-data volume and in
     ./webui_migrate_${TS}.db (delete once you've verified the migration).
EOF
