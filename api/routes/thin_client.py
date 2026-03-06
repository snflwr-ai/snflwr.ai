"""
Thin Client Management API
Serves deployment manifests and accepts registrations from managed thin clients.

Runs on the central management server — NOT on the thin client itself.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Dict, Optional

from config import system_config
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()


# ── Response / request models ─────────────────────────────────────


class ManifestResponse(BaseModel):
    """Deployment manifest served to thin clients."""

    version: str
    config: Dict[str, str]
    launcher_version: str
    launcher_checksum: str = ""
    launcher_url: str = ""
    message: str = ""
    force_update: bool = False


class ClientRegistration(BaseModel):
    """Thin client self-registration payload."""

    hostname: str
    platform: str
    version: str


class RegistrationAck(BaseModel):
    status: str = "registered"


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/manifest", response_model=ManifestResponse)
async def get_manifest():
    """
    Serve the deployment manifest for thin clients.

    This is intentionally unauthenticated — thin clients need to fetch
    connection configuration *before* they have user credentials.
    Only non-secret connection URLs and feature flags are returned.
    """
    return ManifestResponse(
        version=system_config.VERSION,
        config={
            "OLLAMA_BASE_URL": system_config.OLLAMA_HOST,
            "API_PORT": str(system_config.API_PORT),
            "OPEN_WEBUI_URL": system_config.OPEN_WEBUI_URL,
            "BASE_URL": system_config.BASE_URL,
        },
        launcher_version=system_config.VERSION,
        message=f"Welcome to {system_config.APPLICATION_NAME}",
    )


@router.post("/register", response_model=RegistrationAck)
async def register_client(reg: ClientRegistration, request: Request):
    """
    Accept thin client self-registration.

    Logs the client for admin visibility. A future version could persist
    registrations to the database for the admin dashboard.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "Thin client registered: hostname=%r platform=%r version=%r ip=%r",
        sanitize_log_value(reg.hostname),
        sanitize_log_value(reg.platform),
        sanitize_log_value(reg.version),
        sanitize_log_value(client_ip),
    )
    return RegistrationAck(status="registered")
