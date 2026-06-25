# Open WebUI on PostgreSQL (Enterprise)

Enterprise Open WebUI stores its relational data (accounts, chats, settings) in a
dedicated **non-superuser** `openwebui` database inside the existing `snflwr-db`
Postgres container — isolated from `snflwr_db` (child PII, incidents, consent).
Chroma vectors (`vector_db/`) and uploads stay on the `open-webui-data` volume.

## Configuration
- Set `OWUI_DB_PASSWORD` in `.env.production` (a strong, alphanumeric secret:
  `python -c 'import secrets; print(secrets.token_hex(32))'`).
- `docker/compose/docker-compose.yml` wires `DATABASE_URL` for OWUI and passes
  `OWUI_DB_PASSWORD` to the Postgres init hook.

## Fresh install
Nothing extra: on first `postgres` start, `enterprise/init-openwebui.sh` creates
the `openwebui` role + database, and OWUI's Alembic migrations build the schema on
first boot.

## Migrating an existing SQLite install
1. `set -a; . ./.env.production; set +a`  (exports OWUI_DB_PASSWORD)
2. `bash scripts/migrate_owui_to_postgres.sh`
3. Confirm the `user` row counts match — the script prints
   `row-count check — user: sqlite=N postgres=M` to stdout near the end.
4. `docker compose -f docker/compose/docker-compose.yml up -d open-webui`
5. Verify login + chat history, then delete the `./webui_migrate_<ts>.db` safety copy.

The script never mutates the source `webui.db`; to roll back, remove `DATABASE_URL`
from the `open-webui` service and restart — OWUI falls back to the SQLite file
still on the volume.

## Backup / DR
- `OWUI_PG_BACKUP_ENABLED=true` makes `scripts/backup_database.py` also `pg_dump`
  the `openwebui` database, joining the off-host + retention flow. The artifact is
  `snflwr_owui_postgres_<ts>.sql` (or `….sql.gz` when `COMPRESS_BACKUPS=true`).
  Note: despite the `.sql` extension it is **pg_dump custom format** (`-F c`), so
  it must be restored with `pg_restore` — not piped through `psql`. Restore with:
  `restore_postgresql(backup_file, db_name='openwebui', user='openwebui', password=os.getenv('OWUI_DB_PASSWORD'))`
  (the same parameterized `restore_postgresql` in scripts/backup_database.py — it
  invokes `pg_restore` — with the openwebui credentials).
- The OWUI volume backup (`OWUI_BACKUP_ENABLED`) still covers `vector_db/` +
  `uploads/`.

## Rollback of the whole feature
Drop `DATABASE_URL` from `open-webui` and restart. Optionally
`DROP DATABASE openwebui;` once you've confirmed you no longer need it.
