#!/usr/bin/env python3
"""Fail if requirements.lock has drifted from requirements.txt.

WHY
---
``langfuse==2.60.3`` was listed in requirements.txt but ABSENT from
requirements.lock. ``docker/Dockerfile`` installs only from the lock, with
``--require-hashes``, so the built container never contained langfuse — and
``utils/observability.py`` imports it inside a broad ``except``, so rather than
failing loudly the tracing client silently returned ``None``. Observability that
reports nothing is indistinguishable from observability with nothing to report,
so nobody noticed. Nothing in CI checked.

WHAT THIS CHECKS, AND WHY NOT THE OBVIOUS THING
-----------------------------------------------
The obvious guard is "re-run pip-compile and fail if the output differs". That
one goes red the day any transitive dependency publishes a new release, with no
change to this repo at all, because the resolver picks up the newer version.
A check that fails for reasons unrelated to the commit trains people to ignore
it, which is worse than no check.

So this checks CONTAINMENT instead: every ``name==version`` pinned in
requirements.txt must appear at that same version in requirements.lock. No
resolver, no network, fully deterministic — and it catches the failure mode that
actually occurred. Extra packages in the lock are expected and fine; those are
the transitive dependencies.

Usage:
    python scripts/check_lock_sync.py                 # defaults to repo root
    python scripts/check_lock_sync.py reqs.txt lock   # explicit paths

Exit 0 when in sync, 1 when drifted (with the regeneration command in the
output), 2 when a file is unreadable.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

REGEN_COMMAND = (
    "pip-compile --allow-unsafe --generate-hashes "
    "--output-file=requirements.lock requirements.txt"
)

# `name[extra1,extra2]==1.2.3` — extras are stripped; only `==` pins are checked,
# since a range says nothing about what the lock should contain.
_PIN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*"
    r"(?P<version>[^\s;#\\]+)"
)


def canon(name: str) -> str:
    """PEP 503 canonical form: lowercase, runs of -_. collapsed to a hyphen.

    Without this, `Pillow` in one file and `pillow` in the other read as two
    different packages and the guard reports phantom drift.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> Dict[str, str]:
    """Map canonical package name -> pinned version from a requirements file.

    Skips comments, blanks, `-r`/`-c` includes (each included file is checked in
    its own right) and any non-`==` specifier.
    """
    pins: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = _PIN_RE.match(stripped)
        if match:
            pins[canon(match.group("name"))] = match.group("version")
    return pins


def parse_lock(text: str) -> Dict[str, str]:
    """Map canonical package name -> version from a pip-compile lockfile.

    Lock entries are ``name==version \\`` followed by indented ``--hash=`` lines
    and ``# via`` comments; the same pin regex handles the first line of each
    entry and the indented continuations simply do not match.
    """
    return parse_requirements(text)


def find_drift(reqs: Dict[str, str], lock: Dict[str, str]) -> List[str]:
    """Every problem, not just the first — a partial report invites a second
    round trip through CI."""
    problems: List[str] = []
    for name, version in sorted(reqs.items()):
        if name not in lock:
            problems.append(f"{name}=={version} is missing from the lockfile")
        elif lock[name] != version:
            problems.append(
                f"{name} is pinned to {version} but the lockfile has {lock[name]}"
            )
    return problems


def main(argv: List[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    req_path = Path(argv[1]) if len(argv) > 1 else root / "requirements.txt"
    lock_path = Path(argv[2]) if len(argv) > 2 else root / "requirements.lock"

    try:
        reqs = parse_requirements(req_path.read_text())
        lock = parse_lock(lock_path.read_text())
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not reqs:
        print(
            f"error: no pins parsed from {req_path} — refusing to report 'in sync' "
            "on an empty parse",
            file=sys.stderr,
        )
        return 2

    drift = find_drift(reqs, lock)
    if not drift:
        print(f"lockfile in sync ({len(reqs)} pins checked against {len(lock)} locked)")
        return 0

    print(f"requirements.lock has drifted from {req_path.name}:", file=sys.stderr)
    for problem in drift:
        print(f"  - {problem}", file=sys.stderr)
    print(f"\nRegenerate it with:\n  {REGEN_COMMAND}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
