# snflwr.ai Model Structure

## Overview

snflwr.ai uses two model families:

- **Chat model** — `snflwr.ai`, the user-facing chat model. Built locally as a wrapper around a Qwen 3.5 base. Kids never see the raw qwen3.5 tag in the dropdown.
- **Safety model** (Llama Guard 3) — content safety classification in the 5-stage safety pipeline

## Models

### Chat: `snflwr.ai` (built locally, wraps a Qwen 3.5 base)

The user-facing chat model is always `snflwr.ai`. It is built at install /
deploy time by all of:

- `./deploy.sh` — home / Docker tier
- `./start_snflwr.sh` (and `start_snflwr.ps1`) — family / USB tier
- `install.py` — interactive installer
- `docker/Dockerfile.ollama` — enterprise tier (baked into the image)

Each picks a Qwen 3.5 base sized to available RAM, then runs:

```
ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
```

(with `FROM` substituted to point at the chosen base) to produce the
`snflwr.ai` ollama tag, which bundles the K-12 STEM tutor system prompt,
sampling parameters (incl. `repeat_penalty` to prevent reasoning loops),
and safety stop sequences.

### Base: Qwen 3.5 (selected by hardware detection)

| Base model | Size | RAM | Use Case |
|-------|------|-----|----------|
| `qwen3.5:0.8b` | ~0.5 GB | 2 GB+ | Low-resource devices |
| `qwen3.5:2b` | ~1.3 GB | 4 GB+ | Older laptops |
| `qwen3.5:4b` | ~2.5 GB | 6 GB+ | Everyday use |
| `qwen3.5:9b` | ~5.5 GB | 8 GB+ | Mid-range systems (default) |
| `qwen3.5:27b` | ~16 GB | 24 GB+ | Higher quality |
| `qwen3.5:35b` | ~22 GB | 32 GB+ | Server-grade |

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
  --build-arg CHAT_MODEL=qwen3.5:9b \
  --build-arg SAFETY_MODEL=llama-guard3:1b \
  -t snflwr-ollama .

# Enterprise (interactive — detects server RAM and recommends models)
enterprise/build.sh
```
