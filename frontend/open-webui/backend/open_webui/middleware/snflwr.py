# open_webui/middleware/snflwr.py
"""
snflwr.ai Safety Middleware for Open WebUI
Routes all chat requests through snflwr.ai's 4-layer safety pipeline
"""

import json
import logging
import httpx
from typing import Optional, Dict, Any
from fastapi import HTTPException

log = logging.getLogger(__name__)

# snflwr.ai API configuration
# Inside Docker, "localhost" is the container — use env var to reach the host
import os
SNFLWR_API_URL = os.getenv("SNFLWR_API_URL", "http://localhost:39150")
SNFLWR_INTERNAL_KEY = os.getenv("INTERNAL_API_KEY", "snflwr-internal-dev-key")
SNFLWR_ENABLED = True  # Toggle for emergency disable

# The Open WebUI image version this middleware was tested against.
# If the container's actual version differs, log a warning at import time.
TESTED_WEBUI_VERSION = "v0.8.3"

def _check_webui_version():
    try:
        from importlib.metadata import version as pkg_version
        installed = pkg_version("open-webui")
        # Normalise: strip leading 'v' for comparison
        expected = TESTED_WEBUI_VERSION.lstrip("v")
        if installed and not installed.startswith(expected):
            log.warning(
                "snflwr.ai middleware was tested against Open WebUI %s "
                "but this container is running %s — the mounted ollama.py "
                "router may be incompatible",
                TESTED_WEBUI_VERSION, installed,
            )
    except Exception:
        pass  # importlib.metadata not available or package not installed

_check_webui_version()


async def route_through_snflwr_safety(
    user_message: str,
    profile_id: str,
    model: str = "snflwr.ai",
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Route chat message through snflwr.ai safety pipeline

    Args:
        user_message: The student's question/message
        profile_id: Child profile ID from Open WebUI user
        model: AI model to use (default: snflwr.ai)
        session_id: Optional session ID for tracking
        metadata: Optional metadata (chat_id, etc.)

    Returns:
        Dict with keys:
            - message: AI response (or block message)
            - blocked: bool indicating if content was blocked
            - block_reason: str (if blocked)
            - block_category: str (if blocked)
            - safety_metadata: dict with incident info
    """

    if not SNFLWR_ENABLED:
        # Emergency fallback - skip safety pipeline
        log.warning("snflwr.ai safety pipeline disabled - using direct Ollama")
        return None

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Call snflwr.ai API chat endpoint
            response = await client.post(
                f"{SNFLWR_API_URL}/api/chat/send",
                headers={"Authorization": f"Bearer {SNFLWR_INTERNAL_KEY}"},
                json={
                    "message": user_message,
                    "profile_id": profile_id,
                    "model": model,
                    "session_id": session_id,
                    "metadata": metadata
                }
            )

            # Handle non-200 responses
            if response.status_code == 404:
                # Profile not found - need to create child profile first
                log.error(f"Profile {profile_id} not found in snflwr.ai database")
                raise HTTPException(
                    status_code=404,
                    detail="Child profile not found. Please create a profile first."
                )
            elif response.status_code == 403:
                # Subscription issue
                data = response.json()
                return {
                    "message": data.get("message", "Subscription verification required"),
                    "blocked": True,
                    "block_reason": "subscription_required",
                    "upgrade_required": data.get("upgrade_required", False)
                }
            elif response.status_code != 200:
                # Other errors
                log.error(f"snflwr.ai API error: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"snflwr.ai API error: {response.text}"
                )

            # Parse successful response
            snflwr_data = response.json()

            log.info(
                f"snflwr.ai response: blocked={snflwr_data.get('blocked')}, "
                f"profile={profile_id}"
            )

            return snflwr_data

    except httpx.RequestError as e:
        # Connection error - snflwr.ai API not reachable
        log.error(f"Failed to connect to snflwr.ai API: {e}")
        raise HTTPException(
            status_code=503,
            detail="Safety pipeline unavailable. Please ensure snflwr.ai API is running."
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in snflwr.ai middleware: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error in safety pipeline: {str(e)}"
        )


def format_snflwr_response_for_ollama(snflwr_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert snflwr.ai API response to Ollama-compatible format

    Open WebUI expects Ollama's response format:
    {
        "model": "model-name",
        "created_at": "timestamp",
        "message": {
            "role": "assistant",
            "content": "response text"
        },
        "done": true
    }
    """

    return {
        "model": snflwr_data.get("model", "snflwr.ai"),
        "created_at": snflwr_data.get("timestamp", ""),
        "message": {
            "role": "assistant",
            "content": snflwr_data.get("message", "")
        },
        "done": True,
        # Snflwr-specific metadata
        "snflwr_blocked": snflwr_data.get("blocked", False),
        "snflwr_block_reason": snflwr_data.get("block_reason"),
        "snflwr_block_category": snflwr_data.get("block_category"),
        "snflwr_safety_metadata": snflwr_data.get("safety_metadata", {})
    }


def extract_user_message_from_payload(messages: list) -> str:
    """
    Extract the latest user message from Open WebUI's messages array

    Args:
        messages: List of message objects with 'role' and 'content'

    Returns:
        The content of the last user message
    """

    # Get the last user message
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Handle multimodal messages (text + images)
                # Extract text parts
                text_parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                return " ".join(text_parts)

    return ""


async def get_profile_id_from_user(user) -> str:
    """
    Look up the child profile ID for an Open WebUI user via snflwr.ai API.

    Makes an HTTP call to the snflwr.ai API's internal endpoint rather than
    querying the database directly — the database modules are not available
    inside the Open WebUI Docker container.

    FAIL-CLOSED: If the lookup fails for any reason, returns
    "safety_required_<user_id>" which triggers the safety pipeline in the
    router. This prevents network errors from silently bypassing child safety.

    Args:
        user: Open WebUI UserModel object

    Returns:
        Profile ID string, or "no_profile_<user_id>" only on confirmed 200
        with no profile found, or "safety_required_<user_id>" on any error.
    """
    if not hasattr(user, 'id') or not user.id:
        return "default_profile"

    user_id = str(user.id)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SNFLWR_API_URL}/api/internal/profile-for-user/{user_id}",
                headers={"Authorization": f"Bearer {SNFLWR_INTERNAL_KEY}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("profile_id", f"no_profile_{user_id}")
            # Non-200 response — fail closed
            log.error(f"Profile lookup returned {resp.status_code} for user {user_id}")
            return f"safety_required_{user_id}"
    except Exception as e:
        # FAIL CLOSED: If we can't reach the safety API, assume the user
        # might be a child and route through safety rather than bypassing it.
        log.error(f"Profile lookup failed for user {user_id} — fail closed: {e}")
        return f"safety_required_{user_id}"
