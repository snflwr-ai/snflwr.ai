# Self-hosted Langfuse Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add metadata-only LLM observability to the enterprise stack by instrumenting `snflwr-api` with the Langfuse v2 SDK and running a self-hosted Langfuse against a dedicated database in the existing Postgres.

**Architecture:** A `utils/observability.py` wrapper exposes one metadata-only function (`trace_chat_turn`) with no parameter that can carry chat text. `proxy_chat` calls it once per turn (success or block) inside a fail-safe `try/finally`, sending model/latency/token/safety-verdict/age-band/hashed-profile metadata. A `langfuse/langfuse:2` container with a dedicated non-superuser `langfuse` role+DB receives the traces. Everything is gated by `LANGFUSE_ENABLED` (default off).

**Tech Stack:** FastAPI, Langfuse v2 Python SDK, Docker Compose, PostgreSQL 16, pytest.

## Global Constraints

- **No child content** ever reaches Langfuse: `trace_chat_turn` has NO parameter accepting prompt/response text; instrumentation passes only metadata.
- **No raw child identifier**: `profile_id` is sent only as an HMAC-SHA256 hash (salt from `LANGFUSE_HASH_SALT`); exact age only as an age-band (`<13` / `13-17` / `18+` / `unknown`).
- **Fail-safe**: tracing never blocks, slows, or errors a chat turn — every trace path is wrapped in `try/except`; a tracing failure is swallowed.
- **Default-off**: `LANGFUSE_ENABLED=false` (default) → SDK never initialized; zero added latency/dependency.
- **Enterprise tier only** (`docker/compose/docker-compose.yml`); no home-tier changes.
- **Dedicated role**: the `langfuse` Postgres role is **non-superuser** (NOSUPERUSER NOCREATEDB NOCREATEROLE, LOGIN), owns only the `langfuse` database, password via psql `:'var'` (the `\gset`/`\if` idempotent pattern — NOT a `DO $$...$$` block, which doesn't substitute psql vars).
- **Branch:** `feat/langfuse-observability` (already created, spec committed). Commit per task.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `enterprise/init-langfuse.sh` | Postgres init hook: create `langfuse` role + database | Create |
| `docker/compose/docker-compose.yml` | Add `langfuse` service; pass `LANGFUSE_DB_PASSWORD` to postgres; mount init hook | Modify |
| `requirements.txt` | Add the Langfuse v2 SDK | Modify |
| `config.py` | `LANGFUSE_*` settings | Modify |
| `.env.production.example` | Document `LANGFUSE_*` knobs (placeholders) | Modify |
| `utils/observability.py` | Metadata-only Langfuse wrapper (`trace_chat_turn` + helpers) | Create |
| `tests/test_observability.py` | Unit tests for the wrapper | Create |
| `api/routes/ollama_proxy.py` | Instrument `proxy_chat` (timings + fail-safe trace emit) | Modify |
| `tests/test_proxy_observability.py` | Proxy instrumentation tests | Create |
| `docs/guides/OWUI_LANGFUSE.md` | Operator runbook | Create |

---

## Task 1: Langfuse role + database init hook

Mirror of `enterprise/init-openwebui.sh` (already on `main` via the OWUI work — read it as the reference for the exact `\gset`/`\if` pattern). Runs on fresh Postgres init; idempotent for existing clusters.

**Files:**
- Create: `enterprise/init-langfuse.sh`
- Test: a throwaway-Postgres integration check (live Docker)

**Interfaces:**
- Consumes: `LANGFUSE_DB_PASSWORD` (env, passed into the postgres container in Task 2).
- Produces: Postgres role `langfuse` (LOGIN, NOSUPERUSER) owning database `langfuse`.

- [ ] **Step 1: Read the reference and create the hook**

Read `enterprise/init-openwebui.sh` for the exact safe pattern, then create `enterprise/init-langfuse.sh`:
```bash
#!/bin/sh
# Postgres init hook (runs once on first cluster init, as the superuser).
# Creates a dedicated, non-superuser role + database for self-hosted Langfuse.
# Password comes from LANGFUSE_DB_PASSWORD and is injected via a psql variable
# (:'var'); psql does NOT substitute variables inside a DO $$...$$ block, so the
# role is created at the top level guarded by \gset/\if (idempotent).
set -eu

if [ -z "${LANGFUSE_DB_PASSWORD:-}" ]; then
    echo "init-langfuse: LANGFUSE_DB_PASSWORD is not set; refusing to create the langfuse role with an empty password." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     --set=lf_pw="$LANGFUSE_DB_PASSWORD" <<-'EOSQL'
    SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'langfuse') AS role_missing \gset
    \if :role_missing
        CREATE ROLE langfuse LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'lf_pw';
    \endif
EOSQL

if [ "$(psql -v ON_ERROR_STOP=1 -tAc "SELECT 1 FROM pg_database WHERE datname = 'langfuse'" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -c "CREATE DATABASE langfuse OWNER langfuse;"
fi

echo "init-langfuse: ensured role+database 'langfuse'."
```

- [ ] **Step 2: Make executable**

Run:
```bash
cd ~/Repos/snflwr.ai && chmod +x enterprise/init-langfuse.sh && git update-index --chmod=+x enterprise/init-langfuse.sh 2>/dev/null; ls -l enterprise/init-langfuse.sh
```
Expected: mode `-rwxr-xr-x`.

- [ ] **Step 3: Lint**

Run:
```bash
cd ~/Repos/snflwr.ai && (shellcheck enterprise/init-langfuse.sh 2>/dev/null || sh -n enterprise/init-langfuse.sh) && echo "LINT OK"
```
Expected: `LINT OK`.

- [ ] **Step 4: Live integration test (throwaway Postgres)**

Run:
```bash
cd ~/Repos/snflwr.ai && docker run --rm -d --name lf-initdb-test \
  -e POSTGRES_USER=snflwr -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=snflwr_db \
  -e LANGFUSE_DB_PASSWORD=testlfpw \
  -v "$PWD/enterprise/init-langfuse.sh:/docker-entrypoint-initdb.d/03-init-langfuse.sh:ro" \
  postgres:16.8-alpine >/dev/null
until docker exec lf-initdb-test pg_isready -U snflwr -d snflwr_db >/dev/null 2>&1; do sleep 1; done
sleep 3
echo "role:"; docker exec lf-initdb-test psql -tAc "SELECT rolname,rolsuper FROM pg_roles WHERE rolname='langfuse'" -U snflwr -d snflwr_db
echo "db:";   docker exec lf-initdb-test psql -tAc "SELECT datname FROM pg_database WHERE datname='langfuse'" -U snflwr -d snflwr_db
echo "login:"; docker exec -e PGPASSWORD=testlfpw lf-initdb-test psql -U langfuse -d langfuse -tAc "SELECT current_user"
docker rm -f lf-initdb-test >/dev/null
```
Expected: `role: langfuse|f`, `db: langfuse`, `login: langfuse`.

- [ ] **Step 5: Commit**

```bash
cd ~/Repos/snflwr.ai && git add enterprise/init-langfuse.sh
git commit -m "feat(langfuse): create dedicated non-superuser langfuse role+database"
```

---

## Task 2: Langfuse service in enterprise compose

Add the `langfuse/langfuse:2` service and wire the DB password into the postgres init.

**Files:**
- Modify: `docker/compose/docker-compose.yml`

**Interfaces:**
- Consumes: the `langfuse` role/db from Task 1; env `LANGFUSE_DB_PASSWORD`, `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`.
- Produces: a reachable Langfuse UI/API at `http://langfuse:3000` on `snflwr-net`.

- [ ] **Step 1: Pass `LANGFUSE_DB_PASSWORD` to postgres + mount the hook**

In `docker/compose/docker-compose.yml`, the `postgres` service `volumes:` already mounts `init-db.sql` (and, if the OWUI PR has merged, `02-init-openwebui.sh`). Add the langfuse hook mount and the env var. Add under `postgres` → `volumes:`:
```yaml
      - ../../enterprise/init-langfuse.sh:/docker-entrypoint-initdb.d/03-init-langfuse.sh:ro  # ordering vs other init scripts is irrelevant — independent (langfuse role/db)
```
And under `postgres` → `environment:`:
```yaml
      - LANGFUSE_DB_PASSWORD=${LANGFUSE_DB_PASSWORD}
```

- [ ] **Step 2: Add the `langfuse` service**

In `docker/compose/docker-compose.yml`, add a new service (place it after the `redis` service, before `celery-worker`):
```yaml
  # Langfuse - self-hosted LLM observability (metadata only; no child content).
  # Receives traces from snflwr-api when LANGFUSE_ENABLED=true. Internal-only by
  # default (not behind nginx) — operators reach the UI via an SSH tunnel or by
  # adding an nginx location. Runs its own Prisma migrations on first boot.
  langfuse:
    image: langfuse/langfuse:2
    container_name: snflwr-langfuse
    environment:
      - DATABASE_URL=postgresql://langfuse:${LANGFUSE_DB_PASSWORD}@postgres:5432/langfuse
      - NEXTAUTH_URL=${LANGFUSE_NEXTAUTH_URL:-http://localhost:3000}
      - NEXTAUTH_SECRET=${LANGFUSE_NEXTAUTH_SECRET}
      - SALT=${LANGFUSE_SALT}
      - ENCRYPTION_KEY=${LANGFUSE_ENCRYPTION_KEY}
      - TELEMETRY_ENABLED=false
      - LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES=false
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - snflwr-net
    expose:
      - 3000
```

- [ ] **Step 2b: Validate compose**

Run (dummy env so interpolation resolves):
```bash
cd ~/Repos/snflwr.ai && LANGFUSE_DB_PASSWORD=x LANGFUSE_NEXTAUTH_SECRET=x LANGFUSE_SALT=x LANGFUSE_ENCRYPTION_KEY=x POSTGRES_PASSWORD=x REDIS_PASSWORD=x CHAT_MODEL=qwen3.5:9b WEBUI_SECRET_KEY=x JWT_SECRET_KEY=x INTERNAL_API_KEY=x \
  docker compose -f docker/compose/docker-compose.yml config -q && echo "COMPOSE OK"
```
Expected: `COMPOSE OK`.

- [ ] **Step 3: Commit**

```bash
cd ~/Repos/snflwr.ai && git add docker/compose/docker-compose.yml
git commit -m "feat(langfuse): add self-hosted langfuse service to the enterprise stack"
```

---

## Task 3: Config, env example, and dependency

**Files:**
- Modify: `config.py`, `.env.production.example`, `requirements.txt`

**Interfaces:**
- Produces: `system_config.LANGFUSE_ENABLED` (bool), `.LANGFUSE_HOST`, `.LANGFUSE_PUBLIC_KEY`, `.LANGFUSE_SECRET_KEY`, `.LANGFUSE_HASH_SALT` (all str). Task 4's wrapper reads these.

- [ ] **Step 1: Add the Langfuse SDK to requirements**

In `requirements.txt`, after the monitoring lines (`prometheus_client==0.21.1`), add:
```
langfuse==2.60.3              # Self-hosted LLM observability (metadata-only tracing)
```
Then install it into the dev venv:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pip install "langfuse==2.60.3" -q && .venv/bin/python -c "import langfuse; print('langfuse', langfuse.__version__)"
```
Expected: prints a `2.x` version. (If 2.60.3 is unavailable, pin the latest `2.x` that resolves and note it.)

- [ ] **Step 2: Add config fields**

In `config.py`, near the other feature flags (e.g. after the `REDIS_*` block), add to the `_SystemConfig` dataclass body:
```python
    # Self-hosted Langfuse observability (enterprise). Default OFF; when on, the
    # proxy emits METADATA-ONLY traces (no chat content) — see utils/observability.py.
    LANGFUSE_ENABLED: bool = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HASH_SALT: str = os.getenv("LANGFUSE_HASH_SALT", "")
```

- [ ] **Step 3: Document the env knobs**

In `.env.production.example`, add a new section after the `# Monitoring` block (near `SENTRY_*`):
```bash
# ---------------------------------------------------------------------------
# Langfuse (self-hosted LLM observability — METADATA ONLY, no child content)
# ---------------------------------------------------------------------------
# Off by default. When enabled, snflwr-api emits traces (latency, model, token
# counts, safety verdict, age-band, hashed profile ref) to the in-stack Langfuse.
# NEVER sends prompt/response text or a raw child identifier.
LANGFUSE_ENABLED=false
LANGFUSE_HOST=http://langfuse:3000
# Project keys: create a project in the Langfuse UI, then paste its keys here.
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
# Salt for the one-way profile-id hash used to group traces by child.
# Generate with: python -c 'import secrets; print(secrets.token_hex(32))'
LANGFUSE_HASH_SALT=CHANGE-THIS-generate-with-secrets-token-hex-32
# Self-hosted Langfuse service secrets (see docker/compose/docker-compose.yml):
LANGFUSE_DB_PASSWORD=CHANGE-THIS-use-a-DIFFERENT-strong-password
LANGFUSE_NEXTAUTH_URL=http://localhost:3000
LANGFUSE_NEXTAUTH_SECRET=CHANGE-THIS-generate-with-secrets-token-hex-32
LANGFUSE_SALT=CHANGE-THIS-generate-with-secrets-token-hex-32
LANGFUSE_ENCRYPTION_KEY=CHANGE-THIS-generate-with-secrets-token-hex-32
```

- [ ] **Step 4: Verify config loads + no real secrets**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/python -c "from config import system_config; print('enabled:', system_config.LANGFUSE_ENABLED, 'host:', system_config.LANGFUSE_HOST)" && grep -n "LANGFUSE_ENABLED\|LANGFUSE_HASH_SALT" .env.production.example
```
Expected: `enabled: False host: http://langfuse:3000`; env grep shows placeholders only.

- [ ] **Step 5: Commit**

```bash
cd ~/Repos/snflwr.ai && git add requirements.txt config.py .env.production.example
git commit -m "feat(langfuse): config flags, env example, and SDK dependency"
```

---

## Task 4: `utils/observability.py` — metadata-only wrapper

**Files:**
- Create: `utils/observability.py`
- Create: `tests/test_observability.py`

**Interfaces:**
- Consumes: `system_config.LANGFUSE_*` from Task 3.
- Produces: `trace_chat_turn(*, model: str, age_band: str, profile_hash: str, blocked: bool, safety: dict, latency_ms: dict, tokens: dict | None = None) -> None`; helpers `age_band(age) -> str` and `hash_profile(profile_id) -> str`. Task 5 calls these. **No content parameter exists.**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observability.py`:
```python
"""Tests for utils/observability.py — metadata-only Langfuse wrapper."""
import inspect
from unittest.mock import MagicMock, patch


def test_trace_signature_has_no_content_param():
    """Structural guarantee: the trace API cannot carry chat text."""
    import utils.observability as obs
    params = set(inspect.signature(obs.trace_chat_turn).parameters)
    forbidden = {"text", "prompt", "response", "content", "message", "input", "output"}
    assert not (params & forbidden), f"content-bearing params leaked: {params & forbidden}"


def test_age_band_buckets():
    import utils.observability as obs
    assert obs.age_band(9) == "<13"
    assert obs.age_band(13) == "13-17"
    assert obs.age_band(17) == "13-17"
    assert obs.age_band(18) == "18+"
    assert obs.age_band(None) == "unknown"
    assert obs.age_band("oops") == "unknown"


def test_hash_profile_is_stable_and_not_raw(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HASH_SALT", "s" * 64)
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    h1 = obs.hash_profile("prof_123")
    h2 = obs.hash_profile("prof_123")
    assert h1 == h2 and h1 != "prof_123" and len(h1) >= 16


def test_disabled_is_noop_no_sdk(monkeypatch):
    """With LANGFUSE_ENABLED false, trace_chat_turn does nothing and never builds a client."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    with patch.object(obs, "_get_client") as gc:
        obs.trace_chat_turn(model="m", age_band="<13", profile_hash="h",
                            blocked=False, safety={}, latency_ms={"total": 1.0})
        gc.assert_not_called()


def test_enabled_emits_metadata_only(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)

    fake_trace = MagicMock()
    fake_client = MagicMock()
    fake_client.trace.return_value = fake_trace
    with patch.object(obs, "_get_client", return_value=fake_client):
        obs.trace_chat_turn(model="snflwr.ai", age_band="<13", profile_hash="h1",
                            blocked=True, safety={"category": "self_harm", "severity": "major"},
                            latency_ms={"input_check": 5.0, "total": 12.0},
                            tokens={"input": 10, "output": 20})

    fake_client.trace.assert_called_once()
    kwargs = fake_client.trace.call_args.kwargs
    # No content keys anywhere in the trace payload.
    blob = repr(kwargs).lower()
    for bad in ("input=", "output=", "prompt", "content", "messages"):
        assert bad not in blob, f"possible content leak via {bad}: {kwargs}"
    assert kwargs.get("user_id") == "h1"          # grouped by hashed profile
    fake_trace.generation.assert_called_once()


def test_exception_is_swallowed(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    import importlib, config, utils.observability as obs
    importlib.reload(config)
    importlib.reload(obs)
    with patch.object(obs, "_get_client", side_effect=RuntimeError("boom")):
        # Must not raise.
        obs.trace_chat_turn(model="m", age_band="unknown", profile_hash="h",
                            blocked=False, safety={}, latency_ms={"total": 1.0})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_observability.py -q --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: FAIL (`ModuleNotFoundError: utils.observability`).

- [ ] **Step 3: Implement the wrapper**

Create `utils/observability.py`:
```python
"""Metadata-only Langfuse observability for the chat proxy.

Hard rule: this module NEVER receives or sends chat content. `trace_chat_turn`
has no parameter that can carry prompt/response text. It sends only operational
metadata (latency, model, token counts, safety verdict, age-band) plus a one-way
hash of the profile id for per-child grouping. Default-off and fail-safe: any
error is swallowed so tracing can never break or slow a chat turn.
"""
import hashlib
import hmac
from typing import Optional

from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)

_client = None
_init_failed = False


def age_band(age) -> str:
    """Bucket an exact age into a coarse band (privacy-preserving)."""
    if not isinstance(age, int):
        return "unknown"
    if age < 13:
        return "<13"
    if age < 18:
        return "13-17"
    return "18+"


def hash_profile(profile_id: Optional[str]) -> str:
    """One-way HMAC-SHA256 of the profile id (salted). Stable, not reversible."""
    if not profile_id:
        return "anon"
    salt = (system_config.LANGFUSE_HASH_SALT or "snflwr-default-salt").encode()
    return hmac.new(salt, str(profile_id).encode(), hashlib.sha256).hexdigest()[:32]


def _get_client():
    """Lazily build (and memoize) the Langfuse client. Returns None if disabled
    or if keys are missing / init has already failed."""
    global _client, _init_failed
    if _init_failed:
        return None
    if _client is not None:
        return _client
    if not (
        system_config.LANGFUSE_ENABLED
        and system_config.LANGFUSE_PUBLIC_KEY
        and system_config.LANGFUSE_SECRET_KEY
    ):
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=system_config.LANGFUSE_PUBLIC_KEY,
            secret_key=system_config.LANGFUSE_SECRET_KEY,
            host=system_config.LANGFUSE_HOST,
        )
        return _client
    except Exception as exc:  # bad keys, unreachable, missing dep
        _init_failed = True
        logger.warning("Langfuse init failed; tracing disabled: %s", exc)
        return None


def trace_chat_turn(
    *,
    model: str,
    age_band: str,
    profile_hash: str,
    blocked: bool,
    safety: dict,
    latency_ms: dict,
    tokens: Optional[dict] = None,
) -> None:
    """Emit one metadata-only trace for a chat turn. Never raises.

    NOTE: there is deliberately NO parameter for prompt/response text.
    """
    if not system_config.LANGFUSE_ENABLED:
        return
    try:
        client = _get_client()
        if client is None:
            return
        trace = client.trace(
            name="chat-turn",
            user_id=profile_hash,
            metadata={
                "age_band": age_band,
                "blocked": blocked,
                "safety": safety,
            },
            tags=["blocked"] if blocked else ["allowed"],
        )
        trace.generation(
            name="tutor",
            model=model,
            usage=tokens or None,
            level="WARNING" if blocked else "DEFAULT",
            metadata={"latency_ms": latency_ms, "safety": safety},
        )
    except Exception as exc:  # fail-safe: tracing must never break chat
        logger.debug("trace_chat_turn failed (ignored): %s", exc)
```
Note the parameter named `age_band` shadows the module function `age_band` inside `trace_chat_turn`; that's fine (the function only needs the already-bucketed string here). The caller computes the band via the module helper before calling.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_observability.py -q --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: all pass.

- [ ] **Step 5: black check (utils/ is in CI scope)**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/black --check utils/observability.py && echo "BLACK OK"
```
Expected: `BLACK OK`.

- [ ] **Step 6: Commit**

```bash
cd ~/Repos/snflwr.ai && git add utils/observability.py tests/test_observability.py
git commit -m "feat(langfuse): metadata-only observability wrapper (no content, fail-safe)"
```

---

## Task 5: Instrument `proxy_chat`

Add per-turn timing + a fail-safe trace emit covering every return path. The chat path is child-safety-critical: the instrumentation must NOT change any existing behavior — it only observes.

**Files:**
- Modify: `api/routes/ollama_proxy.py`
- Create: `tests/test_proxy_observability.py`

**Interfaces:**
- Consumes: `utils.observability.trace_chat_turn` / `age_band` / `hash_profile` from Task 4.
- Produces: a trace per chat turn (success or block).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proxy_observability.py`:
```python
"""proxy_chat emits one metadata-only trace per turn and never breaks on trace errors."""
import json
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession
    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="admin", session_token="t", email="i@s")
    return app


def _body():
    return {"model": "snflwr.ai", "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def _safe():
    from safety.pipeline import SafetyResult, Severity, Category
    return SafetyResult(is_safe=True, severity=Severity.NONE, category=Category.VALID, reason="")


def _headers():
    return {"X-OpenWebUI-User-Role": "user", "X-OpenWebUI-User-Id": "stud_1"}


def test_trace_emitted_on_allowed_turn():
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "snflwr.ai", "message": {"role": "assistant", "content": "2+2=4"}, "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()),
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request", new_callable=AsyncMock, return_value=ollama_resp),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn") as tr,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    tr.assert_called_once()
    kwargs = tr.call_args.kwargs
    assert kwargs["blocked"] is False
    assert "total" in kwargs["latency_ms"]
    # no content leaked into the trace call
    assert "text" not in kwargs and "content" not in kwargs


def test_trace_emitted_on_blocked_input():
    from safety.pipeline import SafetyResult, Severity, Category
    blocked = SafetyResult(is_safe=False, severity=Severity.MAJOR, category=Category.SELF_HARM, reason="x")
    client = TestClient(_app())
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=blocked),
        patch("api.routes.ollama_proxy._record_safety_incident"),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn") as tr,
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200
    tr.assert_called_once()
    assert tr.call_args.kwargs["blocked"] is True


def test_trace_failure_does_not_break_chat():
    client = TestClient(_app())
    ollama_resp = httpx.Response(200, json={"model": "snflwr.ai", "message": {"role": "assistant", "content": "ok"}, "done": True})
    with (
        patch("api.routes.ollama_proxy._get_profile_for_user", new_callable=AsyncMock, return_value="prof_teen"),
        patch("safety.pipeline.safety_pipeline.check_input", return_value=_safe()),
        patch("safety.pipeline.safety_pipeline.check_output", return_value=_safe()),
        patch("api.routes.ollama_proxy._forward_request", new_callable=AsyncMock, return_value=ollama_resp),
        patch("api.routes.ollama_proxy.observability.trace_chat_turn", side_effect=RuntimeError("trace down")),
    ):
        resp = client.post("/api/chat", json=_body(), headers=_headers())
    assert resp.status_code == 200   # chat unaffected by a tracing error
```

- [ ] **Step 2: Run to verify they fail**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_proxy_observability.py -q --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: FAIL (`module 'api.routes.ollama_proxy' has no attribute 'observability'`).

- [ ] **Step 3: Add the import + timing + fail-safe emit**

In `api/routes/ollama_proxy.py`:

(a) Add near the top imports (after `from utils.logger import get_logger`):
```python
import time
from utils import observability
```

(b) In `proxy_chat`, immediately after the age-resolution block (right before the `# Fail CLOSED:` COPPA-gate comment), initialize the trace context and start the timer:
```python
    _t0 = time.perf_counter()
    _trace = {
        "model": model,
        "age_band": observability.age_band(age),
        "profile_hash": observability.hash_profile(profile_id),
        "blocked": True,
        "safety": {},
        "latency_ms": {},
        "tokens": None,
    }

    def _emit_trace():
        _trace["latency_ms"]["total"] = round((time.perf_counter() - _t0) * 1000, 2)
        try:
            observability.trace_chat_turn(**_trace)
        except Exception:  # belt-and-suspenders; wrapper is already fail-safe
            pass
```

(c) Wrap the remainder of the function body (from the COPPA gate through all return paths) so every `return` is preceded by `_emit_trace()`. The simplest non-invasive way that guarantees coverage of all existing `return` statements: replace each `return JSONResponse(...)` / `return Response(...)` in the student path with a two-line form that emits first. Update `_trace` before emitting at the meaningful points:
  - COPPA-gate block: set `_trace["safety"] = {"blocked_layer": "coppa"}` then `_emit_trace()` before its `return`.
  - input-safety block: `_trace["safety"] = {"category": str(result.category), "severity": str(result.severity), "blocked_layer": "input"}`; `blocked` stays True; `_emit_trace()` before `return`.
  - output-safety block (streaming + non-streaming): `_trace["safety"] = {"category": str(out_result.category), "severity": str(out_result.severity), "blocked_layer": "output"}`; `_emit_trace()` before `return`.
  - success (streaming + non-streaming): set `_trace["blocked"] = False`, `_trace["safety"] = {"blocked_layer": None}`, and (non-streaming) `_trace["tokens"] = _usage_from(upstream_json)` if present; `_emit_trace()` before `return`.
  - the 503 / unreachable returns: `_trace["safety"] = {"blocked_layer": "error"}`; `_emit_trace()` before `return`.

Add a tiny local helper for token extraction (Ollama returns `prompt_eval_count` / `eval_count` on the final message when available):
```python
    def _usage_from(payload):
        if not isinstance(payload, dict):
            return None
        pin = payload.get("prompt_eval_count")
        out = payload.get("eval_count")
        if pin is None and out is None:
            return None
        return {"input": pin or 0, "output": out or 0}
```

(The implementer: keep edits observation-only — do not alter any existing control flow, status code, or response body. Every existing `return` keeps its exact value; only a preceding `_emit_trace()` (and a `_trace[...]` update) is added.)

- [ ] **Step 4: Run the new tests + the existing proxy/COPPA suites**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_proxy_observability.py tests/test_ollama_proxy.py tests/test_coppa_chat_gate.py -q --no-cov -p no:cacheprovider --override-ini="addopts="
```
Expected: all pass (the existing proxy + COPPA tests prove no behavior change).

- [ ] **Step 5: black check**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/black --check api/routes/ollama_proxy.py && echo "BLACK OK"
```
Expected: `BLACK OK`.

- [ ] **Step 6: Commit**

```bash
cd ~/Repos/snflwr.ai && git add api/routes/ollama_proxy.py tests/test_proxy_observability.py
git commit -m "feat(langfuse): emit metadata-only trace per chat turn (fail-safe, all paths)"
```

---

## Task 6: Operator runbook

**Files:**
- Create: `docs/guides/OWUI_LANGFUSE.md`

**Interfaces:**
- Consumes: everything above. Produces: docs only.

- [ ] **Step 1: Write the runbook**

Create `docs/guides/OWUI_LANGFUSE.md`:
```markdown
# Self-hosted Langfuse Observability (Enterprise)

snflwr-api can emit **metadata-only** LLM traces to a self-hosted Langfuse running
in the enterprise stack. It is **off by default** and never sends chat content.

## What is captured (and what is NOT)
Captured: model, per-stage latency, token counts (when the model returns them),
safety verdict (category / severity / which layer blocked / allowed-or-blocked),
an **age-band** (`<13` / `13-17` / `18+`), and a **salted one-way hash** of the
profile id (for per-child grouping).
NEVER captured: prompt or response text, exact age, raw profile/user id, or email.

## Enabling
1. Set the Langfuse service secrets in `.env.production`: `LANGFUSE_DB_PASSWORD`,
   `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`
   (each `python -c 'import secrets; print(secrets.token_hex(32))'`), and
   `LANGFUSE_HASH_SALT`.
2. Bring up the service: `docker compose -f docker/compose/docker-compose.yml up -d langfuse`.
   It runs its own migrations against the dedicated `langfuse` database.
3. Open the UI (internal-only by default — e.g. `ssh -L 3000:localhost:3000 <host>`
   then visit http://localhost:3000), create an account + project, and copy the
   project's public/secret keys.
4. Put the keys in `.env.production` as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`,
   set `LANGFUSE_ENABLED=true`, and restart snflwr-api:
   `docker compose -f docker/compose/docker-compose.yml up -d snflwr-api`.

## Exposing the UI (optional)
By default Langfuse is internal-only (no nginx route). To expose it, add an nginx
`location` to `enterprise/nginx/nginx.conf` proxying to `http://langfuse:3000`, ideally
behind auth — it is an operator tool, not a parent/child surface.

## Disable / rollback
Set `LANGFUSE_ENABLED=false` and restart snflwr-api (tracing no-ops immediately).
Remove the `langfuse` service to stop it; `DROP DATABASE langfuse;` to reclaim space.

## Privacy stance
Self-hosted + metadata-only means no child content or raw identifier ever leaves
snflwr-api into the observability store, satisfying the COPPA/FERPA constraint
regardless of where Langfuse runs.
```

- [ ] **Step 2: Commit**

```bash
cd ~/Repos/snflwr.ai && git add docs/guides/OWUI_LANGFUSE.md
git commit -m "docs(langfuse): operator runbook for self-hosted observability"
```

---

## Final Verification (after all tasks)

- [ ] **Run the touched test suites**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/pytest tests/test_observability.py tests/test_proxy_observability.py tests/test_ollama_proxy.py tests/test_coppa_chat_gate.py -q --no-cov -p no:cacheprovider --override-ini="addopts=" 2>&1 | tail -3
```
Expected: all pass.

- [ ] **black (CI scope) + compose validate**

Run:
```bash
cd ~/Repos/snflwr.ai && .venv/bin/black --check api/routes/ollama_proxy.py utils/observability.py config.py && LANGFUSE_DB_PASSWORD=x LANGFUSE_NEXTAUTH_SECRET=x LANGFUSE_SALT=x LANGFUSE_ENCRYPTION_KEY=x POSTGRES_PASSWORD=x REDIS_PASSWORD=x CHAT_MODEL=qwen3.5:9b WEBUI_SECRET_KEY=x JWT_SECRET_KEY=x INTERNAL_API_KEY=x docker compose -f docker/compose/docker-compose.yml config -q && echo "ALL OK"
```
Expected: `ALL OK`.

- [ ] **shellcheck the init hook**

Run:
```bash
cd ~/Repos/snflwr.ai && (shellcheck enterprise/init-langfuse.sh 2>/dev/null || sh -n enterprise/init-langfuse.sh) && echo "SHELL OK"
```
Expected: `SHELL OK`.

- [ ] **Push + open PR**

```bash
cd ~/Repos/snflwr.ai && git push -u origin feat/langfuse-observability
gh pr create --title "Self-hosted Langfuse observability (metadata-only, enterprise)" --body "Implements docs/superpowers/specs/2026-06-25-langfuse-observability-design.md. Enterprise-only, default-off, fail-safe; metadata-only (no child content, hashed profile ref, age-band); dedicated non-superuser langfuse role+DB."
```
Then confirm CI is green before requesting review/merge.
```
```
