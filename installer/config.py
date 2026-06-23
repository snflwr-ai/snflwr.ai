"""Write the .env file, credentials reference, and initialize the database."""

import os
import platform
from pathlib import Path

from .ui import print_error, print_info, print_success, print_warning


def create_env_file(config):
    """Create .env file with configuration"""
    print_info("Creating .env file...")

    env_path = Path(".env")

    # Backup existing .env
    if env_path.exists():
        backup_path = Path(".env.backup")
        env_path.rename(backup_path)
        print_warning(f"Backed up existing .env to {backup_path}")

    # Write new .env with secure permissions from creation
    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# snflwr.ai Configuration\n")
        f.write(
            f"# Generated on {platform.node()} at {os.path.basename(os.getcwd())}\n\n"
        )

        # Database settings
        f.write("# Database Configuration\n")
        f.write(f"DB_TYPE={config['DATABASE_TYPE']}\n")

        if config["DATABASE_TYPE"] == "sqlite":
            f.write(f"SNFLWR_DATA_DIR={config['SNFLWR_DATA_DIR']}\n")
            if "ENCRYPTION_KEY_PATH" in config:
                f.write(f"ENCRYPTION_KEY_PATH={config['ENCRYPTION_KEY_PATH']}\n")
            if "LOG_PATH" in config:
                f.write(f"LOG_PATH={config['LOG_PATH']}\n")
        else:
            f.write(f"POSTGRES_HOST={config['POSTGRES_HOST']}\n")
            f.write(f"POSTGRES_PORT={config['POSTGRES_PORT']}\n")
            f.write(f"POSTGRES_USER={config['POSTGRES_USER']}\n")
            f.write(f"POSTGRES_PASSWORD={config['POSTGRES_PASSWORD']}\n")
            f.write(f"POSTGRES_DATABASE={config['POSTGRES_DATABASE']}\n")

        # Security settings
        f.write("\n# Security Configuration\n")
        f.write(f"JWT_SECRET_KEY={config['JWT_SECRET_KEY']}\n")
        f.write(f"PARENT_DASHBOARD_PASSWORD={config['PARENT_DASHBOARD_PASSWORD']}\n")

        # Ollama model (used by startup scripts)
        if "OLLAMA_DEFAULT_MODEL" in config:
            f.write(f"\n# AI Model\n")
            f.write(f"OLLAMA_DEFAULT_MODEL={config['OLLAMA_DEFAULT_MODEL']}\n")

        # Safety model (llama-guard3:1b for semantic content classification)
        safety_val = "true" if config.get("ENABLE_SAFETY_MODEL") else "false"
        f.write(f"\n# Child Safety Model (llama-guard3:1b)\n")
        f.write(f"ENABLE_SAFETY_MODEL={safety_val}\n")

        # Enterprise service credentials (only for server deployments)
        if config["DATABASE_TYPE"] == "postgresql":
            f.write("\n# Enterprise Service Credentials\n")
            f.write(f"INTERNAL_API_KEY={config['INTERNAL_API_KEY']}\n")
            f.write(f"WEBUI_SECRET_KEY={config['WEBUI_SECRET_KEY']}\n")
            f.write(f"DB_ENCRYPTION_KEY={config['DB_ENCRYPTION_KEY']}\n")
            f.write(f"\n# Redis (required for enterprise rate limiting & caching)\n")
            f.write("REDIS_ENABLED=true\n")
            f.write(f"REDIS_PASSWORD={config['REDIS_PASSWORD']}\n")
            f.write(f"\n# Monitoring\n")
            f.write(f"GRAFANA_PASSWORD={config['GRAFANA_PASSWORD']}\n")
            f.write(f"KIBANA_ENCRYPTION_KEY={config['KIBANA_ENCRYPTION_KEY']}\n")
            f.write(f"\n# Celery Monitoring (Flower)\n")
            f.write("FLOWER_ENABLED=true\n")
            f.write(f"FLOWER_USER={config['FLOWER_USER']}\n")
            f.write(f"FLOWER_PASSWORD={config['FLOWER_PASSWORD']}\n")
            f.write("\n# Environment\n")
            f.write("ENVIRONMENT=production\n")
        else:
            # Redis disabled for local/family deployments
            f.write("\n# Redis (enable for production rate limiting & caching)\n")
            f.write("REDIS_ENABLED=false\n")

        # Optional settings
        f.write("\n# Optional Settings\n")
        f.write("# LOG_LEVEL=INFO\n")
    print_success(".env file created")


def save_credentials_file(config):
    """Save all generated credentials to a human-readable file.

    This gives admins a single reference document they can print or store
    securely (e.g. on a USB drive, in a password manager, or in a safe).
    """
    from datetime import datetime

    creds_path = Path("CREDENTIALS.md")

    with open(creds_path, "w") as f:
        f.write("# snflwr.ai — Generated Credentials\n\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Host: {platform.node()}\n\n")
        f.write("**KEEP THIS FILE SECURE.** Store it in a password manager, a safe,\n")
        f.write("or on an encrypted drive. Do NOT commit it to version control.\n\n")
        f.write("---\n\n")

        # Always present
        f.write("## snflwr.ai API\n\n")
        f.write(f"- **JWT Secret Key:** (see .env file)\n")
        f.write(f"- **Parent Dashboard Password:** (see .env file)\n\n")

        # Database
        f.write("## Database\n\n")
        if config["DATABASE_TYPE"] == "sqlite":
            f.write(f"- **Type:** SQLite\n")
            data_dir = config.get("SNFLWR_DATA_DIR", "data")
            f.write(f"- **Data Directory:** `{data_dir}`\n")
            f.write(f"- **Database:** `{data_dir}/snflwr.db`\n\n")
        else:
            f.write(f"- **Type:** PostgreSQL\n")
            f.write(
                f"- **Host:** `{config['POSTGRES_HOST']}:{config['POSTGRES_PORT']}`\n"
            )
            f.write(f"- **Database:** `{config['POSTGRES_DATABASE']}`\n")
            f.write(f"- **User:** `{config['POSTGRES_USER']}`\n")
            f.write(f"- **Password:** (see .env file)\n\n")

        # Enterprise-only credentials
        if config["DATABASE_TYPE"] == "postgresql":
            f.write("## Internal API Key\n\n")
            f.write(f"- **INTERNAL_API_KEY:** (see .env file)\n\n")

            f.write("## Open WebUI Frontend\n\n")
            f.write(f"- **WEBUI_SECRET_KEY:** (see .env file)\n\n")

            f.write("## Database Encryption\n\n")
            f.write(f"- **DB_ENCRYPTION_KEY:** (see .env file)\n\n")

            f.write("## Redis\n\n")
            f.write(f"- **Password:** (see .env file)\n\n")

            f.write("## Monitoring — Grafana\n\n")
            f.write(f"- **Username:** `admin`\n")
            f.write(f"- **Password:** (see .env file)\n")
            f.write(f"- **URL:** `http://<your-server>:3000`\n\n")

            f.write("## Monitoring — Kibana\n\n")
            f.write(f"- **Encryption Key:** (see .env file)\n\n")

            f.write("## Celery — Flower Dashboard\n\n")
            f.write(f"- **Username:** `{config['FLOWER_USER']}`\n")
            f.write(f"- **Password:** (see .env file)\n")
            f.write(f"- **URL:** `http://<your-server>:5555`\n\n")

        # Model info
        if "OLLAMA_DEFAULT_MODEL" in config:
            f.write("## AI Model\n\n")
            f.write(f"- **Model:** `{config['OLLAMA_DEFAULT_MODEL']}`\n\n")

        f.write("---\n\n")
        f.write("All of these values are also stored in the `.env` file in your\n")
        f.write("installation directory. This document is a backup reference.\n")

    os.chmod(str(creds_path), 0o600)  # Restrict to owner-only access
    print_success(f"Credentials saved to {creds_path}")

    # Also save a copy to the USB drive if the user chose USB storage
    if config.get("USB_STORAGE"):
        usb_creds_dir = Path(config["SNFLWR_DATA_DIR"])
        if usb_creds_dir.exists():
            import shutil

            usb_creds_path = usb_creds_dir / "CREDENTIALS.md"
            try:
                shutil.copy2(creds_path, usb_creds_path)
                print_success(f"Credentials also saved to USB: {usb_creds_path}")
            except OSError as e:
                print_warning(f"Could not copy credentials to USB drive: {e}")
                print_info(f"  You can manually copy CREDENTIALS.md to {usb_creds_dir}")

    print_warning(
        "Store CREDENTIALS.md somewhere safe — it contains all your passwords!"
    )


def initialize_database():
    """Initialize database schema"""
    print_info("Initializing database...")

    try:
        # Import after .env is created so config picks up the new values
        from storage.database import DatabaseManager
        from storage.db_adapters import DB_ERRORS

        db = DatabaseManager()
        db.initialize_database()
        print_success("Database initialized")
        return True

    except DB_ERRORS as e:
        print_error(f"Database initialization failed: {e}")
        return False
    except ImportError as e:
        print_error(f"Database dependencies missing: {e}")
        return False
