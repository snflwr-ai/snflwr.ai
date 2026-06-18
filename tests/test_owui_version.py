"""Tests for the Open WebUI version resolver used by the guarded upgrader."""

import sys
from pathlib import Path

import pytest

# scripts/ is not a package; add it to the path so we can import the module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import owui_version  # noqa: E402


class TestParseSemver:
    @pytest.mark.parametrize("tag,expected", [
        ("v0.9.6", (0, 9, 6)),
        ("0.9.6", (0, 9, 6)),
        ("v1.0.0", (1, 0, 0)),
        ("v0.10.2", (0, 10, 2)),
    ])
    def test_valid(self, tag, expected):
        assert owui_version.parse_semver(tag) == expected

    @pytest.mark.parametrize("tag", [
        "v0.9.6-rc1", "v0.9", "v0.9.6.1", "latest", "main", "", None, 1234,
    ])
    def test_invalid(self, tag):
        assert owui_version.parse_semver(tag) is None


class TestPickLatestStable:
    def test_picks_highest_stable(self):
        releases = [
            {"tag_name": "v0.8.12", "prerelease": False, "draft": False},
            {"tag_name": "v0.9.6", "prerelease": False, "draft": False},
            {"tag_name": "v0.9.0", "prerelease": False, "draft": False},
        ]
        assert owui_version.pick_latest_stable(releases) == "v0.9.6"

    def test_numeric_not_lexical_ordering(self):
        # v0.10.0 > v0.9.9 numerically (lexical sort would pick v0.9.9)
        releases = [
            {"tag_name": "v0.9.9", "prerelease": False, "draft": False},
            {"tag_name": "v0.10.0", "prerelease": False, "draft": False},
        ]
        assert owui_version.pick_latest_stable(releases) == "v0.10.0"

    def test_skips_prereleases_and_drafts(self):
        releases = [
            {"tag_name": "v0.8.12", "prerelease": False, "draft": False},
            {"tag_name": "v1.0.0", "prerelease": True, "draft": False},   # rc
            {"tag_name": "v0.9.9", "prerelease": False, "draft": True},   # draft
        ]
        assert owui_version.pick_latest_stable(releases) == "v0.8.12"

    def test_skips_non_semver_tags(self):
        releases = [
            {"tag_name": "nightly", "prerelease": False, "draft": False},
            {"tag_name": "v0.8.12", "prerelease": False, "draft": False},
        ]
        assert owui_version.pick_latest_stable(releases) == "v0.8.12"

    def test_no_stable_returns_none(self):
        releases = [
            {"tag_name": "v1.0.0", "prerelease": True, "draft": False},
            {"tag_name": "dev", "prerelease": False, "draft": False},
        ]
        assert owui_version.pick_latest_stable(releases) is None

    def test_empty_returns_none(self):
        assert owui_version.pick_latest_stable([]) is None

    def test_tolerates_malformed_entries(self):
        releases = [
            "not a dict",
            {"no_tag": True, "prerelease": False, "draft": False},
            {"tag_name": "v0.8.12", "prerelease": False, "draft": False},
        ]
        assert owui_version.pick_latest_stable(releases) == "v0.8.12"


class TestLatestStableVersion:
    def test_orchestrates_fetch_and_pick(self, monkeypatch):
        monkeypatch.setattr(owui_version, "fetch_releases", lambda: [
            {"tag_name": "v0.9.6", "prerelease": False, "draft": False},
            {"tag_name": "v0.8.12", "prerelease": False, "draft": False},
        ])
        assert owui_version.latest_stable_version() == "v0.9.6"
