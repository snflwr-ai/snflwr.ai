#!/bin/sh
# Postgres init hook (runs once, on first cluster init, as the superuser).
# Creates a dedicated, non-superuser role + database for Open WebUI so OWUI —
# the largest attack surface — has zero access to snflwr_db (child PII, incident
# logs, COPPA consent). Password comes from the OWUI_DB_PASSWORD container env
# and is injected via a psql variable (:'var') which quotes/escapes it safely.
set -eu

if [ -z "${OWUI_DB_PASSWORD:-}" ]; then
    echo "init-openwebui: OWUI_DB_PASSWORD is not set; refusing to create the openwebui role with an empty password." >&2
    exit 1
fi

# CREATE DATABASE cannot run inside the DO/transaction block, so guard it with a
# shell check via psql -tAc. Both statements are idempotent.
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     --set=owui_pw="$OWUI_DB_PASSWORD" <<-'EOSQL'
    SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openwebui') AS role_missing \gset
    \if :role_missing
        CREATE ROLE openwebui LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owui_pw';
    \endif
EOSQL

if [ "$(psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'openwebui'" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -c "CREATE DATABASE openwebui OWNER openwebui;"
fi

echo "init-openwebui: ensured role+database 'openwebui'."
