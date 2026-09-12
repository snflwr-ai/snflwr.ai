"""Verify a deploy's database credentials BEFORE the running container is replaced.

On 2026-09-12 a deploy passed `--env-file .env` to the home stack. `.env` is the
DEV config -- it targets a plaintext dev database and carries a different
DB_ENCRYPTION_KEY -- so the new container could not open the production database
and crash-looped. The application's own fail-closed checks worked exactly as
designed, but they run at startup, which is *after* `docker compose up -d` has
already destroyed the healthy container. The service was down for minutes.

This moves that same verification earlier, into a throwaway container that
touches nothing. It deliberately reuses the application's own adapter factory
rather than re-implementing a SQLCipher open: a hand-rolled `PRAGMA key` with a
different `kdf_iter` would reject a CORRECT key and block a good deploy. Calling
the real code path means preflight and startup agree by construction.

Usage (from deploy.sh, or by hand):

    docker run --rm --env-file .env.home \
      -v compose_snflwr-home-data:/realdata:ro \
      snflwr-api:latest python scripts/preflight_db_key.py --db /realdata/snflwr.db

Exit codes: 0 the config opens this database; 1 it does not; 2 bad invocation.
No key material is ever printed, on any path.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _describe_env() -> str:
    """Name the configuration under test without revealing any of it."""
    enabled = os.environ.get("DB_ENCRYPTION_ENABLED", "(unset)")
    key = os.environ.get("DB_ENCRYPTION_KEY", "")
    return f"DB_ENCRYPTION_ENABLED={enabled}, DB_ENCRYPTION_KEY={'set' if key else 'EMPTY'}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="path to the database to test")
    ap.add_argument(
        "--env-label",
        default=os.environ.get("PREFLIGHT_ENV_LABEL", "the supplied environment"),
        help="name of the env file under test, for the failure message",
    )
    args = ap.parse_args()

    source = Path(args.db)
    if not source.exists() or source.stat().st_size == 0:
        # A fresh deploy legitimately has no database yet; startup creates one
        # in whichever mode is configured, and there is no key to disagree with.
        print(f"[preflight] no database at {source} yet — nothing to verify")
        return 0

    # Work on a copy. A live WAL-mode database opened through a read-only mount
    # can fail for reasons indistinguishable from a wrong key, and the key check
    # only needs page 1, which is stable. This also makes writes to the real
    # volume impossible rather than merely unlikely.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.db"
        shutil.copy2(source, probe)

        from storage.db_adapters import create_adapter

        try:
            adapter = create_adapter("sqlite", db_path=str(probe))
            # Touch the schema: opening a SQLCipher database with the wrong key
            # succeeds and only fails on first read.
            adapter.execute_query("SELECT count(*) FROM sqlite_master")
        except Exception as exc:  # noqa: BLE001 -- any failure blocks the deploy
            print(f"[preflight] FAILED to open {source}", file=sys.stderr)
            print(f"[preflight] config under test: {_describe_env()}", file=sys.stderr)
            print(f"[preflight] {type(exc).__name__}: {exc}", file=sys.stderr)
            print(
                f"\n[preflight] {args.env_label} cannot open this database.\n"
                "  The home stack's production database lives in the\n"
                "  compose_snflwr-home-data volume and is ENCRYPTED; it needs\n"
                "  .env.home. The repo's data/snflwr.db is the PLAINTEXT dev\n"
                "  database and needs .env. These are different databases with\n"
                "  different keys, and that separation is correct — do not try\n"
                "  to make the two env files match.\n"
                "  Deploy with: ./deploy.sh    (it selects .env.home for you)",
                file=sys.stderr,
            )
            return 1
        finally:
            try:
                adapter.close()
            except Exception:  # noqa: BLE001 -- probe copy is discarded anyway
                pass

    print(f"[preflight] OK — {args.env_label} opens {source} ({_describe_env()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
