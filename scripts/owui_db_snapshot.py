#!/usr/bin/env python3
"""Crash-consistent snapshot and restore of Open WebUI's SQLite ``webui.db``.

Used by ``scripts/guarded_upgrade.sh`` for the owui component. It runs INSIDE
the Open WebUI image (which ships Python), so it needs only the stdlib.

Why this exists: the upgrader used to ``docker cp`` the bare ``webui.db`` out of
a running container and, on rollback, ``docker cp`` it back over the volume.
Open WebUI runs SQLite in WAL mode, so that was wrong twice:

* the copy missed every committed write still sitting in ``webui.db-wal``, and
* the restore left the newer image's ``-wal``/``-shm`` in place. Once a
  checkpoint has run, those WAL frames describe a different base file; SQLite
  replays them onto the restored copy and the database is malformed.

The home stack's ``webui.db`` was found malformed on 2026-09-16 (quick_check
failed; chat indexes pointed at rows the table no longer held), and the next Open
WebUI upgrade died in its Alembic migrations as a result.

Commands (exit 0 on success, 1 on failure, 2 on usage error):

    check    DB           integrity-check a database
    snapshot DB DEST      online backup via the SQLite backup API, verified
    restore  BACKUP DB    replace DB with BACKUP; the owning app MUST be stopped
"""

import os
import shutil
import sqlite3
import sys
from typing import Tuple

SIDECARS = ("-wal", "-shm", "-journal")


def integrity(path: str) -> Tuple[bool, str]:
    """Return (ok, detail) from ``PRAGMA quick_check`` on ``path``."""
    if not os.path.isfile(path):
        return False, "no such file"
    try:
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, str(exc)
    if rows == [("ok",)]:
        return True, "ok"
    return False, "; ".join(str(r[0]) for r in rows[:3])


def snapshot(db: str, dest: str) -> Tuple[bool, str]:
    """Copy a live WAL-mode database consistently, WAL contents included."""
    ok, detail = integrity(db)
    if not ok:
        return False, f"source fails integrity check ({detail})"
    tmp = dest + ".partial"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(db)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    ok, detail = integrity(tmp)
    if not ok:
        os.remove(tmp)
        return False, f"snapshot fails integrity check ({detail})"
    os.replace(tmp, dest)
    return True, "ok"


def restore(backup: str, db: str) -> Tuple[bool, str]:
    """Replace ``db`` with ``backup``, dropping sidecars that belong to the old file."""
    ok, detail = integrity(backup)
    if not ok:
        return False, f"backup fails integrity check ({detail})"
    owner = os.stat(db) if os.path.exists(db) else None
    tmp = db + ".restore"
    shutil.copyfile(backup, tmp)
    for suffix in SIDECARS:
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    os.replace(tmp, db)
    if owner is not None:
        try:
            os.chown(db, owner.st_uid, owner.st_gid)
        except PermissionError:
            pass
        os.chmod(db, owner.st_mode & 0o777)
    return integrity(db)


def main(argv: list) -> int:
    commands = {"check": 1, "snapshot": 2, "restore": 2}
    if len(argv) < 2 or argv[1] not in commands or len(argv) != commands[argv[1]] + 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, args = argv[1], argv[2:]
    if cmd == "check":
        ok, detail = integrity(args[0])
    elif cmd == "snapshot":
        ok, detail = snapshot(args[0], args[1])
    else:
        ok, detail = restore(args[0], args[1])
    print(f"{cmd}: {detail}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
