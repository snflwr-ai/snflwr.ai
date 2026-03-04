#!/usr/bin/env python3
"""
Unified snflwr.ai Launcher — canonical cross-platform entry point.

Tries the full-featured launcher (ui.launcher) first, then falls back
to the simple service monitor (launcher.app) if dependencies are missing.

Usage:
    python launcher/main.py          # or
    python -m launcher.main
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so all imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main():
    """Launch snflwr.ai with fallback chain."""
    # Try the full-featured launcher first (handles all deploy modes
    # including thin-client manifest fetch inside the GUI thread).
    try:
        from ui.launcher import main as full_launcher_main

        full_launcher_main()
        return
    except ImportError as exc:
        print(f"Full launcher unavailable ({exc}), falling back to simple launcher...")
    except Exception as exc:
        print(f"Full launcher failed ({exc}), falling back to simple launcher...")

    # Fall back to the simple service-monitor launcher
    try:
        from launcher.app import main as simple_launcher_main

        simple_launcher_main()
        return
    except ImportError as exc:
        print(f"Simple launcher also unavailable: {exc}")
    except Exception as exc:
        print(f"Simple launcher also failed: {exc}")

    # Last resort — tell the user what happened
    print(
        "\nsnflwr.ai could not start a graphical launcher.\n"
        "Please run the startup script directly:\n"
        "  Linux/macOS: ./start_snflwr.sh\n"
        "  Windows:     START_SNFLWR.bat\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
