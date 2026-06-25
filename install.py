#!/usr/bin/env python3
"""
snflwr.ai Interactive Installer

Thin entrypoint. The implementation lives in the ``installer`` package
(``installer/``). Run it exactly as before:

    python install.py

This file intentionally stays a runnable script at the repo root so the
docs and README (``python install.py``) keep working unchanged. All the
helper functions that used to live here now live in ``installer/`` and are
re-exported from the package, but the user-facing behavior is identical.
"""

from installer.cli import main
from installer.ui import print_error

if __name__ == "__main__":
    try:
        import sys

        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user")
        import sys

        sys.exit(1)
    except Exception as e:
        print_error(f"\nInstallation failed: {e}")
        import traceback

        traceback.print_exc()
        import sys

        sys.exit(1)
