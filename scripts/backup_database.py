#!/usr/bin/env python3
"""
Database Backup Script for snflwr.ai
Supports both SQLite and PostgreSQL databases with automatic rotation
"""

import sys
import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import shutil
import subprocess
import gzip
import json
import urllib.request

# A valid rclone remote target looks like `remote:path/segment`, e.g.
# `b2:snflwr-backups-prod`. We pass it as an argv element (never through a
# shell) but still validate the shape to catch operator typos and reject
# anything with whitespace/shell metacharacters before invoking rclone.
_RCLONE_REMOTE_RE = re.compile(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_./-]*$")

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseBackup:
    """Handle database backups with rotation"""

    def __init__(self):
        # Backup configuration from environment or defaults
        self.backup_enabled = os.getenv("BACKUP_ENABLED", "true").lower() == "true"
        self.backup_path = Path(
            os.getenv("BACKUP_PATH", system_config.APP_DATA_DIR / "backups")
        )
        self.backup_retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
        self.compress_backups = os.getenv("COMPRESS_BACKUPS", "true").lower() == "true"

        # Off-host (remote) backup configuration. Local backups protect
        # against rm -rf / corruption but not host failure; when enabled, the
        # backup is pushed to an rclone remote and the run fails closed if the
        # copy cannot be made (see DR_RUNBOOK.md "Off-host destination").
        self.offhost_enabled = (
            os.getenv("OFFHOST_BACKUP_ENABLED", "false").lower() == "true"
        )
        self.rclone_remote = os.getenv("RCLONE_REMOTE", "").strip()
        self.rclone_config = os.getenv("RCLONE_CONFIG", "").strip()
        offhost_retention = os.getenv("OFFHOST_RETENTION_DAYS", "").strip()
        self.offhost_retention_days = (
            int(offhost_retention) if offhost_retention else self.backup_retention_days
        )

        # Open WebUI data backup. snflwr's own DB backup does NOT cover Open
        # WebUI's data volume (open-webui-data:/app/backend/data), which holds
        # webui.db (all parent/child accounts, chat history, the seeded proxy
        # auth key), vector_db/ and uploads/. Losing that volume loses all chat
        # history even when the snflwr DB backup "succeeded". We pull it out of
        # the running container with `docker cp` (same approach as
        # scripts/guarded_upgrade.sh) and fold it into the same off-host +
        # retention flow.
        # Opt-in (like OFFHOST_BACKUP_ENABLED): enable in OWUI deployments via
        # the env/compose. When on, it is fail-closed — an OWUI backup failure
        # marks the run unsuccessful so monitoring alarms rather than silently
        # leaving chat history/accounts unprotected.
        self.owui_backup_enabled = (
            os.getenv("OWUI_BACKUP_ENABLED", "false").lower() == "true"
        )
        self.owui_container = os.getenv("OWUI_CONTAINER", "snflwr-frontend").strip()
        self.owui_data_path = os.getenv("OWUI_DATA_PATH", "/app/backend/data").strip()

        # Open WebUI relational data on Postgres (enterprise tier). When OWUI is
        # moved off SQLite onto the dedicated `openwebui` database, that data is
        # NOT in snflwr_db and the volume backup only covers vector_db/ + uploads/.
        # This dumps the openwebui DB so relational data stays in DR. Opt-in,
        # fail-closed (same contract as OWUI_BACKUP_ENABLED).
        self.owui_pg_backup_enabled = (
            os.getenv("OWUI_PG_BACKUP_ENABLED", "false").lower() == "true"
        )
        self.owui_db_name = os.getenv("OWUI_DB_NAME", "openwebui").strip()
        self.owui_db_user = os.getenv("OWUI_DB_USER", "openwebui").strip()
        self.owui_db_password = os.getenv("OWUI_DB_PASSWORD", "")

        # Heartbeat / dead-man's-switch. A scheduled backup that silently stops
        # running is a backup that didn't happen. When set, a successful run
        # pings this URL and a failed run pings <URL>/fail, so an external
        # monitor (e.g. healthchecks.io) alarms if no success arrives in time.
        self.heartbeat_url = os.getenv("BACKUP_HEARTBEAT_URL", "").strip()

        # Create backup directory
        self.backup_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Backup configuration:")
        logger.info(f"  Enabled: {self.backup_enabled}")
        logger.info(f"  Path: {self.backup_path}")
        logger.info(f"  Retention: {self.backup_retention_days} days")
        logger.info(f"  Compression: {self.compress_backups}")
        logger.info(f"  Off-host enabled: {self.offhost_enabled}")
        if self.offhost_enabled:
            logger.info(f"  Off-host remote: {self.rclone_remote or '(unset!)'}")
            logger.info(f"  Off-host retention: {self.offhost_retention_days} days")
        logger.info(f"  Open WebUI backup: {self.owui_backup_enabled}")
        if self.owui_backup_enabled:
            logger.info(
                f"  Open WebUI container: {self.owui_container}:{self.owui_data_path}"
            )
        logger.info(f"  Open WebUI Postgres backup: {self.owui_pg_backup_enabled}")

    def backup_sqlite(self) -> tuple[bool, str]:
        """Backup SQLite database"""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_path / f"snflwr_sqlite_{timestamp}.db"

            logger.info(f"Creating SQLite backup: {backup_file}")

            # Use sqlite3 command-line tool for online backup if available
            db_path = system_config.DB_PATH

            if not db_path.exists():
                logger.error(f"Database file not found: {db_path}")
                return False, "Database file not found"

            # Copy the database file
            shutil.copy2(db_path, backup_file)

            # Compress if enabled
            if self.compress_backups:
                compressed_file = Path(str(backup_file) + ".gz")
                with open(backup_file, "rb") as f_in:
                    with gzip.open(compressed_file, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

                # Remove uncompressed backup
                backup_file.unlink()
                backup_file = compressed_file
                logger.info(f"Compressed backup created: {compressed_file}")

            # Get backup size
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            logger.info(
                f"[OK] SQLite backup completed: {backup_file} ({size_mb:.2f} MB)"
            )

            return True, str(backup_file)

        except Exception as e:
            logger.exception(f"SQLite backup failed: {e}")
            return False, str(e)

    @staticmethod
    def _validate_postgres_param(param: str, param_name: str) -> str:
        """
        Validate PostgreSQL connection parameters to prevent command injection

        Args:
            param: Parameter value to validate
            param_name: Name of parameter (for error messages)

        Returns:
            The validated parameter

        Raises:
            ValueError: If parameter contains invalid characters
        """
        import re

        # Only allow alphanumeric, dots, hyphens, underscores
        if not re.match(r"^[a-zA-Z0-9._-]+$", param):
            raise ValueError(
                f"Invalid {param_name}: contains disallowed characters. "
                f"Only alphanumeric, dots, hyphens, and underscores are allowed."
            )
        return param

    def backup_postgresql(
        self,
        db_name: str = None,
        user: str = None,
        password: str = None,
        label: str = "postgres",
    ) -> tuple[bool, str]:
        """Backup a PostgreSQL database using pg_dump.

        Defaults to the primary snflwr database; pass db_name/user/password/label
        to back up another database in the same cluster (e.g. Open WebUI's
        'openwebui'). `label` sets the artifact prefix: snflwr_<label>_<ts>.sql.
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_path / f"snflwr_{label}_{timestamp}.sql"

            # Validate all PostgreSQL parameters to prevent command injection
            host = self._validate_postgres_param(
                system_config.POSTGRES_HOST, "POSTGRES_HOST"
            )
            user = self._validate_postgres_param(
                user or system_config.POSTGRES_USER, "POSTGRES_USER"
            )
            database = self._validate_postgres_param(
                db_name or system_config.POSTGRES_DB, "POSTGRES_DB"
            )

            logger.info(f"Creating PostgreSQL backup of '{database}': {backup_file}")

            # Build pg_dump command with validated parameters
            cmd = [
                "pg_dump",
                "-h",
                host,
                "-p",
                str(system_config.POSTGRES_PORT),  # Port is int, already safe
                "-U",
                user,
                "-d",
                database,
                "-F",
                "c",  # Custom format (compressed)
                "-f",
                str(backup_file),
            ]

            # Set password environment variable
            env = os.environ.copy()
            env["PGPASSWORD"] = (
                password if password is not None else system_config.POSTGRES_PASSWORD
            )

            # Run pg_dump
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.error(f"pg_dump failed: {result.stderr}")
                return False, result.stderr

            # Additional gzip compression if enabled
            if self.compress_backups:
                compressed_file = Path(str(backup_file) + ".gz")
                with open(backup_file, "rb") as f_in:
                    with gzip.open(compressed_file, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

                backup_file.unlink()
                backup_file = compressed_file
                logger.info(f"Additional compression applied: {compressed_file}")

            # Get backup size
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            logger.info(
                f"[OK] PostgreSQL backup completed: {backup_file} ({size_mb:.2f} MB)"
            )

            return True, str(backup_file)

        except subprocess.TimeoutExpired:
            logger.error("PostgreSQL backup timed out after 5 minutes")
            return False, "Backup timeout"
        except FileNotFoundError:
            logger.error("pg_dump command not found. Install PostgreSQL client tools.")
            return False, "pg_dump not found"
        except Exception as e:
            logger.exception(f"PostgreSQL backup failed: {e}")
            return False, str(e)

    def backup_open_webui(self) -> tuple[bool, str]:
        """Back up the Open WebUI data volume from the running container.

        Open WebUI holds all parent/child accounts, chat history, the embeddings
        vector DB and the seeded proxy auth key in its data dir — none of which
        the snflwr DB backup covers. We copy the whole data dir out of the
        container with `docker cp` (the proven approach in guarded_upgrade.sh)
        and archive it as snflwr_owui_<ts>.tar.gz so it joins the same off-host
        and retention flow. Returns (ok, archive_path_or_reason).
        """
        if shutil.which("docker") is None:
            return False, (
                "docker not found on PATH; cannot reach the Open WebUI container "
                "(set OWUI_BACKUP_ENABLED=false if backups run without docker access)"
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive = self.backup_path / f"snflwr_owui_{timestamp}.tar.gz"
        tmp_dir = self.backup_path / f".owui_tmp_{timestamp}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)

            # `docker cp <container>:<dir> <dest>` lands the directory under
            # <dest>/<basename>. Run without a shell; the container name and
            # path come from our own config, never untrusted input.
            src = f"{self.owui_container}:{self.owui_data_path}"
            logger.info(f"Backing up Open WebUI data: {src}")
            result = subprocess.run(
                ["docker", "cp", src, str(tmp_dir)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                msg = (
                    result.stderr.strip()
                    or f"docker cp failed (exit {result.returncode})"
                )
                logger.error(f"Open WebUI backup failed: {msg}")
                return False, msg

            copied = tmp_dir / Path(self.owui_data_path).name
            if not copied.exists():
                return False, f"copied data dir not found at {copied}"

            import tarfile

            with tarfile.open(archive, "w:gz") as tar:
                tar.add(copied, arcname="open-webui-data")

            size_mb = archive.stat().st_size / (1024 * 1024)
            logger.info(
                f"[OK] Open WebUI backup completed: {archive} ({size_mb:.2f} MB)"
            )
            return True, str(archive)

        except subprocess.TimeoutExpired:
            logger.error("Open WebUI backup timed out (docker cp)")
            return False, "docker cp timed out"
        except Exception as e:
            logger.exception(f"Open WebUI backup failed: {e}")
            return False, str(e)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _rclone_cmd(self, *args: str) -> list:
        """Build an rclone argv list, injecting --config when configured.

        Returned as a list and run without a shell, so remote/path values are
        never interpreted by a shell."""
        cmd = ["rclone", *args]
        if self.rclone_config:
            cmd += ["--config", self.rclone_config]
        return cmd

    def _offhost_preflight(self) -> tuple[bool, str]:
        """Validate that an off-host copy can be attempted. Returns
        (ok, message); ok=False means fail closed without invoking rclone."""
        if not self.rclone_remote:
            return False, "OFFHOST_BACKUP_ENABLED is set but RCLONE_REMOTE is empty"
        if not _RCLONE_REMOTE_RE.match(self.rclone_remote):
            return False, (
                f"RCLONE_REMOTE is malformed: {self.rclone_remote!r} "
                "(expected form 'remote:path', e.g. 'b2:snflwr-backups-prod')"
            )
        if shutil.which("rclone") is None:
            return False, (
                "rclone binary not found on PATH; install rclone or disable "
                "OFFHOST_BACKUP_ENABLED"
            )
        return True, ""

    def upload_offhost(self, *files: str) -> tuple[bool, str]:
        """Copy one or more backup artifacts to the configured rclone remote.

        Fail-closed: any preflight failure or non-zero rclone exit returns
        (False, reason). A backup nobody has copied off-box is a backup that
        didn't happen."""
        ok, reason = self._offhost_preflight()
        if not ok:
            logger.error(f"Off-host upload aborted: {reason}")
            return False, reason

        for f in files:
            if not f or not Path(f).exists():
                continue
            cmd = self._rclone_cmd("copy", f, self.rclone_remote)
            logger.info(f"Off-host copy: {f} -> {self.rclone_remote}")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600
                )
            except subprocess.TimeoutExpired:
                msg = f"rclone copy timed out for {f}"
                logger.error(msg)
                return False, msg
            except FileNotFoundError:
                msg = "rclone binary not found when invoking copy"
                logger.error(msg)
                return False, msg
            if result.returncode != 0:
                msg = (
                    result.stderr.strip()
                    or f"rclone copy failed (exit {result.returncode})"
                )
                logger.error(f"Off-host copy failed for {f}: {msg}")
                return False, msg

        logger.info(f"[OK] Off-host copy completed to {self.rclone_remote}")
        return True, "uploaded"

    def prune_offhost(self) -> tuple[bool, str]:
        """Delete remote backups older than the off-host retention window.

        Best-effort: a prune failure is logged but does not fail the backup,
        mirroring local cleanup_old_backups()."""
        ok, reason = self._offhost_preflight()
        if not ok:
            logger.warning(f"Off-host prune skipped: {reason}")
            return False, reason

        min_age = f"{self.offhost_retention_days}d"
        cmd = self._rclone_cmd("delete", self.rclone_remote, "--min-age", min_age)
        logger.info(
            f"Off-host prune: deleting on {self.rclone_remote} older than {min_age}"
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Off-host prune error (ignored): {e}")
            return False, str(e)
        if result.returncode != 0:
            msg = (
                result.stderr.strip()
                or f"rclone delete failed (exit {result.returncode})"
            )
            logger.warning(f"Off-host prune failed (ignored): {msg}")
            return False, msg
        return True, "pruned"

    def pull_offhost(self, filename: str) -> tuple[bool, str]:
        """Pull a single named backup artifact FROM the remote into BACKUP_PATH.

        This is the disaster path: when the host is lost, local backups are
        gone, so a restore must fetch from off-host first. Returns
        (ok, local_path_or_reason)."""
        ok, reason = self._offhost_preflight()
        if not ok:
            logger.error(f"Off-host pull aborted: {reason}")
            return False, reason

        # rclone copy treats the source as a file when it has no trailing
        # slash; it lands in the destination directory under the same name.
        remote_src = f"{self.rclone_remote}/{filename}"
        cmd = self._rclone_cmd("copy", remote_src, str(self.backup_path))
        logger.info(f"Off-host pull: {remote_src} -> {self.backup_path}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"Off-host pull error: {e}")
            return False, str(e)
        if result.returncode != 0:
            msg = (
                result.stderr.strip()
                or f"rclone copy failed (exit {result.returncode})"
            )
            logger.error(f"Off-host pull failed: {msg}")
            return False, msg

        local_path = str(self.backup_path / filename)
        logger.info(f"[OK] Off-host pull completed: {local_path}")
        return True, local_path

    def _ping_heartbeat(self, success: bool) -> None:
        """Best-effort heartbeat ping. Never raises — a monitoring outage must
        not affect the backup result."""
        if not self.heartbeat_url:
            return
        if not self.heartbeat_url.lower().startswith(("http://", "https://")):
            logger.warning(
                f"BACKUP_HEARTBEAT_URL is not http(s); ignoring: {self.heartbeat_url!r}"
            )
            return

        url = self.heartbeat_url if success else f"{self.heartbeat_url}/fail"
        try:
            with urllib.request.urlopen(
                url, timeout=10
            ) as resp:  # noqa: S310 (scheme validated above)
                resp.read()
            logger.info(f"Heartbeat pinged ({'success' if success else 'fail'})")
        except Exception as e:  # noqa: BLE001 — heartbeat is best-effort
            logger.warning(f"Heartbeat ping failed (ignored): {e}")

    def cleanup_old_backups(self):
        """Remove backups older than retention period"""
        logger.info(f"Cleaning up backups older than {self.backup_retention_days} days")

        cutoff_date = datetime.now(timezone.utc) - timedelta(
            days=self.backup_retention_days
        )
        removed_count = 0
        removed_size = 0

        for backup_file in self.backup_path.glob("snflwr_*"):
            if backup_file.is_file():
                file_time = datetime.fromtimestamp(
                    backup_file.stat().st_mtime, tz=timezone.utc
                )

                if file_time < cutoff_date:
                    size = backup_file.stat().st_size
                    logger.info(f"Removing old backup: {backup_file.name}")
                    backup_file.unlink()
                    removed_count += 1
                    removed_size += size

        if removed_count > 0:
            size_mb = removed_size / (1024 * 1024)
            logger.info(
                f"[OK] Removed {removed_count} old backups ({size_mb:.2f} MB freed)"
            )
        else:
            logger.info("No old backups to remove")

    def list_backups(self):
        """List all available backups"""
        logger.info("Available backups:")

        backups = sorted(self.backup_path.glob("snflwr_*"), reverse=True)

        if not backups:
            logger.info("  No backups found")
            return

        for backup_file in backups:
            size_mb = backup_file.stat().st_size / (1024 * 1024)
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
            logger.info(
                f"  {backup_file.name} ({size_mb:.2f} MB) - {mtime.strftime('%Y-%m-%d %H:%M:%S')}"
            )

    def create_backup_metadata(self, backup_file: str, success: bool):
        """Create metadata file for backup"""
        metadata = {
            "backup_file": backup_file,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database_type": system_config.DATABASE_TYPE,
            "success": success,
            "retention_days": self.backup_retention_days,
            "compressed": self.compress_backups,
        }

        metadata_file = Path(backup_file).with_suffix(".json")
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Metadata saved: {metadata_file}")

    def _alert_operator_failure(self, detail: str) -> None:
        """Best-effort operator alert when a backup fails or fails verification —
        so a broken backup is NEVER silent, even without BACKUP_HEARTBEAT_URL set
        (the heartbeat is opt-in; this is the always-on fallback)."""
        try:
            from core.email_service import email_service

            email_service.send_operator_alert(
                subject="snflwr.ai database backup FAILED",
                description=(
                    f"A scheduled database backup failed: {detail}. The last "
                    "successful off-host backup may now be stale — investigate "
                    "immediately, child data may be unprotected against loss."
                ),
            )
        except Exception as exc:  # never let alerting raise from a backup run
            logger.error(
                "Operator alert for backup failure failed (non-fatal): %s", exc
            )

    def verify_backup(self, backup_file=None) -> tuple[bool, str]:
        """Integrity-check a backup by opening it and running PRAGMA
        integrity_check + confirming it has tables. Closes the "nobody ever
        test-restores the backups" gap: catches a corrupt/empty/half-written
        backup (and, via the encryption-aware adapter, a key/format mismatch)
        before a real disaster needs it. Non-destructive — restores to a temp
        file. Returns (ok, detail)."""
        import tempfile

        if backup_file is None:
            cands = sorted(self.backup_path.glob("snflwr_sqlite_*.db*"), reverse=True)
            if not cands:
                return False, "no SQLite backups found to verify"
            backup_file = cands[0]
        backup_file = Path(backup_file)
        if not backup_file.exists():
            return False, f"backup not found: {backup_file}"

        tmp_dir = Path(tempfile.mkdtemp(prefix="snflwr_verify_"))
        tmp_db = tmp_dir / "verify.db"
        try:
            if backup_file.suffix == ".gz":
                with gzip.open(backup_file, "rb") as f_in, open(tmp_db, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                shutil.copy2(backup_file, tmp_db)

            # Open through the encryption-aware adapter so an encrypted (SQLCipher)
            # backup is decrypted with the configured key; a key/format mismatch
            # surfaces here as a clear failure rather than at restore time.
            from storage.db_adapters import create_adapter

            adapter = create_adapter("sqlite", db_path=tmp_db)
            conn = adapter.connect()
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            integ = (
                row["integrity_check"]
                if isinstance(row, dict)
                else (row[0] if row else None)
            )
            cur.execute("SELECT count(*) AS n FROM sqlite_master WHERE type='table'")
            row = cur.fetchone()
            ntables = row["n"] if isinstance(row, dict) else (row[0] if row else 0)
            adapter.close()

            if integ != "ok":
                return False, f"integrity_check failed: {integ!r}"
            if not ntables or ntables < 1:
                return False, "backup opened but has no tables"
            return (
                True,
                f"OK — integrity_check passed, {ntables} tables readable ({backup_file.name})",
            )
        except Exception as exc:
            return False, f"could not open/verify backup: {exc}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def run_backup(self) -> bool:
        """Execute backup based on database type"""
        if not self.backup_enabled:
            logger.warning("Backups are disabled")
            return False

        logger.info("=" * 60)
        logger.info("Starting Database Backup")
        logger.info("=" * 60)

        # Determine database type
        db_type = system_config.DATABASE_TYPE.lower()

        if db_type == "sqlite":
            success, result = self.backup_sqlite()
        elif db_type == "postgresql":
            success, result = self.backup_postgresql()
        else:
            logger.error(f"Unsupported database type: {db_type}")
            return False

        # Create metadata
        if success:
            self.create_backup_metadata(result, success)

        # Open WebUI data backup (additive — snflwr's DB backup does not cover
        # the OWUI volume). Run after the primary DB backup; an OWUI failure
        # marks the overall run unsuccessful (so monitoring alarms) but must not
        # prevent the DB backup itself from going off-host.
        owui_artifact = None
        owui_failed = False
        if success and self.owui_backup_enabled:
            owui_ok, owui_result = self.backup_open_webui()
            if owui_ok:
                owui_artifact = owui_result
            else:
                logger.error(f"[FAIL] Open WebUI backup failed: {owui_result}")
                owui_failed = True

        # Open WebUI relational data on Postgres (additive — the snflwr DB dump
        # covers snflwr_db only; the OWUI volume backup covers vector_db/uploads
        # but not the relational data once OWUI is on Postgres).
        owui_pg_artifact = None
        if success and self.owui_pg_backup_enabled:
            pg_ok, pg_result = self.backup_postgresql(
                db_name=self.owui_db_name,
                user=self.owui_db_user,
                password=self.owui_db_password,
                label="owui_postgres",
            )
            if pg_ok:
                owui_pg_artifact = pg_result
            else:
                logger.error(f"[FAIL] Open WebUI Postgres backup failed: {pg_result}")
                owui_failed = True

        # Push off-host. Fail-closed: a local-only backup is not a success
        # when off-host is enabled.
        if success and self.offhost_enabled:
            metadata_file = str(Path(result).with_suffix(".json"))
            artifacts = [result, metadata_file]
            if owui_artifact:
                artifacts.append(owui_artifact)
            if owui_pg_artifact:
                artifacts.append(owui_pg_artifact)
            up_ok, up_msg = self.upload_offhost(*artifacts)
            if not up_ok:
                logger.error(f"[FAIL] Off-host copy failed: {up_msg}")
                success = False
            else:
                self.prune_offhost()

        # An Open WebUI backup failure marks the run unsuccessful (heartbeat +
        # exit code) AFTER the DB backup and its off-host push have run.
        if owui_failed:
            success = False

        # Cleanup old backups
        self.cleanup_old_backups()

        # List available backups
        self.list_backups()

        logger.info("=" * 60)
        if success:
            logger.info("[OK] Backup completed successfully")
        else:
            logger.error(f"[FAIL] Backup failed: {result}")
        logger.info("=" * 60)

        # On failure, alert the operator directly — not only via the optional
        # heartbeat — so a broken backup is never silent.
        if not success:
            self._alert_operator_failure(str(result))

        # Heartbeat reflects the FINAL result (after off-host), so a monitor
        # only sees success when the backup is truly off-box.
        self._ping_heartbeat(success)

        return success


def restore_sqlite(backup_file: Path) -> bool:
    """Restore SQLite database from backup"""
    logger.info(f"Restoring SQLite database from: {backup_file}")

    try:
        db_path = system_config.DB_PATH

        # Backup current database
        if db_path.exists():
            backup_current = db_path.with_suffix(".db.pre-restore")
            shutil.copy2(db_path, backup_current)
            logger.info(f"Current database backed up to: {backup_current}")

        # Decompress if needed
        if backup_file.suffix == ".gz":
            temp_file = backup_file.with_suffix("")
            with gzip.open(backup_file, "rb") as f_in:
                with open(temp_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            restore_source = temp_file
        else:
            restore_source = backup_file

        # Restore database
        shutil.copy2(restore_source, db_path)

        # Cleanup temp file
        if backup_file.suffix == ".gz":
            temp_file.unlink()

        logger.info("[OK] SQLite database restored successfully")
        return True

    except Exception as e:
        logger.exception(f"Restore failed: {e}")
        return False


def restore_postgresql(
    backup_file: Path, db_name: str = None, user: str = None, password: str = None
) -> bool:
    """Restore a PostgreSQL database from backup.

    Defaults to the primary snflwr database; pass db_name/user/password to
    restore another database in the same cluster (e.g. Open WebUI's 'openwebui').
    """
    logger.info(f"Restoring PostgreSQL database from: {backup_file}")

    try:
        # Decompress if needed
        if backup_file.suffix == ".gz":
            temp_file = backup_file.with_suffix("")
            with gzip.open(backup_file, "rb") as f_in:
                with open(temp_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            restore_source = temp_file
        else:
            restore_source = backup_file

        # Validate all PostgreSQL parameters to prevent command injection
        # Reuse validation method from DatabaseBackup class
        import re

        def validate_param(param: str, param_name: str) -> str:
            if not re.match(r"^[a-zA-Z0-9._-]+$", param):
                raise ValueError(
                    f"Invalid {param_name}: contains disallowed characters. "
                    f"Only alphanumeric, dots, hyphens, and underscores are allowed."
                )
            return param

        host = validate_param(system_config.POSTGRES_HOST, "POSTGRES_HOST")
        user = validate_param(user or system_config.POSTGRES_USER, "POSTGRES_USER")
        database = validate_param(db_name or system_config.POSTGRES_DB, "POSTGRES_DB")

        # Build pg_restore command with validated parameters
        cmd = [
            "pg_restore",
            "-h",
            host,
            "-p",
            str(system_config.POSTGRES_PORT),  # Port is int, already safe
            "-U",
            user,
            "-d",
            database,
            "--clean",  # Drop existing objects
            "--if-exists",  # Don't error on non-existent objects
            str(restore_source),
        ]

        # Set password environment variable
        env = os.environ.copy()
        env["PGPASSWORD"] = (
            password if password is not None else system_config.POSTGRES_PASSWORD
        )

        # Run pg_restore
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        # Cleanup temp file
        if backup_file.suffix == ".gz":
            temp_file.unlink()

        if result.returncode != 0:
            logger.error(f"pg_restore failed: {result.stderr}")
            return False

        logger.info("[OK] PostgreSQL database restored successfully")
        return True

    except Exception as e:
        logger.exception(f"Restore failed: {e}")
        return False


def main():
    """Main execution"""
    import argparse

    parser = argparse.ArgumentParser(description="snflwr.ai Database Backup/Restore")
    parser.add_argument(
        "action",
        choices=["backup", "restore", "list", "pull", "verify"],
        help="Action to perform (verify = integrity-check the latest or --file backup)",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Backup file path for restore; backup file NAME for pull",
    )

    args = parser.parse_args()

    backup_manager = DatabaseBackup()

    if args.action == "backup":
        success = backup_manager.run_backup()
        sys.exit(0 if success else 1)

    elif args.action == "list":
        backup_manager.list_backups()
        sys.exit(0)

    elif args.action == "pull":
        if not args.file:
            logger.error("--file (backup name) required for pull")
            sys.exit(1)
        ok, result = backup_manager.pull_offhost(args.file)
        if ok:
            logger.info(f"Pulled to: {result}")
        sys.exit(0 if ok else 1)

    elif args.action == "verify":
        ok, detail = backup_manager.verify_backup(args.file)
        if ok:
            logger.info(f"Backup verify: {detail}")
        else:
            logger.error(f"Backup verify FAILED: {detail}")
            backup_manager._alert_operator_failure(
                f"backup verification failed: {detail}"
            )
        sys.exit(0 if ok else 1)

    elif args.action == "restore":
        if not args.file:
            logger.error("--file required for restore")
            sys.exit(1)

        backup_file = Path(args.file)
        if not backup_file.exists():
            logger.error(f"Backup file not found: {backup_file}")
            sys.exit(1)

        db_type = system_config.DATABASE_TYPE.lower()

        if db_type == "sqlite":
            success = restore_sqlite(backup_file)
        elif db_type == "postgresql":
            success = restore_postgresql(backup_file)
        else:
            logger.error(f"Unsupported database type: {db_type}")
            sys.exit(1)

        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
