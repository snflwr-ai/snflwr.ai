import sys
import os
import pytest

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
