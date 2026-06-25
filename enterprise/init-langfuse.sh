#!/bin/sh
# Postgres init hook (runs once on first cluster init, as the superuser).
# Creates a dedicated, non-superuser role + database for self-hosted Langfuse.
# Password comes from LANGFUSE_DB_PASSWORD and is injected via a psql variable
# (:'var'); psql does NOT substitute variables inside a DO $$...$$ block, so the
# role is created at the top level guarded by \gset/\if (idempotent).
set -eu

if [ -z "${LANGFUSE_DB_PASSWORD:-}" ]; then
    echo "init-langfuse: LANGFUSE_DB_PASSWORD is not set; Langfuse is not configured. Skipping — the langfuse role/database will not be created."
    exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     --set=lf_pw="$LANGFUSE_DB_PASSWORD" <<-'EOSQL'
    SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'langfuse') AS role_missing \gset
    \if :role_missing
        CREATE ROLE langfuse LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'lf_pw';
    \endif
EOSQL

if [ "$(psql -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname = 'langfuse'" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -c "CREATE DATABASE langfuse OWNER langfuse;"
fi

echo "init-langfuse: ensured role+database 'langfuse'."
