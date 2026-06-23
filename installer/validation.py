"""Post-install validation checks."""

import urllib.error
import urllib.request
from pathlib import Path

from .ollama_setup import check_ollama_installed
from .ui import print_error, print_header, print_success, print_warning


def run_validation():
    """Validate installation"""
    print_header("Validating Installation")

    checks = []

    # Check .env exists
    if Path(".env").exists():
        print_success(".env file exists")
        checks.append(True)
    else:
        print_error(".env file missing")
        checks.append(False)

    # Check database connection
    try:
        from storage.database import DatabaseManager
        from storage.db_adapters import DB_ERRORS

        db = DatabaseManager()
        db.execute_query("SELECT 1")
        print_success("Database connection successful")
        checks.append(True)
    except DB_ERRORS as e:
        print_error(f"Database connection failed: {e}")
        checks.append(False)
    except ImportError as e:
        print_error(f"Database dependencies missing: {e}")
        checks.append(False)

    # Check encryption
    try:
        from storage.encryption import encryption_manager

        test_data = "test"
        encrypted = encryption_manager.encrypt(test_data)
        decrypted = encryption_manager.decrypt(encrypted)
        if decrypted == test_data:
            print_success("Encryption working")
            checks.append(True)
        else:
            print_error("Encryption validation failed — decrypt(encrypt(x)) != x")
            checks.append(False)
    except (ImportError, ValueError, OSError) as e:
        print_error(f"Encryption check failed: {e}")
        checks.append(False)

    # Check Ollama
    if check_ollama_installed():
        print_success("Ollama installed")
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
            print_success("Ollama service reachable")
        except (urllib.error.URLError, OSError):
            print_warning("Ollama service not reachable (start with: ollama serve)")
    else:
        print_warning("Ollama not installed (AI features unavailable)")

    return all(checks)
