#!/usr/bin/env bash
# Manual end-to-end smoke test for scripts/migrate_owui_to_postgres.sh.
# Spins a throwaway Postgres + a seed OWUI (creates webui.db with schema+a user),
# then runs the migration and asserts row counts. Requires Docker + network.
set -euo pipefail
NET="owui-smoke-net-$$"; PG="owui-smoke-pg-$$"; OWUI="owui-smoke-fe-$$"
IMG="ghcr.io/open-webui/open-webui:v0.9.6"
# Pinned pgloader version — must match the tag used by
# scripts/migrate_owui_to_postgres.sh (data-critical step; avoid :latest drift).
PGLOADER_IMG="dimitri/pgloader:v3.6.7"
PW="smokepw123"
docker pull "$PGLOADER_IMG" >/dev/null
cleanup() { docker rm -f "$PG" "$OWUI" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$NET" >/dev/null
docker run -d --rm --name "$PG" --network "$NET" \
  -e POSTGRES_USER=snflwr -e POSTGRES_PASSWORD=sp -e POSTGRES_DB=snflwr_db -e OWUI_DB_PASSWORD="$PW" \
  -v "$PWD/enterprise/init-openwebui.sh:/docker-entrypoint-initdb.d/01-init-openwebui.sh:ro" postgres:16.8-alpine >/dev/null
until docker exec "$PG" pg_isready -U snflwr >/dev/null 2>&1; do sleep 1; done; sleep 3
# Seed OWUI on SQLite so webui.db has the real schema + a user row.
# No --rm: the migration script does `docker stop` then `docker cp` (matching a
# real, non-throwaway deployment). cleanup() force-removes it on exit.
docker run -d --name "$OWUI" --network "$NET" "$IMG" >/dev/null
until docker exec "$OWUI" sh -c 'test -f /app/backend/data/webui.db'; do sleep 2; done; sleep 5
docker exec "$OWUI" python -c "import sqlite3,uuid,time; c=sqlite3.connect('/app/backend/data/webui.db'); c.execute(\"INSERT INTO user (id,name,email,role,created_at,updated_at,last_active_at) VALUES (?,?,?,?,?,?,?)\",(str(uuid.uuid4()),'smoke','s@x.io','admin',int(time.time()),int(time.time()),int(time.time()))); c.commit()" || true
OWUI_CONTAINER="$OWUI" POSTGRES_CONTAINER="$PG" OWUI_IMAGE="$IMG" OWUI_DB_PASSWORD="$PW" POSTGRES_USER=snflwr POSTGRES_DB=snflwr_db \
  bash scripts/migrate_owui_to_postgres.sh
N="$(docker exec "$PG" psql -tAX -U openwebui -d openwebui -c 'SELECT COUNT(*) FROM "user"')"
if [ "$N" -ge 1 ]; then
  echo "SMOKE OK (users in postgres: $N)"
else
  echo "SMOKE FAIL"; exit 1
fi
