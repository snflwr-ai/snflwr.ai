# Enterprise Open WebUI → PostgreSQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move enterprise-tier Open WebUI off its bundled SQLite `webui.db` onto a dedicated, non-superuser PostgreSQL database in the existing `snflwr-db` container, with an automated migration path, DR backup coverage, and the OWUI image pin brought up to date.

**Architecture:** OWUI reads `DATABASE_URL`; we point it at `postgresql://openwebui@postgres:5432/openwebui`. The `openwebui` role and database are created by a Postgres init script (fresh installs) and idempotently by the migration script (existing installs). Existing SQLite data is migrated Alembic-first (boot OWUI once against the empty DB so Alembic builds the exact schema) then pgloader copies rows data-only. The Python backup script gains a second `pg_dump` for the `openwebui` database; Chroma vectors and uploads stay on the `open-webui-data` volume (still covered by the existing OWUI volume backup).

**Tech Stack:** Docker Compose, PostgreSQL 16 (`postgres:16.8-alpine`), Open WebUI (SQLAlchemy/Alembic), `dimitri/pgloader` (throwaway container), Bash, Python 3.10–3.12 (`pytest`).

## Global Constraints

- **Scope: enterprise tier only** (`docker/compose/docker-compose.yml`). Do NOT touch the home tier's database wiring (`docker-compose.home.yml` stays SQLite); the home tier is only touched for the image-pin bump.
- **Dedicated role:** OWUI connects as a **non-superuser** `openwebui` role that owns ONLY the `openwebui` database and has **zero** grants on `snflwr_db`. Never reuse the `snflwr` superuser role for OWUI.
- **Relational data only:** move `webui.db` to Postgres via `DATABASE_URL`. Chroma `vector_db/` and `uploads/` remain on the `open-webui-data` volume. Do NOT set `VECTOR_DB=pgvector`.
- **OWUI image pin:** the target version everywhere is **`v0.9.6`** (current live + safety-reviewed). The previous pin `v0.8.12` becomes the rollback baseline.
- **Secrets:** `OWUI_DB_PASSWORD` is an operator-set secret in `.env.production` (placeholder `CHANGE-THIS-...`, generated with `python -c 'import secrets; print(secrets.token_hex(32))'` — hex output is URL-safe and pgloader-safe). Never print secret values; never commit real values.
- **Backup default:** `OWUI_PG_BACKUP_ENABLED` defaults **on** in `.env.production.example`, **off** in code (`os.getenv(..., 'false')`), matching the existing `OWUI_BACKUP_ENABLED` pattern.
- **Fail-closed:** an enabled `openwebui` Postgres backup failure marks the whole backup run unsuccessful (same contract as `OWUI_BACKUP_ENABLED`).
- **Branch:** all work lands on `feat/owui-postgres` (already created off `main`). Commit frequently; one commit per task minimum.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `enterprise/init-openwebui.sh` | Postgres init hook: create the `openwebui` role + database on fresh cluster init | Create |
| `docker/compose/docker-compose.yml` | Enterprise stack: pass `OWUI_DB_PASSWORD` to `postgres`, mount the new init script, set OWUI `DATABASE_URL` + `depends_on`, bump OWUI image pin | Modify |
| `docker/compose/docker-compose.home.yml` | Home stack: OWUI image pin bump only | Modify |
| `deploy.sh` | Home env writer: `OWU_IMAGE_TAG` default bump | Modify |
| `scripts/guarded_upgrade.sh` | Upgrader: `OWU_IMAGE_TAG` fallback bump | Modify |
| `.env.production.example` | Document `OWUI_DB_PASSWORD` + `OWUI_PG_BACKUP_ENABLED` | Modify |
| `scripts/backup_database.py` | Add `backup_open_webui_postgres()`, wire into `run_backup`, add `restore_open_webui_postgres()` | Modify |
| `tests/test_owui_pg_backup.py` | Unit tests for the new backup/restore (mocked `pg_dump`/`pg_restore`) | Create |
| `scripts/migrate_owui_to_postgres.sh` | One-shot SQLite→Postgres migration (Alembic-first + pgloader data-only) | Create |
| `docs/guides/OWUI_POSTGRES.md` | Operator runbook: new installs, migration, backup, rollback | Create |

---

## Task 1: OWUI image pin bump (v0.8.12 → v0.9.6)

Smallest, independent change; done first so it's out of the way. Pure literal swaps — no behavioral logic.

**Files:**
- Modify: `docker/compose/docker-compose.yml:25`
- Modify: `docker/compose/docker-compose.home.yml:106-107`
- Modify: `deploy.sh:347`
- Modify: `scripts/guarded_upgrade.sh:137`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing other tasks depend on (the enterprise compose default tag is referenced again in Task 5's migration script via the running container, not via a literal).

- [ ] **Step 1: Bump the enterprise compose pin**

In `docker/compose/docker-compose.yml`, change line 25 from:
```yaml
    image: ghcr.io/open-webui/open-webui:v0.8.12
```
to:
```yaml
    image: ghcr.io/open-webui/open-webui:v0.9.6
```

- [ ] **Step 2: Bump the home compose pin + comment**

In `docker/compose/docker-compose.home.yml`, change the comment and default on lines 106-107 from:
```yaml
    # previous tag if the smoke test fails. v0.8.12 is the safety-reviewed pin.
    image: ghcr.io/open-webui/open-webui:${OWU_IMAGE_TAG:-v0.8.12}
```
to:
```yaml
    # previous tag if the smoke test fails. v0.9.6 is the safety-reviewed pin.
    image: ghcr.io/open-webui/open-webui:${OWU_IMAGE_TAG:-v0.9.6}
```

- [ ] **Step 3: Bump the deploy.sh env default**

In `deploy.sh`, change line 347 from `OWU_IMAGE_TAG=v0.8.12` to `OWU_IMAGE_TAG=v0.9.6`.

- [ ] **Step 4: Bump the guarded-upgrade fallback**

In `scripts/guarded_upgrade.sh`, change line 137 from:
```bash
owui_current() { get_env_var OWU_IMAGE_TAG "v0.8.12"; }
```
to:
```bash
owui_current() { get_env_var OWU_IMAGE_TAG "v0.9.6"; }
```

- [ ] **Step 5: Verify no stale v0.8.12 references remain in active config**

Run:
```bash
cd ~/Repos/snflwr.ai && grep -rn "open-webui:v0.8.12\|OWU_IMAGE_TAG.*0.8.12\|0\.8\.12" docker/compose/ deploy.sh scripts/guarded_upgrade.sh
```
Expected: no matches (archived docs under `docs/archived/` may still mention old tags — those are out of scope).

- [ ] **Step 6: Validate compose files still parse**

Run:
```bash
cd ~/Repos/snflwr.ai && docker compose -f docker/compose/docker-compose.yml config -q && docker compose -f docker/compose/docker-compose.home.yml config -q && echo "COMPOSE OK"
```
Expected: `COMPOSE OK` (a warning about unset `${OWUI_DB_PASSWORD}` etc. is fine at this task; `config -q` exits 0).

- [ ] **Step 7: Commit**

```bash
cd ~/Repos/snflwr.ai && git add docker/compose/docker-compose.yml docker/compose/docker-compose.home.yml deploy.sh scripts/guarded_upgrade.sh
git commit -m "chore(owui): bump Open WebUI image pin v0.8.12 -> v0.9.6"
```

---

## Task 2: Create the dedicated `openwebui` role + database

A Postgres init hook that runs on **fresh** cluster init (empty data dir). For existing clusters this hook does not re-run; Task 5's migration script creates the role/DB idempotently. The password is read from the container environment and injected via a psql variable (`:'var'`), which quotes/escapes safely — never string-interpolated into SQL.

**Files:**
- Create: `enterprise/init-openwebui.sh`
- Modify: `docker/compose/docker-compose.yml` (postgres service `environment` + a second initdb mount)

**Interfaces:**
- Consumes: `OWUI_DB_PASSWORD` (env, passed into the `postgres` container).
- Produces: a Postgres role `openwebui` (LOGIN, NOSUPERUSER) owning database `openwebui`. Task 3's `DATABASE_URL`, Task 4's backup, and Task 5's migration all connect as `openwebui`/`openwebui`.

- [ ] **Step 1: Create the init hook script**

Create `enterprise/init-openwebui.sh`:
```bash
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
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openwebui') THEN
            CREATE ROLE openwebui LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owui_pw';
        END IF;
    END
    $$;
EOSQL

if [ "$(psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'openwebui'" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -c "CREATE DATABASE openwebui OWNER openwebui;"
fi

echo "init-openwebui: ensured role+database 'openwebui'."
```

- [ ] **Step 2: Make it executable**

Run:
```bash
cd ~/Repos/snflwr.ai && chmod +x enterprise/init-openwebui.sh && git update-index --chmod=+x enterprise/init-openwebui.sh 2>/dev/null; ls -l enterprise/init-openwebui.sh
```
Expected: mode shows `-rwxr-xr-x`.

- [ ] **Step 3: Pass `OWUI_DB_PASSWORD` to the postgres service and mount the hook**

In `docker/compose/docker-compose.yml`, the `postgres` service currently reads (lines ~161-181):
```yaml
  postgres:
    image: postgres:16.8-alpine
    container_name: snflwr-db
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ../../enterprise/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro
    environment:
      - POSTGRES_USER=snflwr
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=snflwr_db
```
Change it to add the env var and the second init mount (note `01-` so it sorts after `init.sql`):
```yaml
  postgres:
    image: postgres:16.8-alpine
    container_name: snflwr-db
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ../../enterprise/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro
      - ../../enterprise/init-openwebui.sh:/docker-entrypoint-initdb.d/01-init-openwebui.sh:ro
    environment:
      - POSTGRES_USER=snflwr
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=snflwr_db
      # Dedicated OWUI role password (see enterprise/init-openwebui.sh). Only the
      # init hook reads this; OWUI itself gets it via DATABASE_URL.
      - OWUI_DB_PASSWORD=${OWUI_DB_PASSWORD}
```

- [ ] **Step 4: Lint the script**

Run:
```bash
cd ~/Repos/snflwr.ai && shellcheck enterprise/init-openwebui.sh && echo "SHELLCHECK OK"
```
Expected: `SHELLCHECK OK` (if `shellcheck` is unavailable, run `sh -n enterprise/init-openwebui.sh && echo "SYNTAX OK"`).

- [ ] **Step 5: Integration test — fresh Postgres creates the role+DB**

Run (spins a throwaway Postgres using the real init hook):
```bash
cd ~/Repos/snflwr.ai && docker run --rm -d --name owui-initdb-test \
  -e POSTGRES_USER=snflwr -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=snflwr_db \
  -e OWUI_DB_PASSWORD=testowuipw \
  -v "$PWD/enterprise/init-openwebui.sh:/docker-entrypoint-initdb.d/01-init-openwebui.sh:ro" \
  postgres:16.8-alpine >/dev/null
# wait for init to finish
until docker exec owui-initdb-test pg_isready -U snflwr -d snflwr_db >/dev/null 2>&1; do sleep 1; done
sleep 3
echo "role:"; docker exec owui-initdb-test psql -tAc "SELECT rolname,rolsuper FROM pg_roles WHERE rolname='openwebui'" -U snflwr -d snflwr_db
echo "db:"; docker exec owui-initdb-test psql -tAc "SELECT datname FROM pg_database WHERE datname='openwebui'" -U snflwr -d snflwr_db
echo "owui can log in:"; docker exec -e PGPASSWORD=testowuipw owui-initdb-test psql -U openwebui -d openwebui -tAc "SELECT current_user"
docker rm -f owui-initdb-test >/dev/null
```
Expected: `role: openwebui|f` (f = not superuser), `db: openwebui`, `owui can log in: openwebui`.

- [ ] **Step 6: Commit**

```bash
cd ~/Repos/snflwr.ai && git add enterprise/init-openwebui.sh docker/compose/docker-compose.yml
git commit -m "feat(owui): create dedicated non-superuser openwebui role+database in Postgres"
```

---

## Task 3: Wire Open WebUI to Postgres

Point OWUI at the `openwebui` database and make it wait for Postgres health.

**Files:**
- Modify: `docker/compose/docker-compose.yml` (the `open-webui` service)

**Interfaces:**
- Consumes: the `openwebui` role+DB from Task 2; `OWUI_DB_PASSWORD` env.
- Produces: a running OWUI whose relational data lives in `openwebui`. Task 5's migration must run before existing-install operators redeploy with this change.

- [ ] **Step 1: Add `DATABASE_URL` and the postgres dependency**

In `docker/compose/docker-compose.yml`, the `open-webui` service has an `environment:` block starting at line 29 and a `depends_on:` at lines 68-69:
```yaml
    environment:
      # OWU talks to snflwr-api's Ollama proxy — NOT directly to Ollama.
      # Safety pipeline is enforced at the proxy layer.
      - OLLAMA_BASE_URL=http://snflwr-api:8000
```
Insert the `DATABASE_URL` immediately after the `environment:` line (before the `OLLAMA_BASE_URL` comment):
```yaml
    environment:
      # Relational store moved off the bundled SQLite webui.db onto a dedicated,
      # non-superuser Postgres database (vector_db/ + uploads/ stay on the volume).
      # OWUI rewrites postgres:// -> postgresql:// automatically (env.py).
      - DATABASE_URL=postgresql://openwebui:${OWUI_DB_PASSWORD}@postgres:5432/openwebui
      # OWU talks to snflwr-api's Ollama proxy — NOT directly to Ollama.
      # Safety pipeline is enforced at the proxy layer.
      - OLLAMA_BASE_URL=http://snflwr-api:8000
```
Then change the `depends_on` block from:
```yaml
    depends_on:
      - snflwr-api
```
to:
```yaml
    depends_on:
      snflwr-api:
        condition: service_started
      postgres:
        condition: service_healthy
```
(The `postgres` service already defines a `pg_isready` healthcheck at lines 176-180.)

- [ ] **Step 2: Validate the compose file parses with the new env**

Run:
```bash
cd ~/Repos/snflwr.ai && OWUI_DB_PASSWORD=x POSTGRES_PASSWORD=x REDIS_PASSWORD=x CHAT_MODEL=qwen3.5:9b WEBUI_SECRET_KEY=x JWT_SECRET_KEY=x INTERNAL_API_KEY=x \
  docker compose -f docker/compose/docker-compose.yml config | grep -A2 "DATABASE_URL\|condition: service_healthy" | head
```
Expected: shows the rendered `DATABASE_URL=postgresql://openwebui:x@postgres:5432/openwebui` and the `service_healthy` condition under `open-webui`.

- [ ] **Step 3: Commit**

```bash
cd ~/Repos/snflwr.ai && git add docker/compose/docker-compose.yml
git commit -m "feat(owui): point enterprise Open WebUI at the openwebui Postgres database"
```

---

## Task 4: Back up (and restore) the `openwebui` Postgres database

Mirror the existing `backup_postgresql()` for the `openwebui` DB, gated by `OWUI_PG_BACKUP_ENABLED`, fail-closed, folded into the same off-host + heartbeat flow. Add a restore helper. Unit-tested with `subprocess.run` mocked.

**Files:**
- Modify: `scripts/backup_database.py`
- Create: `tests/test_owui_pg_backup.py`

**Interfaces:**
- Consumes: env `OWUI_PG_BACKUP_ENABLED`, `OWUI_DB_NAME` (default `openwebui`), `OWUI_DB_USER` (default `openwebui`), `OWUI_DB_PASSWORD`; reuses `system_config.POSTGRES_HOST`/`POSTGRES_PORT`.
- Produces: `DatabaseBackup.backup_open_webui_postgres() -> tuple[bool, str]`, `restore_open_webui_postgres(backup_file: Path) -> bool`. Artifact name: `snflwr_owui_postgres_<ts>.sql[.gz]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_owui_pg_backup.py`:
```python
"""Unit tests for the Open WebUI Postgres backup/restore (mocked pg_dump/pg_restore)."""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def backup_obj(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path))
    monkeypatch.setenv("OWUI_PG_BACKUP_ENABLED", "true")
    monkeypatch.setenv("OWUI_DB_PASSWORD", "owui-secret")
    monkeypatch.setenv("COMPRESS_BACKUPS", "false")
    from scripts.backup_database import DatabaseBackup
    return DatabaseBackup()


def test_owui_pg_backup_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_PATH", str(tmp_path))
    monkeypatch.delenv("OWUI_PG_BACKUP_ENABLED", raising=False)
    from scripts.backup_database import DatabaseBackup
    assert DatabaseBackup().owui_pg_backup_enabled is False


def test_owui_pg_backup_builds_pg_dump_for_openwebui(backup_obj):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["pgpassword"] = kwargs.get("env", {}).get("PGPASSWORD")
        Path(cmd[cmd.index("-f") + 1]).write_text("dump")
        return MagicMock(returncode=0, stderr="")

    with patch("scripts.backup_database.subprocess.run", side_effect=fake_run):
        ok, result = backup_obj.backup_open_webui_postgres()

    assert ok is True
    assert "snflwr_owui_postgres_" in result
    assert "-d" in captured["cmd"] and "openwebui" in captured["cmd"]
    assert "-U" in captured["cmd"] and "openwebui" in captured["cmd"]
    assert captured["pgpassword"] == "owui-secret"


def test_owui_pg_backup_fails_closed_on_pg_dump_error(backup_obj):
    with patch("scripts.backup_database.subprocess.run",
               return_value=MagicMock(returncode=1, stderr="boom")):
        ok, result = backup_obj.backup_open_webui_postgres()
    assert ok is False
    assert "boom" in result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_owui_pg_backup.py -v --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: FAIL — `AttributeError: 'DatabaseBackup' object has no attribute 'owui_pg_backup_enabled'` / `backup_open_webui_postgres`.

- [ ] **Step 3: Add config flags in `DatabaseBackup.__init__`**

In `scripts/backup_database.py`, immediately after the OWUI volume-backup block (after line 69, `self.owui_data_path = ...`), add:
```python
        # Open WebUI relational data on Postgres (enterprise tier). When OWUI is
        # moved off SQLite onto the dedicated `openwebui` database, that data is
        # NOT in snflwr_db and the volume backup only covers vector_db/ + uploads/.
        # This dumps the openwebui DB so relational data stays in DR. Opt-in,
        # fail-closed (same contract as OWUI_BACKUP_ENABLED).
        self.owui_pg_backup_enabled = os.getenv('OWUI_PG_BACKUP_ENABLED', 'false').lower() == 'true'
        self.owui_db_name = os.getenv('OWUI_DB_NAME', 'openwebui').strip()
        self.owui_db_user = os.getenv('OWUI_DB_USER', 'openwebui').strip()
        self.owui_db_password = os.getenv('OWUI_DB_PASSWORD', '')
```
And in the logging block (after line 91, the `Open WebUI container:` log), add:
```python
        logger.info(f"  Open WebUI Postgres backup: {self.owui_pg_backup_enabled}")
```

- [ ] **Step 4: Add the `backup_open_webui_postgres` method**

In `scripts/backup_database.py`, immediately after the `backup_postgresql` method (after line 224, before `def backup_open_webui`), add:
```python
    def backup_open_webui_postgres(self) -> tuple[bool, str]:
        """Back up the Open WebUI `openwebui` Postgres database via pg_dump.

        Enterprise OWUI stores its relational data in the dedicated `openwebui`
        database (NOT snflwr_db). Dumps it as snflwr_owui_postgres_<ts>.sql so it
        joins the same off-host + retention flow. Returns (ok, path_or_reason).
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_path / f"snflwr_owui_postgres_{timestamp}.sql"
            logger.info(f"Creating Open WebUI Postgres backup: {backup_file}")

            host = self._validate_postgres_param(system_config.POSTGRES_HOST, 'POSTGRES_HOST')
            user = self._validate_postgres_param(self.owui_db_user, 'OWUI_DB_USER')
            database = self._validate_postgres_param(self.owui_db_name, 'OWUI_DB_NAME')

            cmd = [
                'pg_dump',
                '-h', host,
                '-p', str(system_config.POSTGRES_PORT),
                '-U', user,
                '-d', database,
                '-F', 'c',
                '-f', str(backup_file),
            ]
            env = os.environ.copy()
            env['PGPASSWORD'] = self.owui_db_password

            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error(f"Open WebUI pg_dump failed: {result.stderr}")
                return False, result.stderr

            if self.compress_backups:
                compressed_file = Path(str(backup_file) + '.gz')
                with open(backup_file, 'rb') as f_in:
                    with gzip.open(compressed_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                backup_file.unlink()
                backup_file = compressed_file

            size_mb = backup_file.stat().st_size / (1024 * 1024)
            logger.info(f"[OK] Open WebUI Postgres backup completed: {backup_file} ({size_mb:.2f} MB)")
            return True, str(backup_file)

        except subprocess.TimeoutExpired:
            logger.error("Open WebUI Postgres backup timed out after 5 minutes")
            return False, "Backup timeout"
        except FileNotFoundError:
            logger.error("pg_dump command not found. Install PostgreSQL client tools.")
            return False, "pg_dump not found"
        except Exception as e:
            logger.exception(f"Open WebUI Postgres backup failed: {e}")
            return False, str(e)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_owui_pg_backup.py -v --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: 3 passed.

- [ ] **Step 6: Wire into `run_backup` (fail-closed, joins off-host flow)**

In `scripts/backup_database.py`, the `run_backup` method has the OWUI volume block at lines 507-515 and the off-host block at 519-529. Replace the OWUI volume block:
```python
        owui_artifact = None
        owui_failed = False
        if success and self.owui_backup_enabled:
            owui_ok, owui_result = self.backup_open_webui()
            if owui_ok:
                owui_artifact = owui_result
            else:
                logger.error(f"[FAIL] Open WebUI backup failed: {owui_result}")
                owui_failed = True
```
with (adds the Postgres dump alongside the volume archive):
```python
        owui_artifact = None
        owui_failed = False
        if success and self.owui_backup_enabled:
            owui_ok, owui_result = self.backup_open_webui()
            if owui_ok:
                owui_artifact = owui_result
            else:
                logger.error(f"[FAIL] Open WebUI backup failed: {owui_result}")
                owui_failed = True

        owui_pg_artifact = None
        if success and self.owui_pg_backup_enabled:
            pg_ok, pg_result = self.backup_open_webui_postgres()
            if pg_ok:
                owui_pg_artifact = pg_result
            else:
                logger.error(f"[FAIL] Open WebUI Postgres backup failed: {pg_result}")
                owui_failed = True
```
Then in the off-host block, change the artifact assembly from:
```python
            artifacts = [result, metadata_file]
            if owui_artifact:
                artifacts.append(owui_artifact)
```
to:
```python
            artifacts = [result, metadata_file]
            if owui_artifact:
                artifacts.append(owui_artifact)
            if owui_pg_artifact:
                artifacts.append(owui_pg_artifact)
```

- [ ] **Step 7: Add the restore helper**

In `scripts/backup_database.py`, immediately after the `restore_postgresql` function ends (after line 662, before `def main():`), add:
```python
def restore_open_webui_postgres(backup_file: Path) -> bool:
    """Restore the Open WebUI `openwebui` Postgres database from a pg_dump backup."""
    logger.info(f"Restoring Open WebUI Postgres database from: {backup_file}")

    try:
        # Decompress if needed
        if backup_file.suffix == '.gz':
            temp_file = backup_file.with_suffix('')
            with gzip.open(backup_file, 'rb') as f_in:
                with open(temp_file, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            restore_source = temp_file
        else:
            restore_source = backup_file

        import re

        def validate_param(param: str, param_name: str) -> str:
            if not re.match(r'^[a-zA-Z0-9._-]+$', param):
                raise ValueError(
                    f"Invalid {param_name}: contains disallowed characters. "
                    f"Only alphanumeric, dots, hyphens, and underscores are allowed."
                )
            return param

        host = validate_param(system_config.POSTGRES_HOST, 'POSTGRES_HOST')
        user = validate_param(os.getenv('OWUI_DB_USER', 'openwebui'), 'OWUI_DB_USER')
        database = validate_param(os.getenv('OWUI_DB_NAME', 'openwebui'), 'OWUI_DB_NAME')

        cmd = [
            'pg_restore',
            '-h', host,
            '-p', str(system_config.POSTGRES_PORT),
            '-U', user,
            '-d', database,
            '--clean',
            '--if-exists',
            str(restore_source),
        ]

        env = os.environ.copy()
        env['PGPASSWORD'] = os.getenv('OWUI_DB_PASSWORD', '')

        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)

        if backup_file.suffix == '.gz':
            temp_file.unlink()

        if result.returncode != 0:
            logger.error(f"Open WebUI pg_restore failed: {result.stderr}")
            return False

        logger.info("[OK] Open WebUI Postgres database restored successfully")
        return True

    except Exception as e:
        logger.exception(f"Open WebUI restore failed: {e}")
        return False
```
Add a unit test for it to `tests/test_owui_pg_backup.py`:
```python
def test_restore_open_webui_postgres_invokes_pg_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("OWUI_DB_PASSWORD", "owui-secret")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["pgpassword"] = kwargs.get("env", {}).get("PGPASSWORD")
        return MagicMock(returncode=0, stderr="")

    dump = tmp_path / "snflwr_owui_postgres_x.sql"
    dump.write_text("dump")
    from scripts.backup_database import restore_open_webui_postgres
    with patch("scripts.backup_database.subprocess.run", side_effect=fake_run):
        ok = restore_open_webui_postgres(dump)

    assert ok is True
    assert captured["cmd"][0] == "pg_restore"
    assert "openwebui" in captured["cmd"]
    assert captured["pgpassword"] == "owui-secret"
```

- [ ] **Step 8: Run full backup test suite + format/lint check**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_owui_pg_backup.py tests/test_offhost_backup.py tests/test_dr_restore_end_to_end.py -q --no-cov -p no:cacheprovider --override-ini="addopts=" && .venv/bin/black --check scripts/backup_database.py
```
Expected: all pass; black reports unchanged (the CI Code Quality job does not lint `scripts/`, but keep it clean anyway).

- [ ] **Step 9: Commit**

```bash
cd ~/Repos/snflwr.ai && git add scripts/backup_database.py tests/test_owui_pg_backup.py
git commit -m "feat(backup): dump+restore the openwebui Postgres database (opt-in, fail-closed)"
```

---

## Task 5: SQLite → Postgres migration script

One-shot operator script for existing installs. Alembic-first (boot OWUI once against the empty `openwebui` DB so Alembic builds the exact schema) then pgloader copies rows data-only, excluding `alembic_version`, resetting sequences. Idempotent preconditions; never mutates the source SQLite. Verified by a documented dockerized smoke test (full E2E needs Docker, so it is not a CI unit test) plus `shellcheck`.

**Files:**
- Create: `scripts/migrate_owui_to_postgres.sh`

**Interfaces:**
- Consumes: running `snflwr-db` (Postgres) + `snflwr-frontend` (OWUI) containers; env `OWUI_DB_PASSWORD`, `OWUI_CONTAINER` (default `snflwr-frontend`), `POSTGRES_CONTAINER` (default `snflwr-db`), `OWUI_IMAGE` (default `ghcr.io/open-webui/open-webui:v0.9.6`).
- Produces: a populated `openwebui` database. After it runs, operator redeploys with Task 3's `DATABASE_URL`.

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_owui_to_postgres.sh`:
```bash
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
log "ensuring openwebui role + database exist"
psql_super --set=owui_pw="$OWUI_DB_PASSWORD" <<-'EOSQL'
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='openwebui') THEN
            CREATE ROLE openwebui LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owui_pw';
        END IF;
    END $$;
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
log "stopping $OWUI_CONTAINER and copying webui.db out"
docker stop "$OWUI_CONTAINER" >/dev/null
docker cp "$OWUI_CONTAINER:/app/backend/data/webui.db" "$WORKDIR/webui.db"
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
 EXCLUDING TABLE NAMES MATCHING ~/^alembic_version$/
  SET work_mem to '64MB', maintenance_work_mem to '256MB';
EOF
docker run --rm --network "$PG_NET" \
    -v "$WORKDIR/webui.db:/data/webui.db:ro" \
    -v "$WORKDIR/owui.load:/data/owui.load:ro" \
    dimitri/pgloader:latest pgloader /data/owui.load

# 5. Verify a couple of row counts match.
SRC_USERS="$(docker run --rm -v "$WORKDIR/webui.db:/data/webui.db:ro" dimitri/pgloader:latest \
    sqlite3 /data/webui.db "SELECT COUNT(*) FROM user" 2>/dev/null || echo '?')"
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
```

- [ ] **Step 2: Lint + syntax-check the script**

Run:
```bash
cd ~/Repos/snflwr.ai && shellcheck scripts/migrate_owui_to_postgres.sh && bash -n scripts/migrate_owui_to_postgres.sh && echo "LINT OK"
```
Expected: `LINT OK` (resolve any shellcheck warnings; if `shellcheck` unavailable, `bash -n` alone must pass).

- [ ] **Step 3: Make executable**

Run:
```bash
cd ~/Repos/snflwr.ai && chmod +x scripts/migrate_owui_to_postgres.sh && git update-index --chmod=+x scripts/migrate_owui_to_postgres.sh 2>/dev/null; ls -l scripts/migrate_owui_to_postgres.sh
```
Expected: mode `-rwxr-xr-x`.

- [ ] **Step 4: Dockerized smoke test (manual, documented)**

This is the end-to-end verification. It is NOT a CI unit test (it needs Docker + image pulls). Run it once and record the result:
```bash
cd ~/Repos/snflwr.ai && bash docs/snippets/owui_migration_smoke.sh   # created in Step 5
```
Expected: the smoke harness seeds a tiny `webui.db` (one OWUI-booted SQLite with a user row), runs the migration against a throwaway Postgres + OWUI, and asserts `user` row counts match. Prints `SMOKE OK`.

- [ ] **Step 5: Write the smoke-test harness**

Create `docs/snippets/owui_migration_smoke.sh`:
```bash
#!/usr/bin/env bash
# Manual end-to-end smoke test for scripts/migrate_owui_to_postgres.sh.
# Spins a throwaway Postgres + a seed OWUI (creates webui.db with schema+a user),
# then runs the migration and asserts row counts. Requires Docker + network.
set -euo pipefail
NET="owui-smoke-net-$$"; PG="owui-smoke-pg-$$"; OWUI="owui-smoke-fe-$$"
IMG="ghcr.io/open-webui/open-webui:v0.9.6"
PW="smokepw123"
cleanup() { docker rm -f "$PG" "$OWUI" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$NET" >/dev/null
docker run -d --rm --name "$PG" --network "$NET" \
  -e POSTGRES_USER=snflwr -e POSTGRES_PASSWORD=sp -e POSTGRES_DB=snflwr_db -e OWUI_DB_PASSWORD="$PW" \
  -v "$PWD/enterprise/init-openwebui.sh:/docker-entrypoint-initdb.d/01-init-openwebui.sh:ro" postgres:16.8-alpine >/dev/null
until docker exec "$PG" pg_isready -U snflwr >/dev/null 2>&1; do sleep 1; done; sleep 3
# Seed OWUI on SQLite so webui.db has the real schema + a user row.
docker run -d --rm --name "$OWUI" --network "$NET" "$IMG" >/dev/null
until docker exec "$OWUI" sh -c 'test -f /app/backend/data/webui.db'; do sleep 2; done; sleep 5
docker exec "$OWUI" python -c "import sqlite3,uuid,time; c=sqlite3.connect('/app/backend/data/webui.db'); c.execute(\"INSERT INTO user (id,name,email,role,created_at,updated_at,last_active_at) VALUES (?,?,?,?,?,?,?)\",(str(uuid.uuid4()),'smoke','s@x.io','admin',int(time.time()),int(time.time()),int(time.time()))); c.commit()" || true
OWUI_CONTAINER="$OWUI" POSTGRES_CONTAINER="$PG" OWUI_IMAGE="$IMG" OWUI_DB_PASSWORD="$PW" POSTGRES_USER=snflwr POSTGRES_DB=snflwr_db \
  bash scripts/migrate_owui_to_postgres.sh
N="$(docker exec "$PG" psql -tAX -U openwebui -d openwebui -c 'SELECT COUNT(*) FROM "user"')"
[ "$N" -ge 1 ] && echo "SMOKE OK (users in postgres: $N)" || { echo "SMOKE FAIL"; exit 1; }
```
Then make it executable: `chmod +x docs/snippets/owui_migration_smoke.sh` and run Step 4. (If the seed `INSERT` column list drifts across OWUI versions, adjust to the columns OWUI's `user` table actually has — the assertion only needs ≥1 row.)

- [ ] **Step 6: Commit**

```bash
cd ~/Repos/snflwr.ai && git add scripts/migrate_owui_to_postgres.sh docs/snippets/owui_migration_smoke.sh
git commit -m "feat(owui): add SQLite->Postgres migration script (alembic-first + pgloader) + smoke harness"
```

---

## Task 6: Env example + operator runbook

Document the new knobs and the operational procedures (fresh install, migration, backup, rollback).

**Files:**
- Modify: `.env.production.example`
- Create: `docs/guides/OWUI_POSTGRES.md`

**Interfaces:**
- Consumes: everything above.
- Produces: operator-facing documentation; no code depends on this.

- [ ] **Step 1: Add the OWUI Postgres knobs to the env example**

In `.env.production.example`, in the `# Database` section, after line 41 (`POSTGRES_SSLMODE=require`), add:
```bash

# Open WebUI database (enterprise tier). OWUI runs on its OWN non-superuser role
# + database in the same Postgres container, isolated from snflwr_db (child PII).
# Generate with: python -c 'import secrets; print(secrets.token_hex(32))'
OWUI_DB_PASSWORD=CHANGE-THIS-use-strong-password
```
And in the `# Open WebUI data backup` block, after line 119 (`OWUI_DATA_PATH=/app/backend/data`), add:
```bash
# Open WebUI relational data lives in the `openwebui` Postgres DB (see
# OWUI_DB_PASSWORD). This dumps it into the same backup/off-host flow. Leave ON
# whenever OWUI uses Postgres; the volume backup above still covers vector_db/ +
# uploads/. Fail-closed when enabled.
OWUI_PG_BACKUP_ENABLED=true
```

- [ ] **Step 2: Write the operator runbook**

Create `docs/guides/OWUI_POSTGRES.md`:
```markdown
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
3. Confirm the printed `user` row counts match.
4. `docker compose -f docker/compose/docker-compose.yml up -d open-webui`
5. Verify login + chat history, then delete the `./webui_migrate_<ts>.db` safety copy.

The script never mutates the source `webui.db`; to roll back, remove `DATABASE_URL`
from the `open-webui` service and restart — OWUI falls back to the SQLite file
still on the volume.

## Backup / DR
- `OWUI_PG_BACKUP_ENABLED=true` makes `scripts/backup_database.py` also `pg_dump`
  the `openwebui` database (`snflwr_owui_postgres_<ts>.sql[.gz]`), joining the
  off-host + retention flow. Restore with `restore_open_webui_postgres()`.
- The OWUI volume backup (`OWUI_BACKUP_ENABLED`) still covers `vector_db/` +
  `uploads/`.

## Rollback of the whole feature
Drop `DATABASE_URL` from `open-webui` and restart. Optionally
`DROP DATABASE openwebui;` once you've confirmed you no longer need it.
```

- [ ] **Step 3: Verify the env example still has no real secrets**

Run:
```bash
cd ~/Repos/snflwr.ai && grep -n "OWUI_DB_PASSWORD\|OWUI_PG_BACKUP_ENABLED" .env.production.example
```
Expected: `OWUI_DB_PASSWORD=CHANGE-THIS-...` and `OWUI_PG_BACKUP_ENABLED=true` present; no real values.

- [ ] **Step 4: Commit**

```bash
cd ~/Repos/snflwr.ai && git add .env.production.example docs/guides/OWUI_POSTGRES.md
git commit -m "docs(owui): document OWUI Postgres config, migration, backup, rollback"
```

---

## Final Verification (after all tasks)

- [ ] **Run the touched Python tests + coverage gate**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_owui_pg_backup.py tests/test_offhost_backup.py tests/test_dr_restore_end_to_end.py tests/test_dr_restore_postgres.py -q -p no:cacheprovider 2>&1 | tail -5
```
Expected: all pass (these exercise `scripts/backup_database.py`, which is under the coverage ratchet).

- [ ] **Validate both compose files**

Run:
```bash
cd ~/Repos/snflwr.ai && OWUI_DB_PASSWORD=x POSTGRES_PASSWORD=x REDIS_PASSWORD=x CHAT_MODEL=qwen3.5:9b WEBUI_SECRET_KEY=x JWT_SECRET_KEY=x INTERNAL_API_KEY=x \
  docker compose -f docker/compose/docker-compose.yml config -q && docker compose -f docker/compose/docker-compose.home.yml config -q && echo "COMPOSE OK"
```
Expected: `COMPOSE OK`.

- [ ] **shellcheck the new shell artifacts**

Run:
```bash
cd ~/Repos/snflwr.ai && shellcheck enterprise/init-openwebui.sh scripts/migrate_owui_to_postgres.sh docs/snippets/owui_migration_smoke.sh && echo "SHELLCHECK OK"
```
Expected: `SHELLCHECK OK`.

- [ ] **Push and open PR**

```bash
cd ~/Repos/snflwr.ai && git push -u origin feat/owui-postgres
gh pr create --title "Enterprise Open WebUI on PostgreSQL" --body "Implements docs/superpowers/specs/2026-06-24-owui-postgres-design.md. Enterprise-only; dedicated non-superuser openwebui role+DB; alembic-first + pgloader migration; DR backup coverage; OWUI pin v0.8.12->v0.9.6."
```
Then confirm CI is green before requesting review/merge.
