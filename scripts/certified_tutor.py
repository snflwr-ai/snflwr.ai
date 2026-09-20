#!/usr/bin/env python3
"""What tutor this box should build and serve, from the certified registry.

WHY THIS EXISTS. The install paths asked `resource_detection.recommend_base_model()`
for a gemma4 BASE and wrapped it as `snflwr.ai`. The serving plan, meanwhile,
serves only what has a sealed tutoring run -- today just `snflwr.ai-31b`. The two
disagreed in the worst possible direction:

  * `deploy.sh` rebuilt `snflwr.ai` from whatever the ladder picked (on the very
    box the sealed run was measured on, that is `gemma4:e4b`, because the ladder
    reserves card space for a classifier that is CPU-pinned), so every deploy
    produced an UNCERTIFIED tutor while the plan served a certified one that had
    been built by hand;
  * `.env.example` and `.env.production.example` set
    `OLLAMA_DEFAULT_MODEL=snflwr.ai`, which the quality floor refuses -- so an
    operator who copied the shipped template turned tutoring OFF. Measured
    2026-09-20: configured `snflwr.ai` on a 23 GB card gives
    `tier=unsupported tutoring=False`, while an EMPTY value on the same card
    gives `snflwr.ai-31b tier=certified`. The template was worse than silence.

So the registry answers both questions -- what to build, and what to configure --
and the shell asks it instead of keeping its own opinion. Same reason the ladder
itself was consolidated after five hardcoded copies drifted apart (2026-09-10).

Usage:
    certified_tutor.py [--vram-gb N] [--format tsv|env|model|base]

Exit codes:
    0  a certified backbone fits; details on stdout
    3  nothing certified fits this hardware; the requirement is on stdout
    2  usage error
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing the app writes its startup banner to STDOUT (db init, circuit
# breaker, cache warnings). A shell caller does `MODEL=$(certified_tutor.py
# --format model)`, so one stray line makes the model name garbage -- the whole
# point of this script is to hand shell a value it can trust. Swallow the import
# noise and keep stdout for the answer.
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    from core.serving_plan import (  # noqa: E402 - after sys.path
        CERTIFIED_BACKBONES,
        GPU_RESERVE_GB,
        _certified_for,
        _detect_vram_gb,
    )


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--vram-gb",
        type=float,
        default=None,
        help="VRAM to size against; detected when omitted",
    )
    ap.add_argument(
        "--engine",
        default="ollama",
        help="engine the box will run (default: ollama)",
    )
    ap.add_argument(
        "--format",
        default="tsv",
        choices=("tsv", "env", "model", "base"),
        help="tsv: model<TAB>base<TAB>num_ctx; env: shell assignments",
    )
    args = ap.parse_args(argv)

    vram_gb = args.vram_gb
    if vram_gb is None:
        try:
            vram_gb = float(_detect_vram_gb())
        except Exception:  # noqa: BLE001 - no GPU is a normal answer, not an error
            vram_gb = 0.0

    entry = _certified_for(args.engine, vram_gb)
    if entry is None:
        needed = min(e.vram_gb for e in CERTIFIED_BACKBONES) + GPU_RESERVE_GB
        print(
            f"no certified backbone fits {vram_gb:.1f} GB VRAM on {args.engine} "
            f"(needs {needed:.1f} GB). This box cannot tutor: the serving plan "
            "would disable tutoring rather than serve an uncertified model.",
            file=sys.stdout,
        )
        return 3

    if args.format == "model":
        print(entry.model)
    elif args.format == "base":
        print(entry.base)
    elif args.format == "env":
        print(f"OLLAMA_DEFAULT_MODEL={entry.model}")
        print(f"BASE_MODEL={entry.base}")
        print(f"INFERENCE_NUM_CTX={entry.num_ctx}")
    else:
        print(f"{entry.model}\t{entry.base}\t{entry.num_ctx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
