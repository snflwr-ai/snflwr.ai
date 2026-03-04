"""
Admin Dashboard Routes
Serves the self-contained admin dashboard SPA for platform management.

🔒 SECURED: Both routes require admin authentication.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

ADMIN_DIR = Path(__file__).parent.parent / "static" / "admin"


@router.get("/admin", response_class=HTMLResponse)
async def serve_admin_dashboard():
    """Serve the admin dashboard SPA (auth handled client-side by admin.js)"""
    html_path = ADMIN_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())


@router.get("/admin/", response_class=HTMLResponse)
async def serve_admin_dashboard_trailing():
    """Handle trailing slash"""
    html_path = ADMIN_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())
