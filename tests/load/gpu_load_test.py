#!/usr/bin/env python3
"""GPU-saturation load test for the student /api/chat path.

The existing locustfile exercises mixed traffic and measures HTTP latency. This
script is purpose-built to find the **single-GPU ceiling**: it ramps concurrent
/api/chat requests and reports the metrics that actually reflect GPU saturation
on a tutor turn (input-guard + answer + output-guard = ~3 inferences):

  * TTFT   — time to first streamed token (perceived responsiveness)
  * tok/s  — output tokens / generation time (raw GPU throughput)
  * p50/p95 total latency, and how they degrade with concurrency
  * throughput (completed turns/min) and error rate per concurrency level

It talks plain HTTP to a DEPLOYED instance (no app internals), so run it on or
near the target box. Ollama + the models must be loaded.

Usage:
  python scripts/gpu_load_test.py \
      --url http://localhost:39150 --token "$SF_TOKEN" \
      --model snflwr.ai --concurrency 1,2,4,8 --requests 16 --stream

Get a token: POST /api/auth/login (see tests/load/README.md). A student token is
ideal — it exercises the full safety pipeline (admins bypass it).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:  # pragma: no cover - runtime guard
    raise SystemExit("httpx is required: pip install httpx")


PROMPTS = [
    "Can you explain how photosynthesis works?",
    "What is 3/4 plus 1/2? Show the steps.",
    "Why is the sky blue?",
    "Help me write a topic sentence about recycling.",
    "What were the main causes of the American Revolution?",
]


@dataclass
class Result:
    ok: bool
    ttft_s: float | None = None       # time to first content token
    total_s: float | None = None      # full turn wall-clock
    out_tokens: int = 0
    status: int = 0
    error: str = ""


@dataclass
class LevelStats:
    concurrency: int
    results: list[Result] = field(default_factory=list)

    def _ok(self) -> list[Result]:
        return [r for r in self.results if r.ok]

    def summary(self, wall_s: float) -> dict:
        ok = self._ok()
        n_ok = len(ok)

        def pct(vals, p):
            if not vals:
                return None
            vals = sorted(vals)
            k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
            return vals[k]

        ttfts = [r.ttft_s for r in ok if r.ttft_s is not None]
        totals = [r.total_s for r in ok if r.total_s is not None]
        toks = [
            r.out_tokens / r.total_s
            for r in ok
            if r.total_s and r.total_s > 0 and r.out_tokens
        ]
        return {
            "concurrency": self.concurrency,
            "ok": n_ok,
            "failed": len(self.results) - n_ok,
            "ttft_p50": pct(ttfts, 50),
            "ttft_p95": pct(ttfts, 95),
            "total_p50": pct(totals, 50),
            "total_p95": pct(totals, 95),
            "tok_s_mean": statistics.mean(toks) if toks else None,
            "turns_per_min": (n_ok / wall_s * 60) if wall_s > 0 else 0,
        }


async def _one_request(
    client: httpx.AsyncClient, url: str, token: str, model: str, prompt: str, stream: bool
) -> Result:
    body = {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        if stream:
            ttft = None
            out_tokens = 0
            async with client.stream(
                "POST", f"{url}/api/chat", json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    return Result(ok=False, status=resp.status_code, error="non-200")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    content = (obj.get("message") or {}).get("content", "")
                    if content:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        out_tokens += 1  # NDJSON chunk ~= 1 token (Ollama default)
            total = time.perf_counter() - t0
            return Result(ok=True, ttft_s=ttft, total_s=total, out_tokens=out_tokens, status=200)
        else:
            resp = await client.post(f"{url}/api/chat", json=body, headers=headers)
            total = time.perf_counter() - t0
            if resp.status_code != 200:
                return Result(ok=False, status=resp.status_code, error="non-200")
            data = resp.json()
            out_tokens = int(data.get("eval_count") or 0)
            # No streaming → TTFT == total (first byte is the whole message).
            return Result(ok=True, ttft_s=total, total_s=total, out_tokens=out_tokens, status=200)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the run
        return Result(ok=False, error=type(exc).__name__)


async def _run_level(args, concurrency: int) -> tuple[LevelStats, float]:
    stats = LevelStats(concurrency=concurrency)
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency + 2)
    timeout = httpx.Timeout(args.timeout)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        async def worker(i: int):
            async with sem:
                prompt = PROMPTS[i % len(PROMPTS)]
                stats.results.append(
                    await _one_request(client, args.url, args.token, args.model, prompt, args.stream)
                )

        wall0 = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(args.requests)))
        wall = time.perf_counter() - wall0
    return stats, wall


def _fmt(v, suffix="", nd=2):
    return f"{v:.{nd}f}{suffix}" if isinstance(v, (int, float)) else "—"


async def main_async(args) -> None:
    levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    print(
        f"GPU load test → {args.url} model={args.model} stream={args.stream} "
        f"requests/level={args.requests}\n"
    )
    header = f"{'conc':>4} {'ok':>4} {'fail':>4} {'ttft_p50':>9} {'ttft_p95':>9} {'tot_p50':>8} {'tot_p95':>8} {'tok/s':>7} {'turns/min':>9}"
    print(header)
    print("-" * len(header))
    for c in levels:
        stats, wall = await _run_level(args, c)
        s = stats.summary(wall)
        print(
            f"{s['concurrency']:>4} {s['ok']:>4} {s['failed']:>4} "
            f"{_fmt(s['ttft_p50'],'s'):>9} {_fmt(s['ttft_p95'],'s'):>9} "
            f"{_fmt(s['total_p50'],'s'):>8} {_fmt(s['total_p95'],'s'):>8} "
            f"{_fmt(s['tok_s_mean'],'',1):>7} {_fmt(s['turns_per_min'],'',1):>9}"
        )
    print(
        "\nRead the ceiling where tok/s stops rising and ttft_p95/tot_p95 start "
        "climbing with concurrency — that's GPU saturation. Rising 'fail' = the "
        "circuit breaker / rate limiter shedding load (expected backpressure)."
    )


def parse_args():
    p = argparse.ArgumentParser(description="GPU-saturation load test for /api/chat")
    p.add_argument("--url", default="http://localhost:39150", help="API base URL")
    p.add_argument("--token", required=True, help="Bearer token (student token preferred)")
    p.add_argument("--model", default="snflwr.ai")
    p.add_argument("--concurrency", default="1,2,4,8", help="comma-separated levels")
    p.add_argument("--requests", type=int, default=16, help="requests per level")
    p.add_argument("--timeout", type=float, default=180.0, help="per-request timeout (s)")
    p.add_argument("--stream", action="store_true", help="use streaming (measures real TTFT)")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
