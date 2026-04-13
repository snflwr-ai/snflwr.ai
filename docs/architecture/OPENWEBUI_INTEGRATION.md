# snflwr.ai — Open WebUI Integration Architecture

**Last Updated:** 2026-04-13
**Status:** Complete — proxy-based architecture

---

## Overview

snflwr.ai integrates with Open WebUI by acting as an **Ollama-compatible
proxy**. OWU is completely unmodified — its `OLLAMA_BASE_URL` points at
snflwr-api, which intercepts chat requests to run the safety pipeline before
forwarding to the real Ollama backend.

## Architecture

```
┌──────────────────────────────────────────────┐
│  Open WebUI  (unmodified, stock container)   │
│  OLLAMA_BASE_URL = http://snflwr-api:PORT    │
└──────────────┬───────────────────────────────┘
               │  Ollama API calls
┌──────────────▼───────────────────────────────┐
│  snflwr-api  — Ollama Proxy                  │
│  api/routes/ollama_proxy.py                  │
│                                              │
│  /api/chat → safety pipeline for students    │
│  /api/*    → pass-through to Ollama          │
└──────────────┬───────────────────────────────┘
               │
┌──────────────▼───────────────────────────────┐
│  Ollama  (OLLAMA_PROXY_TARGET)               │
│  Models: snflwr.ai, llama-guard3:1b          │
└──────────────────────────────────────────────┘
```

## Component Integration

### Authentication

- OWU handles all user auth (registration, login, sessions)
- OWU forwards user identity via `X-OpenWebUI-User-Id` and
  `X-OpenWebUI-User-Role` headers (requires `ENABLE_FORWARD_USER_INFO_HEADERS=true`)
- snflwr-api maps the OWU user ID to a child profile for safety decisions

### Chat Flow

1. Student sends message in OWU
2. OWU POSTs to `OLLAMA_BASE_URL/api/chat` (which is snflwr-api)
3. Proxy reads user headers → determines `admin` vs `user`
4. **Admin path**: inject `think=False`, forward to Ollama
5. **Student path**: resolve child profile → run 5-stage safety pipeline
   - Safe → forward to Ollama
   - Blocked → return Ollama-format block response directly
6. Response streams back through proxy to OWU

### Non-Chat Endpoints

All other Ollama API endpoints (`/api/tags`, `/api/show`, `/api/generate`,
`/api/embed`, `/api/pull`, etc.) are forwarded unchanged.

## Configuration

### Required Environment Variables

**Open WebUI container:**

| Variable | Value | Purpose |
|----------|-------|---------|
| `OLLAMA_BASE_URL` | `http://snflwr-api:<port>` | Route traffic through proxy |
| `ENABLE_FORWARD_USER_INFO_HEADERS` | `true` | Send user identity to proxy |

**snflwr-api container:**

| Variable | Value | Purpose |
|----------|-------|---------|
| `OLLAMA_PROXY_TARGET` | `http://ollama:11434` | Where the real Ollama lives |

### Removed Variables

These were used by the old middleware/fork approach and are no longer needed
in the OWU container:

- `SNFLWR_API_URL` — OWU no longer calls snflwr-api directly
- `INTERNAL_API_KEY` (in OWU) — no server-to-server auth needed from OWU

Note: `INTERNAL_API_KEY` is still used by snflwr-api itself for its own
internal auth middleware.

## Previous Architecture (Deprecated)

The original integration used bind-mounted forked files inside the OWU
container:

- `frontend/open-webui/backend/open_webui/routers/ollama.py` — 2000-line
  forked router
- `frontend/open-webui/backend/open_webui/middleware/snflwr.py` — custom
  middleware calling snflwr-api

These files have been deleted. The proxy-based approach eliminates the need
for any OWU modifications, making version upgrades trivial.

## File Reference

| File | Role |
|------|------|
| `api/routes/ollama_proxy.py` | Proxy router — safety + pass-through |
| `api/server.py` | Mounts proxy at `/ollama` prefix |
| `config.py` | `OLLAMA_PROXY_TARGET` config |
| `docker/compose/docker-compose.home.yml` | Home deployment |
| `docker/compose/docker-compose.yml` | Production deployment |
| `tests/test_ollama_proxy.py` | Proxy endpoint + safety tests |
| `tests/test_middleware_integration.py` | Proxy helper function tests |
| `tests/test_no_thinking_leak.py` | Structural guard for `think=False` |
