import sys
import os
import pytest

# ---------------------------------------------------------------------------
# Test database: run on plain SQLite, not SQLCipher.
# ---------------------------------------------------------------------------
# Production defaults DB_ENCRYPTION_ENABLED=true, and sqlcipher3-binary ships in
# requirements.txt, so without this the entire unit suite would run against the
# encrypted adapter. The unit tests target plain-sqlite3 semantics (exception
# types, PRAGMA results, backup/restore of a readable file) and many already try
# to opt out via monkeypatch.setenv — but system_config reads this value once at
# import time, so a per-test setenv lands too late. Set it here, before config is
# imported, so the opt-out actually takes effect. setdefault means an explicit
# DB_ENCRYPTION_ENABLED in the environment still wins. The encrypted adapter is
# covered directly by tests/test_encrypted_db_adapter.py, and the real encrypted
# init path is covered by the Database Schema Validation CI job (init_db.py).
os.environ.setdefault("DB_ENCRYPTION_ENABLED", "false")
# Rate-limit counters default to a shared SQLite file in production; keep test
# sessions hermetic (tests that need the shared store pass an explicit path).
os.environ.setdefault("SNFLWR_RATE_LIMIT_DB", "memory")

# ---------------------------------------------------------------------------
# Serving plan: give the suite a deployment that is allowed to tutor.
# ---------------------------------------------------------------------------
# core.serving_plan refuses to tutor unless the model that will be SERVED is one
# a sealed run certified, and it reads the card with nvidia-smi. CI has no GPU
# and no configured model, so every proxy test would otherwise receive the
# "tutor unavailable" block instead of a tutoring turn -- 61 of them did, which
# is how this fixture came to exist.
#
# Declaring the card is enough: with no model pinned, the floor selects the
# largest certified backbone that fits, which is the certified reference
# deployment. Deliberately NOT setting OLLAMA_DEFAULT_MODEL -- that value also
# drives which models a student may see and which model the reveal confirm
# resolves to, and pinning it here broke four unrelated tests.
# The floor itself is exercised directly, with its own stubs, in
# tests/test_serving_plan.py and tests/test_proxy_admission.py.
os.environ.setdefault("INFERENCE_VRAM_GB", "24")

# Ensure the Open WebUI backend is importable during tests
ROOT = os.path.dirname(__file__)
OPEN_WEBUI_BACKEND = os.path.join(ROOT, 'frontend', 'open-webui', 'backend')
if os.path.isdir(OPEN_WEBUI_BACKEND) and OPEN_WEBUI_BACKEND not in sys.path:
    sys.path.insert(0, OPEN_WEBUI_BACKEND)

# Also add its parent so that test helpers under `test` package are importable
OPEN_WEBUI_ROOT = os.path.join(ROOT, 'frontend', 'open-webui')
if os.path.isdir(OPEN_WEBUI_ROOT) and OPEN_WEBUI_ROOT not in sys.path:
    sys.path.insert(0, OPEN_WEBUI_ROOT)


# ---------------------------------------------------------------------------
# Optional dependency detection for skip markers
# ---------------------------------------------------------------------------
def _can_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


has_redis = _can_import('redis')
has_httpx = _can_import('httpx')
has_pydantic = _can_import('pydantic')
has_schedule = _can_import('schedule')

requires_redis = pytest.mark.skipif(not has_redis, reason="redis package not installed")
requires_httpx = pytest.mark.skipif(not has_httpx, reason="httpx package not installed")
requires_pydantic = pytest.mark.skipif(not has_pydantic, reason="pydantic package not installed")
requires_schedule = pytest.mark.skipif(not has_schedule, reason="schedule package not installed")
