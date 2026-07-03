"""api.routes.ollama_proxy — OWUI→Ollama proxy with the child-safety pipeline.
Split into focused submodules; this package assembles the router and preserves
the public import/patch surface."""

from fastapi import APIRouter, Depends

# Identity-preserving re-exports for proxy_mod.<attr> access in tests
from api.middleware.auth import get_current_session  # noqa: F401
from api.routes.ollama_proxy import (  # noqa: F401
    access,
    blocks,
    guards,
    profile,
    transport,
)
from api.routes.ollama_proxy.access import (  # noqa: F401
    _filter_show_for_students,
    _filter_tags_for_students,
    _get_user_from_headers,
)
from api.routes.ollama_proxy.blocks import (  # noqa: F401
    _extract_last_user_message,
    _ollama_block_response,
)
from api.routes.ollama_proxy.chat import router as _chat_router
from api.routes.ollama_proxy.passthrough import router as _passthrough_router
from api.routes.ollama_proxy.profile import _get_profile_for_user  # noqa: F401

# Direct-import compat
from api.routes.ollama_proxy.transport import (  # noqa: F401
    _forward_request,
    _stream_chat_from_ollama,
)
from config import system_config  # noqa: F401
from utils import observability  # noqa: F401
from utils.circuit_breaker import ollama_circuit  # noqa: F401
from utils.rate_limiter import rate_limiter  # noqa: F401

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_session)])
router.include_router(_chat_router)
router.include_router(_passthrough_router)

__all__ = ["router"]
