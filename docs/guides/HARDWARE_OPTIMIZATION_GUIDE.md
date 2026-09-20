---
---

# snflwr.ai - Hardware Optimization Guide

## The Challenge

Different customers have vastly different hardware:
- **Family laptop**: 8GB RAM, no GPU
- **School computer lab**: 16GB RAM, basic GPU
- **Dedicated server**: 64GB RAM + a 24GB+ GPU

Pinning one model size to all of them would either be too slow on low-end
hardware, underutilize high-end hardware, or fail to load at all. So the tutor
backbone and the safety classifier are **sized to the detected hardware** at
install time (`start_snflwr.sh` / `install.py`), and operators can override.

## The model tiers

The tutor is always the `snflwr.ai` Ollama model (a wrapper over a base model via
`models/Snflwr_AI_Kids.modelfile`). What changes by tier is the **base model** it
wraps and the **safety classifier** size.

| Tier | Requirement | Tutor | Safety classifier | Tutoring |
|------|-------------|-------|-------------------|----------|
| **Supported** | GPU with **~22 GB VRAM** or more | **`snflwr.ai-31b`** (built on `gemma4:31b`) | `llama-guard3-cpu` (CPU-pinned) | yes |
| **Everything else** | less than that, or no GPU | *(none)* | `llama-guard3-cpu` | **no — the serving plan disables it** |

**One tutor, not a ladder (2026-09-20).** The three-tier ladder this table used
to describe is gone for tutoring. Only `snflwr.ai-31b` has a sealed tutoring run,
and the quality floor refuses anything else rather than hand a child a tutor
measured as worse at its own job: 31b scored **88.8 against e4b's 80.3** overall
and cut wrong content **17 → 4** on the same 121 probes. The rest of the product
— dashboard, parent accounts, the safety pipeline — installs and runs on smaller
boxes; only tutoring is withheld.

The previous **≥26 GB** figure for 31b rested on it having to share the card with
a 5 GB guard. The guard is CPU-pinned now (`llama-guard3-cpu`, `num_gpu 0`), so
the requirement is the backbone's own measured footprint plus a reserve. Do not
restate the number here — ask the registry, which is what the running system
enforces:

```bash
python3 scripts/certified_tutor.py            # or exit 3 with the requirement
```

Notes:
- **gemma4:e4b is the recommended default** anywhere with ≥16GB RAM or a GPU.
  Judged tutoring quality is strong (a Claude-judged 48-case bake-off scored e4b
  ~89/100, humanities as well as STEM). On **average** quality, e4b ≈ 31b — but
  the difference shows up in the **reliability tail**: the small e4b backbone
  occasionally slips on multi-step mental math (e.g. botching "15% of 80"), a
  stochastic small-model limit that prompting reduces but can't fully remove. So
  average score — and it roughly halves per-GPU throughput. Think of it as the
  natural accuracy difference between hardware classes, not a bug on e4b.
  **Superseded 2026-09-20:** the throughput trade is no longer offered. 31b is
  the only certified tutor, because a cheaper tutor taught wrong content in at
  least 10% of replies. The old co-residency sizing (tutor + a ~5GB guard on one
  card → ≥26GB) is void: the guard is CPU-pinned and takes no VRAM, so a 23GB
  card serves the 31b tutor in production. Ask
  `python3 scripts/certified_tutor.py` rather than a table.
  rig opts up the same way an enterprise node does; it's hardware capability, not
  a "home vs enterprise" label.
- **There is no low-RAM fallback family any more.** The old small tiers came from
  a different model family, which meant a small box ran a *different tutor* that
  none of the persona, pedagogy or S9051B compliance work had been measured
  against. The one small in-family candidate, `gemma4:e2b`, measured **70.0 vs
  79.0** overall and **74.9 vs 81.9** on homework integrity across 3 repeat runs
  — roughly 4x the harness's own run-to-run noise. Removed 2026-09-10: an
  under-spec box is now told it is unsupported rather than quietly given the
  weakest tutor at withholding homework answers.

---

## How model sizing works in production

`start_snflwr.sh` (and `install.py`) detect RAM/GPU and pick:

- **Tutor**: there is no sizing decision any more. The install asks
  `scripts/certified_tutor.py`, which reads the same registry the serving plan
  enforces, and gets either `snflwr.ai-31b` (built on `gemma4:31b`) or a refusal
  with the requirement. Nothing smaller is offered: the floor would disable
  tutoring anyway, and a smaller tutor measured materially worse at withholding
  homework answers.
- **Safety classifier**: `llama-guard3:8b` when there's headroom (GPU ≥16GB VRAM,
  or ≥24GB RAM); otherwise the faster `llama-guard3:1b`. The API prefers `:8b`
  (`config.py` `SAFETY_MODEL` default) and falls back to `:1b`.

The tutor and the safety classifier are **not** co-resident: the classifier is
CPU-pinned (`llama-guard3-cpu`, `num_gpu 0`) because on the card it was evicted
mid-request and failed closed, blocking children on harmless questions. So the
VRAM requirement is the tutor's alone — ~22 GB for `snflwr.ai-31b` — and the
old "≥26GB co-resident" arithmetic no longer applies.

### Building images with specific models

```bash
# Default / standard tier
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=gemma4:e4b \
  --build-arg SAFETY_MODEL=llama-guard3:8b \
  -t snflwr-ollama:standard .

# Low-RAM fallback tier
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=gemma4:12b \
  --build-arg SAFETY_MODEL=llama-guard3:1b \
  -t snflwr-ollama:fallback .
```

For enterprise builds, `enterprise/build.sh --auto` detects server RAM and selects
the right base + safety model.

---

## Architecture (same pipeline, different model sizes)

Every tier runs the **same** safety pipeline and API; only the two model sizes
change:

```
User question
    ↓
check_input  → Safety classifier (llama-guard3-cpu — CPU-pinned on purpose)
    ↓
snflwr.ai tutor (snflwr.ai-31b, the only certified backbone      GPU · gemma4:12b on 14–15GB RAM)
    ↓
check_output → Safety classifier (same model, same pin)
    ↓
Response
```

Enforcement lives in the Ollama proxy (`api/routes/ollama_proxy.py`), fail-closed
on both input and output — see `docs/architecture/REQUEST_FLOW_AND_SAFETY.md`.

### Admin / parent access

Admins and parents reach the base chat model directly (no custom modelfile). No
separate educator model is required.

---

## Detecting the active tier

The base model behind `snflwr.ai` tells you the tier:

```python
import ollama

def detect_tier():
    info = ollama.show('snflwr.ai')
    base = (info.get('details', {}) or {}).get('parent_model', '') or ''
    if base.startswith('gemma4:31b'):
        return 'high-end'
    if base.startswith('gemma4:e4b'):
        return 'standard'
    if base.startswith('gemma4:12b'):
        return 'minimum'
    return 'unknown'

# Safety classifier (prefer :8b, fall back to :1b)
def detect_safety_model():
    names = [m['name'] for m in ollama.list()['models']]
    if 'llama-guard3:8b' in names:
        return 'llama-guard3:8b'
    if 'llama-guard3:1b' in names:
        return 'llama-guard3:1b'
    raise RuntimeError("Safety model not found")
```

---

## Performance characteristics

Approximate, on consumer hardware (illustrative — run `tests/load/gpu_load_test.py`
for your box):

| Backbone | VRAM (GPU) | Per-turn latency | Notes |
|----------|-----------|------------------|-------|
| `gemma4:12b` | ~7.9 GB | ~4 s | not a tutor any more; retired from tutoring 2026-09-20 |
| **gemma4:e4b** | ~10 GB | ~4–5 s (GPU) | default; concurrency headroom alongside the 8b guard |
| **`snflwr.ai-31b`** (the tutor) | ~19 GB | ~8–15 s (GPU) | the only certified tutor; needs ~22 GB VRAM (the guard is on the CPU) |

**Single-GPU throughput ceiling** (e.g. one RTX 3090 Ti): ~**13–15 tutor turns/min**,
and it **plateaus** — adding concurrency raises per-turn latency, not throughput.
Scaling is **horizontal** (more GPUs behind the nginx LB; see
`docs/deployment/SCALING_GUIDE.md`), not a bigger model or more concurrency per card.

> A GPU is the single biggest performance lever. If a GPU box is unexpectedly slow,
> check for the silent GPU→CPU fallback (`docker exec snflwr-ollama ollama ps` →
> `PROCESSOR` column); `scripts/gpu_watchdog.sh` auto-recovers it.

---

## Switching tiers

The model lives in the Ollama data volume; swap it with the guarded upgrade flow
(snapshot → swap → smoke-test → auto-rollback):

```bash
# The backbone is not a choice any more; this is how you ASK what it is
# SNFLWR_ENABLE_GEMMA_31B is gone (2026-09-20): 31b is the only certified
# tutor, not an opt-in tier. Nothing to enable. / model upgrade
./deploy.sh --upgrade model
```

All data (users, conversations, profiles) is preserved in the database — only the
Ollama model changes.

---

## Summary

1. The tutor backbone and the safety classifier are **sized to detected hardware**.
2. **The tutor: `snflwr.ai-31b` + CPU-pinned `llama-guard3-cpu`** — the only
   certified pairing. Smaller boxes run everything except tutoring.
3. **Minimum supported:** `gemma4:12b` + `llama-guard3:1b` (14–15GB RAM). Below that: unsupported, install refuses.
4. ~~Opt-in high-end tier~~ — gone 2026-09-20: 31b is the only certified tutor. (was: 31b on a ≥26GB GPU, so it co-resides with the
   8b guard) — for headroom on big hardware, not better tutoring quality.
5. Same fail-closed safety pipeline and features on every tier.
6. Scale **horizontally** (more GPUs), not by enlarging the model on one card.
