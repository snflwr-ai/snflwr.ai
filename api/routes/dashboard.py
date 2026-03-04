"""
Parent/Admin Dashboard Routes
Serves the self-contained dashboard SPA for managing child profiles,
viewing safety alerts, and monitoring analytics.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

DASHBOARD_DIR = Path(__file__).parent.parent / "static" / "dashboard"


@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the parent/admin dashboard SPA"""
    html_path = DASHBOARD_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())


@router.get("/dashboard/", response_class=HTMLResponse)
async def serve_dashboard_trailing():
    """Handle trailing slash"""
    html_path = DASHBOARD_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())
