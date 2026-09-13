"""A safety model name that does not match EXACTLY disables content safety.

`SemanticClassifier._find_model` resolves the configured model with
`preferred in names`, where names come from `ollama list`. There is no
normalisation, so "llama-guard3-cpu" does not match "llama-guard3-cpu:latest".
When neither the preferred model nor any fallback matches, it returns None and
the classifier goes disabled -- no exception, no failed healthcheck.

Two live traps found on 2026-09-12:
  * the shipped fallback `llama-guard3:1b` is not pulled by any setup step here,
    so a missing preferred model fell back to nothing;
  * writing `SAFETY_MODEL=llama-guard3-cpu` (the natural thing) would not have
    matched `llama-guard3-cpu:latest` and would have silently turned content
    safety off on a K-12 product.

This pins the shape of the names rather than the contents of anyone's model
store, so it runs without a daemon.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker" / "compose" / "docker-compose.home.yml"

# Ollama reports "name:tag". A bare name never matches.
_TAGGED = re.compile(r"^[A-Za-z0-9._\-]+:[A-Za-z0-9._\-]+$")


def _compose_value(var: str) -> str:
    m = re.search(rf"{var}=\$\{{{var}:-([^}}]*)\}}", COMPOSE.read_text())
    assert m, f"{var} is not declared in {COMPOSE.name}"
    return m.group(1)


def test_safety_model_default_is_a_tagged_name():
    value = _compose_value("SAFETY_MODEL")
    assert _TAGGED.match(value), (
        f"SAFETY_MODEL={value!r} has no tag. _find_model compares against "
        "`ollama list` names verbatim, so an untagged name matches nothing and "
        "silently disables the classifier."
    )


def test_every_safety_fallback_is_a_tagged_name():
    for fb in [
        v.strip()
        for v in _compose_value("SAFETY_MODEL_FALLBACKS").split(",")
        if v.strip()
    ]:
        assert _TAGGED.match(fb), f"fallback {fb!r} has no tag"


def test_config_default_fallback_is_tagged_too():
    """config.py's default applies when compose does not pass the variable."""
    src = (ROOT / "config.py").read_text()
    m = re.search(r'os\.getenv\(\s*"SAFETY_MODEL_FALLBACKS"\s*,\s*"([^"]*)"', src)
    assert m, "SAFETY_MODEL_FALLBACKS default not found in config.py"
    for fb in [v.strip() for v in m.group(1).split(",") if v.strip()]:
        assert _TAGGED.match(fb), f"config.py fallback {fb!r} has no tag"
