# Disaster Recovery Runbook

This document is the operator-facing companion to `tests/test_dr_restore_end_to_end.py`. The test enforces that the engineering surface works; this runbook tells you how to use it under stress.

For incident triage and severity classification, see `docs/deployment/INCIDENT_RESPONSE_RUNBOOK.md` first. This runbook covers the narrower scope of *getting data back* once you have already decided you need to.

## RPO / RTO targets

These are operator commitments per deployment tier. The DR test (`tests/test_dr_restore_end_to_end.py`) keeps the engineering path honest; meeting these numbers in practice depends on you running backups on the cadence below.

| Tier | Database | RPO (max data loss) | RTO (max downtime) | Backup cadence |
|---|---|---|---|---|
| **Family / USB** | SQLite + SQLCipher, on the USB stick | 4 hours | 1 hour | Manual or one-shot — parent runs backup before unplugging |
| **Home Server** | SQLite + SQLCipher in docker volume | 1 hour | 30 minutes | Systemd timer or cron every hour |
| **Enterprise** | PostgreSQL | 15 minutes | 2 hours | `pg_dump` every 15 min via cron; WAL archiving on for point-in-time recovery |

These numbers reflect "tutoring transcripts and learning analytics, not financial data" — they would be too loose for billing systems and are too tight for archive-grade compliance storage. Tighten the Enterprise tier in writing before you sign with a customer whose policy demands lower RPO.

## Backup

Wired by the `scripts/backup_database.py backup` CLI. The script:

- Routes to `backup_sqlite()` or `backup_postgresql()` based on `system_config.DB_TYPE`.
- Writes to `$BACKUP_PATH` (defaults to `$APP_DATA_DIR/backups`).
- Names files `snflwr_<engine>_<UTC-timestamp>.{db,sql}[.gz]` so they sort chronologically.
- Compresses by default (`COMPRESS_BACKUPS=true`); writes a `.json` sidecar with size, type, timestamp.
- Retains for `BACKUP_RETENTION_DAYS` (default 30) and prunes older files.

**SQLite backups copy the encrypted file as-is.** The encryption key is *not* in the backup. Restoring a SQLite backup on a host that does not have the same `DB_ENCRYPTION_KEY` (or operator passphrase + `encryption.meta.json`) will produce a file the app cannot open. See `ENCRYPTION_KEY_RECOVERY.md` before you find this out at 3am.

### Schedule it

The script is a one-shot — wire it to a scheduler.

**Home Server (systemd timer)**

```ini
# /etc/systemd/system/snflwr-backup.service
[Unit]
Description=snflwr.ai database backup
After=docker.service

[Service]
Type=oneshot
User=snflwr
WorkingDirectory=/opt/snflwr
ExecStart=/opt/snflwr/.venv/bin/python scripts/backup_database.py backup
```

```ini
# /etc/systemd/system/snflwr-backup.timer
[Unit]
Description=hourly snflwr.ai backup

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl enable --now snflwr-backup.timer`

**Enterprise (cron)**

```
*/15 * * * * /opt/snflwr/.venv/bin/python /opt/snflwr/scripts/backup_database.py backup >> /var/log/snflwr-backup.log 2>&1
```

### Off-host destination

Local backups protect against `rm -rf` and SQLite corruption. They do **not** protect against host failure. A backup nobody has copied off-box is a backup that didn't happen.

**This is now built into the backup script** via rclone. When enabled, every successful local backup (and its `.json` metadata) is pushed to the configured remote, and the backup run **fails closed** — a non-zero exit — if the off-host copy cannot be made, so your scheduler/monitoring alarms instead of silently keeping a local-only copy.

rclone ships inside the Docker image; on bare metal `install.py` offers to install it (or `curl https://rclone.org/install.sh | sudo bash`).

```bash
# 1. Configure a remote once (interactive). Backblaze B2 is cheap and not AWS.
rclone config            # creates ~/.config/rclone/rclone.conf

# 2. Enable off-host in .env:
#    OFFHOST_BACKUP_ENABLED=true
#    RCLONE_REMOTE=b2:snflwr-backups-prod
#    OFFHOST_RETENTION_DAYS=30        # optional; defaults to BACKUP_RETENTION_DAYS
#    RCLONE_CONFIG=/etc/snflwr/rclone.conf   # optional; non-default config path

# 3. The scheduled `backup_database.py backup` run now copies off-host
#    automatically and prunes remote backups older than the retention window.
```

`RCLONE_REMOTE` must be of the form `remote:path` (validated before rclone is invoked). Remote retention is enforced with `rclone delete <remote> --min-age <N>d` after each successful upload; a prune failure is logged but does not fail the backup (mirroring local cleanup).

If you prefer rsync to a host on a separate network segment instead, disable the built-in path (`OFFHOST_BACKUP_ENABLED=false`) and wire your own post-backup step:

```bash
rsync -avz --include='*.gz' --include='*.json' --exclude='*' \
    $BACKUP_PATH/ snflwr-backup@backup.host:/backups/snflwr/
```

### Heartbeat (don't trust a backup nobody is watching)

A scheduled backup that silently stops running looks identical to one that's succeeding — until you need a restore and there's nothing there. Set `BACKUP_HEARTBEAT_URL` to a [healthchecks.io](https://healthchecks.io)-style check. Each successful run (after the off-host copy) pings the URL; each failure pings `<URL>/fail`. Configure the check's period to your backup schedule + grace, and it alarms if a ping is ever missed — catching a dead cron timer, a full disk, or a broken off-host destination.

## Restore — host lost? Pull from off-host first

If the machine itself is gone, the local `$BACKUP_PATH` is gone with it. Pull the backup back down from the remote before restoring:

```bash
# List what's off-host (newest first), then pull the one you want.
rclone lsf "$RCLONE_REMOTE" --include "*.gz" | sort | tail -5
python scripts/backup_database.py pull --file snflwr_sqlite_<TIMESTAMP>.db.gz
# -> lands in $BACKUP_PATH; now restore from it as below.
```

`tests/test_offhost_backup.py::TestPullOffhost::test_pull_then_restore_recovers_data` exercises this exact pull→restore path end-to-end, so it's a proven recovery route, not just documentation.

## Restore — SQLite

```bash
# 1. Stop the snflwr-api container so the DB file is quiescent.
docker compose -f docker/compose/docker-compose.home.yml stop snflwr-api

# 2. List backups, newest first.
ls -lt $BACKUP_PATH/snflwr_sqlite_*.db.gz | head -5

# 3. Restore. The script saves the current DB as `.db.pre-restore` first.
python scripts/backup_database.py restore --file $BACKUP_PATH/snflwr_sqlite_<TIMESTAMP>.db.gz

# 4. Start snflwr-api and watch the logs.
docker compose -f docker/compose/docker-compose.home.yml up -d snflwr-api
docker compose -f docker/compose/docker-compose.home.yml logs -f snflwr-api

# 5. Verify the app is serving requests against the restored DB.
curl -fsS http://localhost:39150/health
```

The `.db.pre-restore` safety copy is your one chance to back out if you restored the wrong file. **Do not delete it for at least 24 hours.**

If the restore succeeds but the app cannot read the DB on startup, the most likely cause is encryption key mismatch — see `ENCRYPTION_KEY_RECOVERY.md`.

## Restore — PostgreSQL

```bash
docker compose -f docker/compose/docker-compose.enterprise.yml stop snflwr-api
python scripts/backup_database.py restore --file $BACKUP_PATH/snflwr_postgres_<TIMESTAMP>.sql.gz
docker compose -f docker/compose/docker-compose.enterprise.yml up -d snflwr-api
curl -fsS http://localhost:39150/health
```

`pg_restore --clean --if-exists` drops tables before recreating them. There is no `.pre-restore` safety copy on the Postgres side — take a `pg_dump` of the current state *before* running restore if you are not 100% sure of the source backup.

## After restore

1. Spot-check at least one child profile: did encrypted columns (name, email) decrypt cleanly?
2. Tail `audit_log` for the last hour to confirm the app is writing.
3. Decide whether to publish a status update (per the incident runbook's communication ladder).
4. Schedule the next backup explicitly if the timer was disabled during recovery.

## What the engineering test guarantees vs. what it doesn't

`tests/test_dr_restore_end_to_end.py` runs on every PR and proves:

- The backup CLI writes a real gzip-compressed SQLite file.
- A corrupted live DB can be restored from that backup.
- The schema and seeded data survive the round-trip byte-identical.
- Post-restore writes succeed (no read-only file mode regressions).
- A `.pre-restore` safety copy of the live DB is saved before overwrite.

It does **not** prove:

- Encryption key recovery works (separate document, separate concern).
- An off-host backup destination is reachable (depends on your rclone / rsync config).
- Postgres restore works (needs a live container; runs nightly, not on every PR).
- The full app can serve user traffic against the restored DB (the test imports the schema; it does not run the FastAPI lifespan).

Schedule a quarterly *live* drill where you restore last week's backup into a staging environment, point a TestClient at it, and run the smoke suite. The engineering test catches regressions; the live drill catches everything the test can't simulate (disk-full conditions, off-host copy lag, key-vault outages, the cron timer never having fired).
