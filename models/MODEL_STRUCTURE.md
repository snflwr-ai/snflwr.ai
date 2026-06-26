# snflwr.ai Model Structure

## Overview

snflwr.ai uses two model families:

- **Chat model** — `snflwr.ai`, the user-facing chat model. Built locally as a wrapper around a base model (`gemma4:e4b` by default on 16 GB+ systems; Qwen 3.5 tiers as fallback). Kids never see the raw base-model tag in the dropdown.
- **Safety model** (Llama Guard 3) — content safety classification in the 5-stage safety pipeline

## Models

### Chat: `snflwr.ai` (built locally, wraps a base model)

The user-facing chat model is always `snflwr.ai`. It is built at install /
deploy time by all of:

- `./deploy.sh` — home / Docker tier
- `./start_snflwr.sh` (and `start_snflwr.ps1`) — family / USB tier
- `install.py` — interactive installer
- `docker/Dockerfile.ollama` — enterprise tier (baked into the image)

Each picks a base model sized to available RAM (`gemma4:e4b` on 16 GB+ systems, otherwise a Qwen 3.5 tier), then runs:

```
ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
```

(with `FROM` substituted to point at the chosen base) to produce the
`snflwr.ai` ollama tag, which bundles the K-12 STEM tutor system prompt,
sampling parameters (incl. `repeat_penalty` to prevent reasoning loops),
and safety stop sequences.

### Base model (selected by hardware detection)

| Base model | Size | RAM | Use Case |
|-------|------|-----|----------|
| `gemma4:e4b` | ~10 GB | 16 GB+ | **Default — recommended backbone** |
| `qwen3.5:4b` | ~2.5 GB | 8 GB+ | Fallback (gemma too large) |
| `qwen3.5:2b` | ~1.3 GB | 6 GB+ | Fallback (older laptops) |
| `qwen3.5:0.8b` | ~0.5 GB | 2 GB+ | Fallback (low-resource) |

### Safety: Llama Guard 3 (configurable via `SAFETY_MODEL` build arg)

| Model | Size | RAM | Use Case |
|-------|------|-----|----------|
| **`llama-guard3:8b`** | ~4.9 GB | +8 GB | Enterprise (higher accuracy) |
| `llama-guard3:1b` | ~1 GB | +2 GB | Home use (faster) |

## Profile -> Model Mapping

| Profile | Model | Notes |
|---------|-------|-------|
| **Student** | `snflwr.ai` | K-12 STEM tutor persona, age-adaptive, full safety pipeline |
| **Admin/Parent** (enterprise only) | `${CHAT_MODEL}` (raw base) | Lets admins use the model without the K-12 persona constraints |
| **Home tier (all users)** | `snflwr.ai` | No admin/student split — everyone gets the wrapper |
| **Safety (all)** | `llama-guard3:8b` / `:1b` | Stage 4 of safety pipeline |

## Files

```
models/
  Snflwr_AI_Kids.modelfile   # Student tutor persona (the only modelfile)
  MODEL_STRUCTURE.md            # This file
```

## Building

```bash
# Both build args are required — choose based on hardware:
docker build -f docker/Dockerfile.ollama \
  --build-arg CHAT_MODEL=gemma4:e4b \
  --build-arg SAFETY_MODEL=llama-guard3:1b \
  -t snflwr-ollama .

# Enterprise (interactive — detects server RAM and recommends models)
enterprise/build.sh
```
