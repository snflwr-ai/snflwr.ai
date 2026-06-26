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

| Tier | RAM / VRAM | Tutor backbone | Safety classifier | Best for |
|------|-----------|----------------|-------------------|----------|
| **Fallback** | 6–12GB RAM, no/small GPU | a small **qwen3.5** tier (`qwen3.5:4b` → `2b` → `0.8b` by RAM) | `llama-guard3:1b` | old laptops, Chromebooks |
| **Standard (default)** | 16GB+ RAM, or any GPU | **gemma4:e4b** (~10GB) | `llama-guard3:8b` | all K-12, most families & schools |
| **High-end (opt-in)** | GPU **≥26GB** VRAM | **gemma4:31b** (~19GB), via `SNFLWR_ENABLE_GEMMA_31B=true` | `llama-guard3:8b` | big single-GPU / multi-GPU servers |

Notes:
- **gemma4:e4b is the recommended default** anywhere with ≥16GB RAM or a GPU. A
  stronger-judge bake-off found **gemma4:31b ≈ gemma4:e4b on tutoring quality**
  (near-tied), so the high-end tier is about headroom on big hardware, **not** a
  quality requirement — and it roughly halves per-GPU throughput.
- The qwen tiers exist **only** as a low-RAM fallback for boxes that can't run
  gemma4:e4b. They are not the primary backbone.

---

## How model sizing works in production

`start_snflwr.sh` (and `install.py`) detect RAM/GPU and pick:

- **Tutor backbone**: `gemma4:e4b` on ≥16GB RAM or any GPU; otherwise a qwen3.5
  fallback sized to RAM. A GPU with ≥26GB VRAM **and** `SNFLWR_ENABLE_GEMMA_31B=true`
  selects `gemma4:31b`.
- **Safety classifier**: `llama-guard3:8b` when there's headroom (GPU ≥16GB VRAM,
  or ≥24GB RAM); otherwise the faster `llama-guard3:1b`. The API prefers `:8b`
  (`config.py` `SAFETY_MODEL` default) and falls back to `:1b`.

The two models stay **co-resident** so every turn (input + output both run through
the classifier) avoids per-turn reload thrash. This is why the high-end tier needs
≥26GB VRAM: `gemma4:31b` (~19GB) must fit alongside `llama-guard3:8b` (~5GB); below
that, keep `gemma4:e4b`.

### Building images with specific models

```bash
# Default / standard tier
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=gemma4:e4b \
  --build-arg SAFETY_MODEL=llama-guard3:8b \
  -t snflwr-ollama:standard .

# Low-RAM fallback tier
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=qwen3.5:4b \
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
check_input  → Safety classifier (llama-guard3:8b, or :1b on small hardware)
    ↓
snflwr.ai tutor (gemma4:e4b default · gemma4:31b on ≥26GB GPU · qwen fallback on small RAM)
    ↓
check_output → Safety classifier (same model)
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
    if base.startswith('qwen3.5'):
        return 'fallback'
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
| qwen3.5 fallback (0.8b–4b) | 1–4 GB | ~1–3 s | low-RAM only; lower tutoring quality |
| **gemma4:e4b** | ~10 GB | ~4–5 s (GPU) | default; concurrency headroom alongside the 8b guard |
| **gemma4:31b** | ~19 GB | ~8–15 s (GPU) | dense; ~half the throughput; needs ≥26GB with the guard |

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
# Change the backbone (e.g. enable the high-end tier on a ≥26GB GPU)
export SNFLWR_ENABLE_GEMMA_31B=true   # then re-run the deploy / model upgrade
./deploy.sh --upgrade model
```

All data (users, conversations, profiles) is preserved in the database — only the
Ollama model changes.

---

## Summary

1. The tutor backbone and the safety classifier are **sized to detected hardware**.
2. **Default: `gemma4:e4b` + `llama-guard3:8b`** (≥16GB RAM or any GPU).
3. **Low-RAM fallback:** a small `qwen3.5` tier + `llama-guard3:1b`.
4. **Opt-in high-end:** `gemma4:31b` on a **≥26GB** GPU (so it co-resides with the
   8b guard) — for headroom on big hardware, not better tutoring quality.
5. Same fail-closed safety pipeline and features on every tier.
6. Scale **horizontally** (more GPUs), not by enlarging the model on one card.
