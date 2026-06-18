---
title: Guarded Upgrade Framework
last_updated: 2026-06-18
---

# Guarded Upgrade Framework

Externally-maintained components (Open WebUI, Ollama, and the tutor model
backbone) are upgraded through a **guarded** protocol: pull → snapshot →
swap → **smoke test** → keep if green, **auto-roll-back** if not. A component
upgrade can never leave the kids' app in a broken state.

```
./deploy.sh --upgrade <owui|ollama|model> [target] [--dry-run]
```

`--upgrade-owui [target]` is kept as a back-compat alias for `--upgrade owui`.

## Components

| Component | What "target" is | Default target | Smoke test |
|---|---|---|---|
| `owui` | Open WebUI image tag | latest stable release | container healthy, web UI 200, proxy auth returns models + rejects anonymous |
| `ollama` | Ollama image tag | latest stable release | `ollama --version`, the `snflwr.ai` model loads + generates on GPU, proxy round-trip |
| `model` | base model tag (e.g. `gemma4:e4b`) | **required — no default** | rebuilds the `snflwr.ai` wrapper, then runs the tutoring + safety **canary** through the real proxy |

Pinned versions live in `.env.home` as `OWU_IMAGE_TAG`, `OLLAMA_IMAGE_TAG`, and
`BASE_MODEL`; the upgrader bumps them and rolls them back on failure.

## Examples

```bash
./deploy.sh --upgrade owui                 # Open WebUI → latest stable, guarded
./deploy.sh --upgrade ollama 0.30.10       # Ollama → a specific tag
./deploy.sh --upgrade model gemma4:e4b     # rebuild the tutor wrapper on a base
./deploy.sh --upgrade owui --dry-run       # show target, change nothing
```

## How rollback works

- **owui** — restores the previous image tag **and** the pre-upgrade `webui.db`
  snapshot (a newer Open WebUI runs Alembic migrations an older image can't
  read, so the DB must be rolled back too).
- **ollama** — restores the previous image tag; models persist in the volume.
- **model** — restores the previous `snflwr.ai` wrapper from a byte-exact
  snapshot tag (`snflwr.ai:preupgrade-bak`, made with `ollama cp`).

## What the smoke tests do *not* cover

The smoke tests verify **function**, not the full safety evaluation. The proxy's
admin/student split is fail-closed (a lost user-role header degrades everyone to
student = more filtering, never less), so an upgrade can't silently weaken
safety. For a **major** version jump — especially a `model` backbone change —
also run the full quality + safety gate before production:

```bash
python -m evals.tutoring.run_eval --model snflwr.ai --base-url http://<ollama>:11434
```

## Scripts

| Script | Role |
|---|---|
| `scripts/guarded_upgrade.sh` | the framework — shared core + the three components |
| `scripts/owui_upgrade.sh` | thin back-compat shim → `guarded_upgrade.sh owui` |
| `scripts/gh_latest_release.py` | resolves the latest stable GitHub release tag for a repo |
| `scripts/model_canary.py` | the `model` smoke test — tutoring + safety prompts through the proxy |
| `scripts/owui_connect.py` | seeds the proxy bearer credential into `webui.db` (run by deploy.sh + the owui upgrade) |
