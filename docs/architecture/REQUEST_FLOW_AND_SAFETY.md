---
title: Request Flow & Safety Enforcement (current)
last_updated: 2026-06-18
---

# Request Flow & Safety Enforcement

This is the **current** student request path and where safety is enforced. It
supersedes the older fork/middleware docs (`FORK_MODIFICATIONS_FOR_K12.md`,
`OPEN_WEBUI_K12_LOCKDOWN.md`) and the Open WebUI function-filter docs, which
describe a deprecated approach.

## The path

```
Student (browser)
   │
   ▼
Open WebUI (snflwr-frontend)            ← UI only; forwards user identity headers
   │  OLLAMA_BASE_URL → snflwr-api
   ▼
snflwr-api  /api/chat  (ollama_proxy.py)   ← THE enforcement point
   │   • Bearer (INTERNAL_API_KEY) required
   │   • X-OpenWebUI-User-Role decides student vs admin
   ├── role == admin  ──────────────► forward to Ollama (safety bypassed)
   └── role == user (default) ─► SAFETY PIPELINE (input) ─► Ollama ─► SAFETY PIPELINE (output)
                                          │                                      │
                                          ▼                                      ▼
                                   gemma4 (snflwr.ai)              blocked → safe templated response
```

Key facts:
- **Tutoring answers come only from the gemma4 model** (`snflwr.ai` wrapper).
  `llama-guard` is a classifier — it never generates anything the child sees.
- **Both model-reaching paths enforce safety.** Students normally reach the model
  through the proxy (`ollama_proxy.py`), but the native `/api/chat/send` route
  (`chat.py`) runs the same safety pipeline **and** the per-child COPPA consent
  gate, so neither can be skipped by routing. The consent gate is shared between
  the two paths in `core/coppa_gate.py` (an under-13 profile tutors only once
  parental consent is verified; fail-closed). Enforcement is in the API — not in
  an Open WebUI function the student could toggle.
- **Fail-closed:** a missing/forged role header is treated as a *student*, so
  safety filtering can only increase, never decrease.
- **Admins bypass** the safety pipeline (for testing/administration). Keep admin
  accounts tightly controlled.
- The proxy also filters `/api/tags` so students see only the `snflwr.ai` model,
  not the backbone/rollback/backup variants.

## The safety pipeline (`safety/pipeline.py`)

Layered defense; first block wins; every stage fails closed.

1. **Validate** (length, type) — always on
2. **Normalize** (de-obfuscate) — always on
3. **Pattern match** (danger phrases, PII, age-adaptive) — always on
4. **Semantic classifier** (llama-guard3:8b) — *optional*; self-disables if the
   model isn't pulled (deploy.sh pulls `SAFETY_MODEL`, default `llama-guard3:8b`)
5. **Age gate** — always on

Stages 1–3 and 5 are deterministic and protect even if Ollama/the classifier is
down. `check_input` runs on the child's message; `check_output` runs on the
model's reply (catches jailbreaks that slip past input).

### Classifier availability
The classifier reports `available` / `degraded` / `disabled` (see the `/health`
endpoint's `safety_classifier`). If it is **disabled at startup** (model not
pulled, Ollama unreachable), an **operator alert** is sent (`alert_if_unavailable`)
and a 60s health probe attempts recovery. Use the **8b** model, not 1b — 1b
false-blocks benign tutoring.

## Crisis handling & escalation

- A self-harm disclosure always returns a supportive response with the **988
  Suicide & Crisis Lifeline** and "talk to a trusted adult" (non-negotiable,
  `get_safe_response`).
- Blocked student messages (input or output) call
  `_record_safety_incident()` in the proxy, which logs a DB incident and — for
  major/critical severity — queues a **parent alert** (`incident_logger`). This
  is fail-safe: a logging error never blocks the child's safe response.
- **Known limitation:** a profile-less session uses a synthetic `profile_id`
  that can't satisfy the `safety_incidents` foreign key (kept for COPPA
  cascade-delete), so the DB row is dropped; it is still captured in the file
  audit log and escalated via an operator alert. Real onboarded students have a
  valid profile, so the DB + parent-alert path works for them.

## Alerts require configuration

Operator and parent alerts only **send** when `ADMIN_EMAIL` + SMTP are
configured; otherwise they log-and-skip. Set these before production.

## Data at rest

The database is encrypted at rest with **SQLCipher (AES-256)** — the image
ships `sqlcipher3-binary` and the API requires `DB_ENCRYPTION_KEY` (generated
into `.env.home` by deploy.sh). In `ENVIRONMENT=production`, startup **fails
closed** if encryption is unavailable.
