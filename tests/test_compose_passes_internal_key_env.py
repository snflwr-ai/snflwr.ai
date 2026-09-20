"""Every INTERNAL_API_KEY* variable config.py reads must reach the API container.

2026-09-19: rotating the relay key with scripts/rotate_internal_key.py relies on
INTERNAL_API_KEY_PREVIOUS so the API accepts the old key until Open WebUI is
reseeded. No compose file passed it (nor INTERNAL_API_KEY_CREATED_AT, which the
API logged as "not set" on every start while the env file set it), so the
advertised zero-downtime rotation was a hard cut. Same class as
test_compose_passes_inference_env.py; derived from the source for the same reason.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILES = (
    ROOT / "docker" / "compose" / "docker-compose.home.yml",
    ROOT / "docker" / "compose" / "docker-compose.yml",
)
_GETENV = re.compile(r'os\.getenv\(\s*"(INTERNAL_API_KEY[A-Z0-9_]*)"')


def _needed() -> set:
    return set(_GETENV.findall((ROOT / "config.py").read_text()))


def test_the_source_actually_reads_the_key_family():
    """Guard the guard: a regex that matches nothing would pass every check."""
    assert {"INTERNAL_API_KEY", "INTERNAL_API_KEY_PREVIOUS"} <= _needed()


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_api_service_receives_every_key_variable(compose):
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    services = yaml.safe_load(compose.read_text())["services"]
    env = services["snflwr-api"].get("environment") or []
    names = (
        set(env)
        if isinstance(env, dict)
        else {str(e).split("=", 1)[0].strip() for e in env}
    )
    missing = sorted(_needed() - names)
    assert not missing, f"{compose.name} snflwr-api does not pass {missing}"


# ---------------------------------------------------------------------------
# Homework-integrity configuration must reach the API too (2026-09-20)
# ---------------------------------------------------------------------------
# The enterprise compose passed SAFETY_MODEL and nothing else, and the k8s
# manifests pass none of it. GUIDANCE_ENFORCEMENT_ENABLED defaults to "false",
# so the enforcer was OFF on the stack aimed at schools -- and could not be
# switched on, because the variable never reached the container. Same class as
# the INTERNAL_API_KEY_PREVIOUS hole above, with a child-facing consequence.

_GUIDANCE = re.compile(r'os\.getenv\(\s*"(GUIDANCE_[A-Z0-9_]+|SAFETY_MODEL[A-Z0-9_]*)"')


def _guidance_vars() -> set:
    return set(_GUIDANCE.findall((ROOT / "config.py").read_text()))


def test_the_source_reads_guidance_configuration():
    """Guard the guard."""
    found = _guidance_vars()
    assert "GUIDANCE_ENFORCEMENT_ENABLED" in found or "GUIDANCE_GATE_MODEL" in found, (
        "no GUIDANCE_* reads found in config.py — the regex is stale"
    )


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_api_service_receives_guidance_configuration(compose):
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    services = yaml.safe_load(compose.read_text())["services"]
    env = services["snflwr-api"].get("environment") or []
    names = (
        set(env)
        if isinstance(env, dict)
        else {str(e).split("=", 1)[0].strip() for e in env}
    )
    missing = sorted(v for v in _guidance_vars() if v not in names)
    assert not missing, (
        f"{compose.name} snflwr-api does not pass {missing}. With "
        "GUIDANCE_ENFORCEMENT_ENABLED absent the enforcer defaults OFF and cannot "
        "be enabled from the env file."
    )
