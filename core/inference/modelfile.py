"""Read the tutor persona and sampling parameters out of the Ollama Modelfile.

`models/Snflwr_AI_Kids.modelfile` is the single source of truth for who the tutor
is and how it samples. Ollama applies it automatically. vLLM has no equivalent:
it serves plain weights, so whatever the Modelfile declares has to travel in
every request instead.

That makes this module a safety boundary, not a convenience. The persona carries
the S9051B compliance posture and the homework-withholding instructions; a vLLM
deployment that silently served an empty system prompt would be a different
product with the same name. Parsing failures therefore raise — they never fall
back to "no persona".
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

DEFAULT_MODELFILE = (
    Path(__file__).resolve().parents[2] / "models" / "Snflwr_AI_Kids.modelfile"
)

# PARAMETERs that configure the SERVER (engine flags), not a single request.
_SERVER_PARAMETERS = frozenset(
    {
        "num_ctx",
        "num_gpu",
        "num_thread",
        "num_batch",
        "repeat_last_n",
        "num_predict",
        "keep_alive",
    }
)

_SYSTEM_TRIPLE = re.compile(r'^\s*SYSTEM\s+"""(.*?)"""', re.DOTALL | re.MULTILINE)
_SYSTEM_SINGLE = re.compile(r'^\s*SYSTEM\s+"([^"\n]*)"\s*$', re.MULTILINE)
_PARAMETER = re.compile(r"^\s*PARAMETER\s+(\S+)\s+(.*?)\s*$", re.MULTILINE)


class ModelfileError(RuntimeError):
    """The Modelfile is missing, unreadable, or has no persona."""


@dataclass(frozen=True)
class ModelfileSpec:
    persona: str
    sampling: dict = field(default_factory=dict)
    num_ctx: int = 0
    num_predict: int = 0
    source: str = ""

    @property
    def persona_sha256(self) -> str:
        return hashlib.sha256(self.persona.encode()).hexdigest()


def _coerce(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load(path: Union[str, Path, None] = None) -> ModelfileSpec:
    """Parse a Modelfile into the persona and the parameters a request needs."""
    target = Path(path or os.getenv("TUTOR_MODELFILE_PATH") or DEFAULT_MODELFILE)
    try:
        text = target.read_text()
    except OSError as exc:
        raise ModelfileError(f"cannot read Modelfile {target}: {exc}") from exc

    match = _SYSTEM_TRIPLE.search(text) or _SYSTEM_SINGLE.search(text)
    if not match:
        raise ModelfileError(f"no SYSTEM block in {target}")
    persona = match.group(1).strip()

    sampling: dict = {}
    num_ctx = 0
    num_predict = 0
    for name, raw in _PARAMETER.findall(text):
        key = name.strip().lower()
        value = _coerce(raw)
        if key == "num_ctx":
            num_ctx = int(value) if isinstance(value, (int, float)) else 0
            continue
        if key == "num_predict":
            num_predict = int(value) if isinstance(value, (int, float)) else 0
            continue
        if key in _SERVER_PARAMETERS:
            continue
        if key == "stop":
            sampling.setdefault("stop", []).append(value)
        else:
            sampling[key] = value

    return ModelfileSpec(
        persona=persona,
        sampling=sampling,
        num_ctx=num_ctx,
        num_predict=num_predict,
        source=str(target),
    )


_CACHE: dict[str, ModelfileSpec] = {}


def get(path: Union[str, Path, None] = None) -> ModelfileSpec:
    """Cached load — the persona is ~36 KB and never changes at runtime."""
    target = str(path or os.getenv("TUTOR_MODELFILE_PATH") or DEFAULT_MODELFILE)
    if target not in _CACHE:
        spec = load(target)
        _CACHE[target] = spec
        logger.info(
            "tutor persona loaded from %s (%d chars, sha256 %s)",
            spec.source,
            len(spec.persona),
            spec.persona_sha256[:12],
        )
    return _CACHE[target]
