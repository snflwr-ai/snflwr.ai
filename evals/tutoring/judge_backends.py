"""Pluggable judge backends for the tutoring bake-off.

`judge.judge_case()` takes a `generate(prompt) -> str` callable, so the judge
model is swappable. Two backends:

- **ollama** — a local model (privacy-aligned, free). Cheap, but a small local
  model grading a larger candidate is a weak evaluator, and a gemma judge grading
  gemma candidates carries model-family bias.
- **anthropic** — a frontier Claude model. The stronger, NEUTRAL evaluator for
  settling close calls: it is not a gemma candidate, so it has no family bias
  toward either side. The eval dataset is synthetic tutoring Q&A (no real child
  data), so grading it through an API is acceptable for this offline benchmark.

The Anthropic backend is the "stronger judge" re-run path. It needs the optional
`anthropic` SDK and ANTHROPIC_API_KEY; absent either, it fails fast with a clear
message rather than silently degrading.
"""

from __future__ import annotations

import os
from typing import Callable

# Strongest neutral judge by default; override with --judge on the CLI.
DEFAULT_ANTHROPIC_JUDGE = "claude-opus-4-8"


def ollama_judge(base_url: str, model: str) -> Callable[[str], str]:
    """Local Ollama judge (free, privacy-aligned, but a weaker evaluator)."""
    from evals.tutoring import run_eval

    def generate(prompt: str) -> str:
        return run_eval.generate_via_ollama(prompt, "adult evaluator", base_url, model)

    return generate


def anthropic_judge(
    model: str = DEFAULT_ANTHROPIC_JUDGE, api_key: str | None = None
) -> Callable[[str], str]:
    """Claude judge — the stronger, neutral evaluator.

    Uses adaptive thinking so the judge reasons about the rubric before scoring,
    and returns the final text block (the JSON object `judge.parse_judge_response`
    expects). Requires `pip install anthropic` and ANTHROPIC_API_KEY."""
    try:
        import anthropic
    except ImportError as exc:  # optional dependency
        raise SystemExit(
            "The Claude judge needs the Anthropic SDK: pip install anthropic"
        ) from exc

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set — required for --judge-backend anthropic"
        )

    client = anthropic.Anthropic(api_key=key)

    def generate(prompt: str) -> str:
        message = client.messages.create(
            model=model,
            max_tokens=2048,  # headroom for adaptive thinking + the JSON verdict
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    return generate


def build_judge(backend: str, *, model: str, base_url: str) -> Callable[[str], str]:
    """Resolve a judge `generate` callable for the chosen backend."""
    if backend == "ollama":
        return ollama_judge(base_url, model)
    if backend == "anthropic":
        return anthropic_judge(model=model)
    raise SystemExit(f"unknown judge backend: {backend!r} (expected ollama|anthropic)")
