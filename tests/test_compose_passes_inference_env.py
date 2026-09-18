"""An env var the code reads must actually reach the container.

2026-09-18: the install-time context probe shipped, ran correctly on the
reference box, found 24576, and wrote `INFERENCE_NUM_CTX=24576` to the env file.
The container went on serving 16384, because no compose file passed
`INFERENCE_NUM_CTX` into the API service. The measurement was right, the write
was right, and nothing acted on it.

The same hole silently disabled ALL of remote inference mode: with
`INFERENCE_REMOTE_URL` and the two tokens unreachable, a deployment could not be
put into remote mode or made to serve it, whatever the operator put in the file.

This test reads the names `core/serving_plan.py`, `core/inference/client.py` and
`api/routes/inference.py` actually pull out of the environment, and fails if a
compose file that runs the API does not pass them. It is deliberately derived
from the SOURCE rather than a hand-written list, so a variable added later is
covered without anyone remembering to update this file.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Files whose os.getenv calls define what the serving stack needs at runtime.
_SOURCES = (
    ROOT / "core" / "serving_plan.py",
    ROOT / "core" / "inference" / "client.py",
    ROOT / "api" / "routes" / "inference.py",
)

# Compose files that build/run the snflwr-api service.
_COMPOSE_FILES = (
    ROOT / "docker" / "compose" / "docker-compose.home.yml",
    ROOT / "docker" / "compose" / "docker-compose.yml",
)

_GETENV = re.compile(r'os\.getenv\(\s*"(INFERENCE_[A-Z0-9_]+|VLLM_[A-Z0-9_]+)"')


def _needed() -> set:
    names = set()
    for src in _SOURCES:
        if src.exists():
            names |= set(_GETENV.findall(src.read_text()))
    return names


def test_the_source_actually_reads_some_inference_vars():
    """Guard the guard: a regex that matches nothing would pass every check."""
    assert _needed(), "no INFERENCE_*/VLLM_* os.getenv calls found — regex is stale"


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_every_inference_var_the_code_reads_is_passed_through(compose):
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    text = compose.read_text()
    missing = sorted(n for n in _needed() if n not in text)
    assert not missing, (
        f"{compose.name} does not pass {missing} into the API container. "
        "The serving plan reads these from the process environment, so a value "
        "in the env file alone has no effect."
    )


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_the_probed_context_window_reaches_the_container(compose):
    """The specific one that was broken, named so a failure reads clearly."""
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    assert "INFERENCE_NUM_CTX" in compose.read_text(), (
        f"{compose.name} drops INFERENCE_NUM_CTX: the install-time probe's "
        "result would be measured, written, and then ignored."
    )


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_remote_mode_can_be_switched_on_at_all(compose):
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    text = compose.read_text()
    for name in ("INFERENCE_REMOTE_URL", "INFERENCE_REMOTE_TOKEN", "INFERENCE_SERVER_TOKEN"):
        assert name in text, f"{compose.name} drops {name}: remote mode is unreachable"


@pytest.mark.parametrize("compose", _COMPOSE_FILES, ids=lambda p: p.name)
def test_the_tokens_default_to_empty_not_to_a_value(compose):
    """A credential must never acquire a default in a compose file."""
    if not compose.exists():
        pytest.skip(f"{compose.name} not present")
    text = compose.read_text()
    for name in ("INFERENCE_REMOTE_TOKEN", "INFERENCE_SERVER_TOKEN"):
        for default in re.findall(r"\$\{%s:-([^}]*)\}" % name, text):
            assert default == "", f"{compose.name} gives {name} a default value"
