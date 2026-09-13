"""A default declared in BOTH compose and config.py is a default that can drift.

2026-09-12: config.py's GUIDANCE_ENFORCER_TIMEOUT_S was raised 8 -> 15 because a
confirm needing 9.3s was timing out and fail-opening a reveal it had correctly
identified. The code shipped, CI was green, the container was healthy -- and the
live value was still 8, because docker-compose.home.yml carries its own
`${VAR:-8}` default which wins over the code's.

The prompts in the same deploy DID take effect, so nothing looked wrong. Only
reading the setting out of the running container caught it.

This compares every `${VAR:-default}` in the compose file against the default in
config.py and fails on any disagreement.
"""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker" / "compose" / "docker-compose.home.yml"

# ${NAME:-default} inside an environment entry
_SUBST = re.compile(r"\$\{([A-Z0-9_]+):-([^}]*)\}")
# os.getenv("NAME", "default") in config.py
_GETENV = re.compile(r'os\.getenv\(\s*"([A-Z0-9_]+)"\s*,\s*"([^"]*)"\s*\)')


def _compose_defaults() -> dict:
    if not COMPOSE.exists():
        pytest.skip(f"{COMPOSE} not present")
    return {name: default for name, default in _SUBST.findall(COMPOSE.read_text())}


def _config_defaults() -> dict:
    return {
        name: default
        for name, default in _GETENV.findall((ROOT / "config.py").read_text())
    }


# Deliberate empty-overrides: compose passes "" so an unconfigured deployment
# stays unconfigured rather than silently inheriting a plausible-looking default.
# These are NOT the drift this test exists to catch, but they ARE the same
# mechanism -- compose winning over config.py -- so they are listed explicitly
# rather than excluded by a blanket "ignore empty" rule.
#
# Worth a look independently: with SMTP_HOST="" the code falls through to
# "smtp.gmail.com" only in utils/email_alerts.py, not in config.py, so the two
# disagree about where email is meant to go. Not changed here; out of scope.
DELIBERATE_EMPTY_OVERRIDES = {"SMTP_HOST", "SMTP_FROM_EMAIL", "SMTP_FROM_NAME"}


def test_compose_and_config_agree_on_every_shared_default():
    compose, config = _compose_defaults(), _config_defaults()
    shared = sorted(set(compose) & set(config))
    assert shared, "no shared defaults found — the regexes stopped matching"
    mismatched = {
        name: (compose[name], config[name])
        for name in shared
        if compose[name].strip() != config[name].strip()
        and not (name in DELIBERATE_EMPTY_OVERRIDES and compose[name].strip() == "")
    }
    assert not mismatched, (
        "compose default wins over config.py, so these silently override the code: "
        + ", ".join(
            f"{n}: compose={c!r} config={g!r}" for n, (c, g) in mismatched.items()
        )
    )
