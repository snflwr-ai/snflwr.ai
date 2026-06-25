# Design: Self-hosted Langfuse Observability (enterprise)

**Date:** 2026-06-25
**Status:** Approved (pending spec review)
**Scope:** Enterprise deployment tier only

## Problem

snflwr.ai has Prometheus metrics + structured logs but no per-trace LLM
observability — no way to see latency distributions, token/cost, model behavior,
or safety-pipeline decisions grouped by session/child over time. This is robustness
backlog item #3. The hard constraint: child prompts/responses must **never** reach a
third-party cloud (COPPA/FERPA), so the tool is self-hosted **and** we send no chat
content to it at all.

## Decisions (locked during brainstorming, 2026-06-25)

| Decision | Choice | Rationale |
|---|---|---|
| Integration point | **Instrument `snflwr-api` directly** | The FastAPI proxy already sees every request, safety verdict, model latency, and incident — richest traces, no extra moving part (vs an OWUI Pipelines service). |
| Trace data | **Metadata only — no prompt/response text** | Strongest COPPA/FERPA minimization; removes any PII-redaction or content-at-rest problem entirely. |
| Deployment | **Langfuse v2, reuse `snflwr-db`** | Single `langfuse-web` container + a dedicated `langfuse` role/database in the existing Postgres (mirrors the OWUI-Postgres pattern). v3 would add ClickHouse + Redis + blob — too heavy here. |
| Trace identity | **Salted one-way hash of `profile_id` + `session_id`** | Per-child / per-session analysis without storing a raw child identifier; the hash is consistent but not reversible. |
| Tier | **Enterprise only** | Observability is an operator concern; the enterprise stack already runs Postgres + monitoring. |
| Default | **Off (`LANGFUSE_ENABLED=false`)** | Zero impact unless an operator opts in. |

## Constraints (binding)

- **No child content** ever reaches Langfuse. The instrumentation API structurally
  cannot carry prompt/response text (no parameter accepts it).
- **No raw child identifier** in traces: `profile_id` is sent only as a salted
  SHA-256 hash; exact age is bucketed to an age-band.
- **Fail-safe:** tracing must never block, slow, or error a chat turn. All trace
  calls are wrapped in try/except; the SDK flushes on a background thread. If
  Langfuse is unreachable, chat is unaffected.
- **Default-off:** with `LANGFUSE_ENABLED=false` (default), the SDK never
  initializes and there is zero added latency or dependency.
- **Enterprise tier only.** No changes to home-tier DB/observability wiring.

## Components & changes

### A. Langfuse v2 service + dedicated role/database
- New service in `docker/compose/docker-compose.yml`: `langfuse/langfuse:2`
  (`container_name: snflwr-langfuse`), on `snflwr-net`, `depends_on: postgres
  (service_healthy)`. Not exposed publicly (operator reaches it via the internal
  network / an SSH tunnel or an nginx-guarded path — exposure left to the operator,
  default internal-only).
- `enterprise/init-langfuse.sh` (mirrors `enterprise/init-openwebui.sh`): creates a
  **non-superuser** `langfuse` role (LOGIN, NOSUPERUSER NOCREATEDB NOCREATEROLE,
  password via psql `:'var'` using the `\gset`/`\if` idempotent pattern) owning a
  dedicated `langfuse` database. Mounted at
  `/docker-entrypoint-initdb.d/03-init-langfuse.sh`. The migration/role-ensure is
  also idempotent for existing clusters.
- Langfuse env: `DATABASE_URL=postgresql://langfuse:${LANGFUSE_DB_PASSWORD}@postgres:5432/langfuse`,
  `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, `TELEMETRY_ENABLED=false`,
  `LANGFUSE_DEFAULT_PROJECT_*` optional. Langfuse runs its own Prisma migrations on
  boot against the empty DB.

### B. `utils/observability.py` — metadata-only wrapper
- A thin module that lazily initializes a singleton Langfuse client **only** when
  `LANGFUSE_ENABLED` is true and keys are present; otherwise a no-op.
- Public function, e.g. `trace_chat_turn(*, model, age_band, profile_hash,
  session_id, latencies: dict, tokens: dict | None, safety: dict, blocked: bool)
  -> None`. **There is no parameter for prompt/response text.** It builds a Langfuse
  trace + a single generation/span with these fields as metadata and returns
  immediately (SDK batches/flushes in the background).
- Helpers: `_age_band(age) -> '<13'|'13-17'|'18+'|'unknown'`;
  `_hash_profile(profile_id) -> str` (HMAC-SHA256 with `LANGFUSE_HASH_SALT` or a
  per-deploy salt; truncated hex). All wrapped so any exception is swallowed and
  logged at debug.

### C. Instrument `api/routes/ollama_proxy.py::proxy_chat`
- Record per-stage timings already computed there (input-check, model forward,
  output-check, total), the resolved model, the `age` (→ age-band), `profile_id`
  (→ hash), `session_id`, and the safety verdict (category, severity, allowed/blocked,
  which layer). After the turn completes (success OR blocked), call
  `observability.trace_chat_turn(...)` inside a `try/except` that never affects the
  response.
- Both the COPPA-gated/blocked path and the normal path emit a trace (with
  `blocked=True/False`) so blocks are observable too.

### D. Config & env
- `config.py` / `.env.production.example`: `LANGFUSE_ENABLED` (default false),
  `LANGFUSE_HOST` (default `http://langfuse:3000`), `LANGFUSE_PUBLIC_KEY`,
  `LANGFUSE_SECRET_KEY`, `LANGFUSE_HASH_SALT` (operator-set), plus the
  `LANGFUSE_DB_PASSWORD` and Langfuse-service secrets. All placeholders, never real.
- `requirements.txt`: add the `langfuse` Python SDK (v2-compatible pin).

### E. Tests
- `tests/test_observability.py`:
  - With `LANGFUSE_ENABLED=false`, `trace_chat_turn` is a no-op and never imports/inits the SDK.
  - The wrapper's signature/implementation carries **no content field** — assert that a call records only the metadata keys (mock the Langfuse client, inspect the payload keys; assert no prompt/response/text key present).
  - A raised exception inside the SDK call is swallowed (tracing failure never propagates).
  - `_age_band` buckets correctly; `_hash_profile` is stable and not the raw id.
- A proxy test that a chat turn still returns 200 when `trace_chat_turn` raises.

### F. Docs
- `docs/guides/OWUI_LANGFUSE.md`: what's captured (metadata only, hashed ids),
  how to enable (`LANGFUSE_ENABLED` + keys + bring up the service), how to reach the
  UI, and the COPPA stance (no content, no raw child id, self-hosted).

## Data captured per trace (exhaustive — nothing else)
`model`, `age_band`, `profile_hash`, `session_id`, `blocked`, `safety.category`,
`safety.severity`, `safety.blocked_layer`, `latency_ms.{input_check,model,output_check,total}`,
`tokens.{prompt,completion}` (when the upstream provides them). **No prompt text, no
response text, no exact age, no raw profile_id/user_id, no email.**

## Fail-safe behavior
- `LANGFUSE_ENABLED=false` → `trace_chat_turn` returns immediately; SDK never imported.
- SDK init failure (bad keys / unreachable host) → logged once at warning; subsequent
  calls no-op.
- Per-call exception → swallowed at debug; chat response already sent or proceeds.

## Out of scope (YAGNI)
- Home tier; ClickHouse/Redis/blob (Langfuse v3); pgvector.
- Backing up the `langfuse` database (observability data is low-value for DR; the
  parameterized `backup_postgresql` could cover it later via a flag if wanted).
- Instrumenting routes beyond the chat turn (auth, profiles, admin).
- Cost dashboards beyond what Langfuse derives from token counts.

## Rollback
Set `LANGFUSE_ENABLED=false` and remove the `langfuse` service — `snflwr-api` no-ops
its tracing; nothing else depends on it. The `langfuse` database can be dropped.
