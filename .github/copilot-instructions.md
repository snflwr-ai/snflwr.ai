## Copilot / AI Coding Agent Instructions

Purpose: concise repo-specific guidance so an AI coding agent can be productive immediately.

**Big Picture**:
- **Backend (API)**: FastAPI app lives at [api/server.py](api/server.py#L1-L120). Start the server with `python -m api.server` or `uvicorn api.server:app --reload` for development. Configuration comes from `config.py` (env vars: `API_HOST`, `API_PORT`, `API_RELOAD`, `DB_TYPE`, etc.).
- **Safety pipeline**: 5-stage fail-closed pipeline in [safety/pipeline.py](safety/pipeline.py). Stages: input validation → normalization → pattern matching → semantic classification → age gate. All stages fail closed — any unhandled error blocks content. Logging via `safety/incident_logger.py` and `utils/logger.py`.
- **Models & LLM runtime**: Ollama integration is in [utils/ollama_client.py](utils/ollama_client.py#L1-L40); default host is from `config.py` (`OLLAMA_HOST`). Chat model is qwen3.5 (size chosen at install based on hardware detection). Tests and tools assume Ollama can be mocked.
- **Frontend**: Open WebUI fork at `frontend/open-webui/`. Snflwr-specific glue belongs under `frontend/snflwr-bridge/` or `api/` middleware — do NOT modify Open WebUI core files directly.

**Developer Workflows (explicit commands)**:
- Setup dev env: `python -m venv .venv && .venv\Scripts\activate` (Windows) then `pip install -r requirements-dev.txt`.
- Run API (dev): `python -m api.server` or `uvicorn api.server:app --reload --host 0.0.0.0 --port 39150`.
- Run tests: `pytest tests/ -m "not integration"` (or `pytest tests/test_safety_filters.py -v` for safety). Some tests expect SQLite by default; set `DB_TYPE=postgresql` and provision a DB for integration tests.

**Project-Specific Conventions & Important Patterns**:
- **Safety-first, fail-closed**: any unexpected error in safety code must default to rejecting content or returning safe alternatives. See `safety_pipeline.get_safe_response()` for canonical safe messaging.
- **Audit & incident logging**: use `utils/logger.py` and `safety/incident_logger.py` for safety incidents. Preserve metadata (profile_id, excerpt, severity).
- **Frontend integration rule**: place Snflwr hooks in `frontend/snflwr-bridge/` or `api/`; avoid touching `frontend/open-webui/` core files.

**Integration Points & External Dependencies**:
- Ollama: calls and retry logic in `utils/ollama_client.py`. Environment variable: `OLLAMA_HOST` (default `http://localhost:11434`).
- Storage: default SQLite path and behavior are configured in `config.py` (look for `APP_DATA_DIR`, `DB_TYPE`). For production set `DB_TYPE=postgresql` and the `POSTGRES_*` env vars.
- SMTP: parent alerts configurable via env (`SMTP_ENABLED`, `SMTP_HOST`, `SMTP_USERNAME`, etc.).

**Key Files (quick links)**:
- API entry: [api/server.py](api/server.py#L1-L120)
- Safety pipeline: [safety/pipeline.py](safety/pipeline.py)
- Ollama client: [utils/ollama_client.py](utils/ollama_client.py#L1-L40)
- Config: [config.py](config.py#L1-L40)
- Frontend notes: [frontend/README.md](frontend/README.md)

**Quick Agent Tasks & Rules**:
- When changing safety logic, add/adjust unit tests under `tests/` (see existing safety tests) and run them locally.
- Preserve fail-closed semantics: if unsure how to handle input/output, return a safe redirect using `safety_pipeline.get_safe_response()`.
- Avoid editing generated content (built models, frontend core). Add integration code in the designated bridge folders.

If you want adjustments (more examples, CI steps, or expanded run/debug recipes), tell me which section to expand.
