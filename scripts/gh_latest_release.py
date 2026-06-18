#!/usr/bin/env python3
"""Resolve the latest *stable* GitHub release tag for any repo.

Used by the guarded upgraders (scripts/guarded_upgrade.sh) to find the newest
version to upgrade a component to. Stable = a published, non-draft,
non-prerelease release whose tag is plain semver (``vX.Y.Z``) — never an
rc/beta/dev tag.

CLI:
    gh_latest_release.py open-webui/open-webui   ->  v0.9.6
    gh_latest_release.py ollama/ollama           ->  v0.30.10

Prints the resolved tag to stdout and exits 0, or writes an error to stderr
and exits 1. The HTTP fetch is isolated from the selection logic so the
selection can be unit-tested without network access.
"""
import json
import sys
import urllib.request


def releases_url(repo):
    return f"https://api.github.com/repos/{repo}/releases?per_page=50"


def parse_semver(tag):
    """Parse ``v1.2.3`` / ``1.2.3`` into ``(1, 2, 3)``, or None.

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

    Drafts, prereleases, and non-semver tags are ignored. Returns the tag
    string (e.g. ``v0.9.6``) or None if there are no stable releases.
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


def fetch_releases(repo, timeout=15):
    """Fetch the GitHub releases list for *repo*. Raises on network/parse error."""
    req = urllib.request.Request(
        releases_url(repo), headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def latest_stable_version(repo):
    """Resolve the latest stable tag for *repo*, or None if undeterminable."""
    return pick_latest_stable(fetch_releases(repo))


def main(argv):
    if len(argv) != 2:
        print("usage: gh_latest_release.py <owner/repo>", file=sys.stderr)
        return 2
    repo = argv[1]
    try:
        tag = latest_stable_version(repo)
    except Exception as exc:  # network, JSON, etc.
        print(f"ERROR: could not query {repo} releases: {exc}", file=sys.stderr)
        return 1
    if not tag:
        print(f"ERROR: no stable release found for {repo}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
