# Open WebUI + snflwr.ai Integration Guide

## Architecture

snflwr.ai acts as an **Ollama-compatible proxy** between Open WebUI and Ollama.
OWU's `OLLAMA_BASE_URL` points at snflwr-api instead of Ollama directly.
All Ollama API traffic flows through snflwr-api, where the safety pipeline is
enforced transparently — no forked OWU files, no bind mounts.

```
Student Browser
      │
      ▼
Open WebUI (unmodified)
      │  OLLAMA_BASE_URL = http://snflwr-api:39150
      ▼
snflwr-api  ── Ollama Proxy (/api/*)
      │
      ├─ Non-chat requests (/api/tags, /api/show, etc.)
      │     → forwarded unchanged to Ollama
      │
      ├─ /api/chat  (role = admin)
      │     → body["think"] = False, forwarded to Ollama
      │
      └─ /api/chat  (role = user / student)
            │
            ├─ Resolve student profile from OWU headers
            ├─ Extract last user message
            ├─ Run 5-stage safety pipeline
            │     ├─ Input validation
            │     ├─ Normalization
            │     ├─ Pattern matching
            │     ├─ Semantic classification (llama-guard)
            │     └─ Age gate
            │
            ├─ BLOCKED → return Ollama-format block response
            └─ SAFE    → forward to Ollama (streaming or non-streaming)
```

### Key files

| File | Purpose |
|------|---------|
| `api/routes/ollama_proxy.py` | Ollama-compatible proxy with safety pipeline |
| `api/server.py` | Mounts the proxy router at `/ollama` |
| `config.py` | `OLLAMA_PROXY_TARGET` — where the real Ollama lives |
| `docker/compose/docker-compose.home.yml` | Home deployment compose |
| `docker/compose/docker-compose.yml` | Production compose |

### Why a proxy instead of middleware?

The previous approach bind-mounted a forked `ollama.py` router and a custom
middleware package into the OWU container. This had major drawbacks:

- **Upgrade friction** — every OWU version bump required rebasing the 2000-line
  router fork against upstream changes.
- **Fragile coupling** — import paths, internal APIs, and data structures inside
  OWU could break the fork silently.
- **Two enforcement points** — safety logic lived both in the middleware and in
  snflwr-api, making it harder to reason about coverage.

The proxy approach keeps OWU completely unmodified. Safety enforcement lives
in one place (snflwr-api). OWU upgrades are a tag bump.

---

## Configuration

### Docker Compose

OWU needs two environment variables:

```yaml
environment:
  # Route all Ollama traffic through snflwr-api
  - OLLAMA_BASE_URL=http://snflwr-api:39150
  # Send user identity headers so the proxy knows who is chatting
  - ENABLE_FORWARD_USER_INFO_HEADERS=true
```

snflwr-api needs to know where the real Ollama is:

```yaml
environment:
  - OLLAMA_PROXY_TARGET=http://ollama:11434
```

### Local Development (no Docker)

```bash
# Terminal 1 — Ollama
ollama serve  # http://localhost:11434

# Terminal 2 — snflwr-api
export OLLAMA_PROXY_TARGET=http://localhost:11434
python3 -m api.server  # http://localhost:39150

# Terminal 3 — Open WebUI
export OLLAMA_BASE_URL=http://localhost:39150
# start OWU however you prefer
```

---

## User Identity

The proxy reads OWU's forwarded user headers to determine identity:

| Header | Meaning |
|--------|---------|
| `X-OpenWebUI-User-Id` | OWU user ID |
| `X-OpenWebUI-User-Role` | `admin` or `user` |

- **admin** → bypass safety, forward directly (with `think=False`)
- **user** → resolve child profile, run safety pipeline
- **missing headers** → fail closed, treated as student

---

## Testing

```bash
# Run all non-e2e tests
python3 -m pytest tests/ -m "not e2e" -o "addopts=" --ignore=tests/test_local_deployment_e2e.py

# Proxy-specific tests
python3 -m pytest tests/test_ollama_proxy.py tests/test_middleware_integration.py -v

# Structural checks (forked files deleted, thinking leak guard)
python3 -m pytest tests/test_no_thinking_leak.py -v
```

---

## Troubleshooting

### Chat returns 503

Ollama is unreachable from snflwr-api. Check `OLLAMA_PROXY_TARGET` and that
the Ollama container/process is running.

### Safety not enforced (unsafe content passes through)

1. Confirm OWU's `OLLAMA_BASE_URL` points at snflwr-api, not Ollama directly.
2. Confirm `ENABLE_FORWARD_USER_INFO_HEADERS=true` so the proxy sees user role.
3. Check snflwr-api logs for "Admin user … forwarding" — if the user is `admin`
   in OWU, safety is bypassed by design.

### OWU shows raw `<thinking>` blocks for admin users

The proxy sets `body["think"] = False` on admin requests. If you see thinking
output, verify you're running the latest `ollama_proxy.py`.
