#!/usr/bin/env python3
"""Rotate INTERNAL_API_KEY in a .env file.

Moves the current key to INTERNAL_API_KEY_PREVIOUS, generates a new
INTERNAL_API_KEY, and stamps INTERNAL_API_KEY_CREATED_AT (UTC). The backend
accepts BOTH keys during the window (constant-time compare in
api/middleware/auth.py), so there is zero downtime. After running, re-seed Open
WebUI so the relay sends the new key, then restart the API. Once OWU is on the
new key, INTERNAL_API_KEY_PREVIOUS can be removed.

Never prints key material.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone

_MANAGED = (
    "INTERNAL_API_KEY",
    "INTERNAL_API_KEY_PREVIOUS",
    "INTERNAL_API_KEY_CREATED_AT",
)


def _key_of(line: str) -> str:
    return line.split("=", 1)[0].strip() if "=" in line else ""


def rotate_env(path: str) -> dict:
    """Rotate INTERNAL_API_KEY in the .env at ``path``. Returns metadata (no key
    material). Raises ValueError if the file has no INTERNAL_API_KEY."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    current = None
    for line in lines:
        if _key_of(line) == "INTERNAL_API_KEY":
            current = line.split("=", 1)[1].strip()
            break
    if not current:
        raise ValueError(f"No INTERNAL_API_KEY found in {path}")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updates = {
        "INTERNAL_API_KEY": secrets.token_hex(32),
        "INTERNAL_API_KEY_PREVIOUS": current,
        "INTERNAL_API_KEY_CREATED_AT": created_at,
    }

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = _key_of(line)
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key in _MANAGED:
        if key not in seen:
            out.append(f"{key}={updates[key]}")

    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return {"created_at": created_at, "path": path}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rotate INTERNAL_API_KEY in a .env file."
    )
    parser.add_argument(
        "--env-file", default=".env", help="Path to the .env file (default: .env)"
    )
    args = parser.parse_args(argv)
    try:
        meta = rotate_env(args.env_file)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(
        f"Rotated INTERNAL_API_KEY in {meta['path']} (created_at={meta['created_at']})."
    )
    print(
        "The old key is now INTERNAL_API_KEY_PREVIOUS (still accepted during the window)."
    )
    print("Next steps (zero-downtime):")
    print("  1. Re-seed Open WebUI with the new key:")
    print(
        "     docker exec snflwr-api printenv INTERNAL_API_KEY | "
        "docker exec -i snflwr-frontend python /tmp/owui_connect.py"
    )
    print("  2. Restart the API so it loads the new .env.")
    print(
        "  3. Once OWU is confirmed on the new key, remove INTERNAL_API_KEY_PREVIOUS."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
