"""
snflwr.ai middleware package for Open WebUI.

This directory is bind-mounted into the running Open WebUI container at
/app/backend/open_webui/middleware/ so that the snflwr-aware fork of
open_webui/routers/ollama.py (also bind-mounted) can resolve:

    from open_webui.middleware.snflwr import (...)

Without this __init__.py, the directory would not be recognised as a
Python package and the import would fail at startup, leaving Open WebUI
running with its stock router and the snflwr safety pipeline bypassed.
"""
