#!/usr/bin/env python3
"""Resolve the latest *stable* Open WebUI release tag.

Used by the guarded upgrader (scripts/owui_upgrade.sh) when no explicit target
tag is given. Stable = a published, non-draft, non-prerelease GitHub release
whose tag is plain semver (``vX.Y.Z``) — never an rc/beta/dev tag.

CLI: prints the resolved tag (e.g. ``v0.9.6``) to stdout and exits 0, or writes
an error to stderr and exits 1. The HTTP fetch is isolated from the selection
logic so the selection can be unit-tested without network access.
"""
import json
import sys
import urllib.request

RELEASES_URL = "https://api.github.com/repos/open-webui/open-webui/releases?per_page=50"


def parse_semver(tag):
    """Parse ``v1.2.3`` / ``1.2.3`` into a ``(1, 2, 3)`` tuple, or None.

    Anything with a pre-release/build suffix (``v1.2.3-rc1``) is rejected so
    only clean stable tags compare as versions.
    """
    if not isinstance(tag, str):
        return None
    core = tag[1:] if tag.startswith("v") else tag
    parts = core.split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def pick_latest_stable(releases):
    """Return the highest stable release tag from a GitHub releases list.

    *releases* is the parsed JSON list of release objects. Drafts,
    prereleases, and non-semver tags are ignored. Returns the tag string
    (e.g. ``v0.9.6``) or None if there are no stable releases.
    """
    best_version = None
    best_tag = None
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name")
        version = parse_semver(tag)
        if version is None:
            continue
        if best_version is None or version > best_version:
            best_version = version
            best_tag = tag
    return best_tag


def fetch_releases(timeout=15):
    """Fetch the GitHub releases list. Raises on network/parse error."""
    req = urllib.request.Request(
        RELEASES_URL, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def latest_stable_version():
    """Resolve the latest stable tag, or None if it can't be determined."""
    return pick_latest_stable(fetch_releases())


def main():
    try:
        tag = latest_stable_version()
    except Exception as exc:  # network, JSON, etc.
        print(f"ERROR: could not query Open WebUI releases: {exc}", file=sys.stderr)
        return 1
    if not tag:
        print("ERROR: no stable Open WebUI release found", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
