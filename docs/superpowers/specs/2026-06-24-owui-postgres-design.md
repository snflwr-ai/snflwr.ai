# Design: Move Enterprise Open WebUI to PostgreSQL

**Date:** 2026-06-24
**Status:** Approved (pending spec review)
**Scope:** Enterprise deployment tier only

## Problem

Open WebUI (OWUI) stores its relational data in SQLite (`webui.db`) inside the
`open-webui-data` volume — all parent/child accounts, chat history, and the
seeded proxy auth key. The enterprise tier already runs a managed PostgreSQL
(`snflwr-db`) with DR coverage for the snflwr-api backend, but OWUI is **not**
wired to it. This blocks multi-instance / HA OWUI and leaves OWUI's relational
state outside DB-level backups (covered only by a volume tarball).

This is item #2 of the OWUI robustness backlog (see memory
`snflwr-ai-owui-robustness`).

## Decisions (locked during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Tier scope | **Enterprise only** | Home tier is intentionally "no PostgreSQL". Enterprise already has Postgres + DR; it is the HA/multi-instance case the gap is about. |
| DB topology | **Dedicated `openwebui` database + dedicated non-superuser role, in the existing `snflwr-db` container** | One Postgres to run/back up/DR. Least-privilege isolation (see below). |
| Existing data | **Automated migration script** (Alembic-first + pgloader data-only) | OWUI has no official SQLite→PG path; this is the robust DIY approach. |
| Vectors/uploads | **Relational only** | Chroma `vector_db/` + `uploads/` stay on the `open-webui-data` volume, still covered by the item-#1 volume backup. pgvector deferred. |
| Backup default | **`OWUI_PG_BACKUP_ENABLED=true`** in enterprise example | Keeps DR honest once relational data moves to Postgres. |
| Version pin | **Bump v0.8.12 → v0.9.6** (folded in) | Committed defaults drifted behind the live deploy. |

## Why a dedicated, non-superuser role (not reuse `snflwr`)

The `postgres` image creates `POSTGRES_USER` (`snflwr`) as a **cluster
superuser**. Reusing it for OWUI would hand the product's largest attack
surface (internet-facing chat UI, large third-party codebase, frequent CVEs)
full read/write over `snflwr_db` — which holds child PII, incident logs, and
COPPA consent records.

A dedicated `openwebui` role that is **non-superuser**, **owns only the
`openwebui` database**, and has **zero grants on `snflwr_db`** contains an OWUI
compromise to OWUI's own data. Secondary benefits: independent credential
rotation, per-role auditability and connection limits, ownership hygiene.

Cost is ~4 lines in `init-db.sql`, one new secret (`OWUI_DB_PASSWORD`), and the
migration/backup scripts using those creds.

## Verified facts (against the live v0.9.6 image)

- OWUI reads `DATABASE_URL` (`env.py:305`, default `sqlite:///{DATA_DIR}/webui.db`).
- It auto-rewrites `postgres://` → `postgresql://` (`env.py:334`).
- The official image bundles `psycopg2` 2.9.11 (both v0.8.12 and v0.9.6).
- OWUI uses SQLAlchemy + Alembic; schema/migrations run on container startup.
- `vector_db/` (Chroma) and `uploads/` remain file-based on the data volume.
- OWUI has **no** official SQLite→PostgreSQL migration procedure (its docs only
  cover copying `webui.db` between SQLite instances).
- `backup_database.py` currently dumps only `POSTGRES_DB` (`snflwr_db`).
- Enterprise secrets are set manually in `.env.production` (no auto-gen);
  `build.sh` only validates presence.

## Components & changes

### A. Wire OWUI to Postgres — `docker/compose/docker-compose.yml`
- Add to the `open-webui` service `environment`:
  `DATABASE_URL=postgresql://openwebui:${OWUI_DB_PASSWORD}@postgres:5432/openwebui`
- Add to `open-webui` `depends_on`: `postgres: { condition: service_healthy }`
  (currently only depends on `snflwr-api`).
- Keep the `open-webui-data` volume (vectors + uploads).

### B. Create role + database — `enterprise/init-db.sql`
Runs only on **fresh** Postgres init. Add:
```sql
-- Open WebUI gets an isolated, non-superuser role and its own database.
CREATE ROLE openwebui LOGIN PASSWORD :'owui_db_password';
CREATE DATABASE openwebui OWNER openwebui;
```
`init-db.sql` cannot read shell env directly; the password is passed via a psql
variable. Implementation detail to resolve in the plan: either (a) template the
value in at deploy time, or (b) have `build.sh`/an init wrapper run a small SQL
snippet with `-v owui_db_password=...` after first boot. The migration script
also ensures the role+DB exist (idempotent) for **existing** `postgres-data`
volumes, where `init-db.sql` will not re-run.

### C. Migration script — `scripts/migrate_owui_to_postgres.sh`
Alembic-first + pgloader data-only. Steps:
1. **Preconditions:** docker available; `openwebui` role+DB exist (create if
   missing); refuse if the target already holds OWUI app data (no double-load).
2. **Quiesce + safety copy:** stop `snflwr-frontend`; `docker cp` the live
   `webui.db` out to the host.
3. **Build schema:** boot one throwaway OWUI container using the **same image
   tag** as the running deployment, pointed at the empty `openwebui` DB, so
   Alembic creates the exact matching schema; then stop it. (Same tag ⇒ source
   and target schemas match by construction — this is why Alembic-first is
   robust.)
4. **Copy data:** run `pgloader` in a throwaway `dimitri/pgloader` container,
   **data-only**, excluding `alembic_version`, with `reset sequences` and FK
   triggers disabled during load.
5. **Verify:** compare per-table row counts (SQLite vs PG); print summary and
   the next step (set `DATABASE_URL`, `docker compose up -d open-webui`).

Safety properties: never mutates the source SQLite; idempotent precondition
checks; aborts loudly rather than half-migrating.

### D. Backup/DR coverage — `scripts/backup_database.py`
- Add a second `pg_dump` for the `openwebui` DB → `snflwr_owui_postgres_<ts>.sql`,
  joining the same off-host + retention flow as the primary dump.
- Gated by `OWUI_PG_BACKUP_ENABLED` (default **on** in the enterprise example).
  Connect as the `openwebui` role (or grant the backup user read on it).
- Mirror the restore path (`restore_postgresql` analogue for the OWUI dump).
- The item-#1 volume backup keeps running for `vector_db/` + `uploads/`.

### E. Env example + docs — `.env.production.example`, maintenance/upgrade docs
- Add `OWUI_DB_PASSWORD=CHANGE-THIS-...` and `OWUI_PG_BACKUP_ENABLED=true`.
- Document the OWUI `DATABASE_URL` and a short migration runbook pointing at the
  script.

### F. Version pin bump (folded in) — v0.8.12 → v0.9.6
- `docker/compose/docker-compose.yml:25` (hardcoded)
- `docker/compose/docker-compose.home.yml:107` (`:-v0.8.12` fallback)
- `deploy.sh:347`
- `scripts/guarded_upgrade.sh:137`

v0.8.12 then becomes the natural rollback baseline. Pruning the 3 stale local
images (`v0.8.3`, `cuda`, `open-terminal:main`, ~18 GB) is a runtime op done
separately, not a repo change.

## Testing

- `shellcheck` the migration script.
- **Dockerized end-to-end dry-run:** seed a small SQLite `webui.db` → run the
  migration → assert per-table row counts match and OWUI boots clean on PG.
- **Backup unit coverage** for the new `openwebui` dump + restore path (repo
  enforces an 85% coverage floor).
- Confirm `docker compose -f docker/compose/docker-compose.yml config` is valid
  after the compose edits.

## Out of scope

- Home tier (stays SQLite).
- Moving Chroma vectors to pgvector (`VECTOR_DB=pgvector`) — later item.
- Self-hosted Langfuse observability (item #3).
- S3 object storage for uploads (item #4).
- Right-sizing the backend/Celery `snflwr` superuser (pre-existing).

## Rollback

OWUI on Postgres is reverted by removing `DATABASE_URL` from the `open-webui`
service and restarting — OWUI falls back to the SQLite `webui.db` still present
on the `open-webui-data` volume (the migration never deletes it). The `openwebui`
database can be dropped independently.
