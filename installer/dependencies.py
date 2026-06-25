"""Python dependency checks and virtual-environment bootstrapping."""

import platform
import subprocess
import sys
from pathlib import Path

from .ui import ask_yes_no, print_error, print_info, print_success, print_warning


def check_dependencies():
    """Check if required packages are installed"""
    print_info("Checking dependencies...")

    required = [
        "fastapi",
        "uvicorn",
        "argon2-cffi",
        "redis",
        "pydantic",
        "python-dotenv",
        "cryptography",
        "requests",
        "aiohttp",
        "structlog",
        "sentry-sdk",
        "psutil",
        "starlette",
    ]

    # Map package names to their import names (for packages where they differ)
    PACKAGE_IMPORT_MAP = {
        "argon2-cffi": "argon2",
        "python-dotenv": "dotenv",
        "sentry-sdk": "sentry_sdk",
    }

    missing = []
    for package in required:
        try:
            import_name = PACKAGE_IMPORT_MAP.get(package, package.replace("-", "_"))
            __import__(import_name)
        except ImportError:
            missing.append(package)

    if missing:
        print_warning(f"Missing packages: {', '.join(missing)}")
        # Install everything from requirements.txt to ensure all deps are present
        # (not just the subset we check — other modules need structlog, etc.)
        req_file = Path("requirements.txt")
        if ask_yes_no("Install all dependencies from requirements.txt?", default=True):
            try:
                if req_file.exists():
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "-q",
                            "-r",
                            str(req_file),
                        ],
                        check=True,
                    )
                else:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install"] + missing,
                        check=True,
                    )
                print_success("Dependencies installed")
                return True
            except subprocess.CalledProcessError:
                print_error("Failed to install dependencies")
                return False
        else:
            print_error("Cannot continue without dependencies")
            return False

    print_success("All dependencies installed")
    return True


def ensure_venv():
    """Create a virtual environment and re-launch inside it.

    Modern Python (3.12+ on macOS/Homebrew, Ubuntu 23.04+, Fedora 38+) enforces
    PEP 668 which forbids global pip install.  Running inside a venv avoids this
    entirely and keeps the user's system clean.
    """
    # Already inside a venv — nothing to do
    if sys.prefix != sys.base_prefix:
        return

    venv_dir = Path("venv")

    if not venv_dir.exists():
        print_info("Creating Python virtual environment...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
            print_success("Virtual environment created")
        except subprocess.CalledProcessError:
            print_error("Failed to create virtual environment")
            print_info(
                "Try: python3 -m venv venv   (you may need to install python3-venv)"
            )
            sys.exit(1)

    # Determine the venv Python path
    if platform.system() == "Windows":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    if not venv_python.exists():
        print_error(f"Virtual environment python not found at {venv_python}")
        sys.exit(1)

    print_info("Re-launching installer inside virtual environment...")
    # Re-exec this script under the venv interpreter.
    # On Windows, os.execv() does not properly inherit the console stdin,
    # which causes interactive prompts to lose user input.  Use
    # subprocess.run() instead so stdin/stdout/stderr are inherited.
    if platform.system() == "Windows":
        result = subprocess.run(
            [str(venv_python), _entry_script()] + sys.argv[1:],
        )
        sys.exit(result.returncode)
    else:
        import os

        os.execv(str(venv_python), [str(venv_python), _entry_script()] + sys.argv[1:])


def _entry_script() -> str:
    """Path to the install.py entrypoint to re-exec under the venv.

    The original code re-execed ``__file__`` (install.py at the repo root).
    After the refactor the runnable script is still install.py beside the
    ``installer`` package, so resolve it relative to the package directory.
    """
    return str(Path(__file__).resolve().parent.parent / "install.py")
