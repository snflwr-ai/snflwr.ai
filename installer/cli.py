"""Installer orchestration: next-steps summary and the main() entrypoint."""

import os
import platform
import sys

from .config import create_env_file, initialize_database, save_credentials_file
from .dependencies import check_dependencies, ensure_venv
from .deployment import (
    setup_enterprise_deployment,
    setup_family_deployment,
    setup_security,
)
from .detection import check_python_version, check_system_requirements
from .firewall import configure_firewall
from .ollama_setup import check_ollama_installed, setup_ollama, setup_safety_model
from .platform_utils import _windows_start_cmd
from .rclone_setup import setup_offhost_backup
from .shortcuts import create_desktop_shortcut, launch_snflwr
from .ui import (
    ask_question,
    ask_yes_no,
    print_error,
    print_header,
    print_warning,
)
from .validation import run_validation


def show_next_steps(config):
    """Show next steps to user"""
    print_header("Installation Complete!")

    print("snflwr.ai is ready to use!\n")

    print("Next Steps:\n")

    system = platform.system()

    _db_type = os.environ.get("DB_TYPE", config.get("DATABASE_TYPE", "sqlite"))
    if _db_type == "sqlite":
        print("1. Start the application:")
        if system == "Windows":
            print(f"   {_windows_start_cmd()}\n")
        else:
            print("   ./start_snflwr.sh\n")

        print("2. Open snflwr.ai in your browser:")
        print("   http://localhost:3000")
        print("   (opens automatically when you start the application)\n")

        print("3. Parent Dashboard Password:")
        print("   (saved to .env file — keep this file secure!)\n")

        data_dir = os.environ.get("SNFLWR_DATA_DIR", "")
        if "SnflwrAI" in data_dir:
            print("4. Your data is stored at:")
            print(f"   {data_dir}")
            print("   Keep this USB drive safe — it contains all your data!\n")

    else:
        _pg_db = os.environ.get("POSTGRES_DATABASE", "snflwr_ai")
        print("1. Set up PostgreSQL database:")
        print(f"   createdb {_pg_db}\n")

        print("2. Run database migrations:")
        print("   python -m database.init_db\n")

        print("3. Start the application:")
        if system == "Windows":
            print(f"   {_windows_start_cmd()}\n")
        else:
            print("   ./start_snflwr.sh\n")

        print("4. Open snflwr.ai in your browser:")
        print("   http://localhost:3000\n")

    if not check_ollama_installed():
        print("\nOllama Setup (required for AI):")
        print("   Visit https://ollama.com/download")
        print("   Then run: ollama pull qwen3.5:9b\n")

    print("For Developers:")
    print("  API documentation: http://localhost:39150/docs")
    print("  Configuration: .env file\n")

    print("Security Reminders:")
    print("  Keep your .env file secure (contains secrets)")
    print("  Back up your database regularly")
    if _db_type == "sqlite":
        print("  Keep your USB drive in a safe place\n")


def main():
    """Main installer flow"""
    print_header("snflwr.ai Interactive Installer")

    print(
        """
Welcome to snflwr.ai - K-12 Safe AI Learning Platform

This installer will guide you through setting up snflwr.ai
for your specific needs. The process takes about 2 minutes.
    """
    )

    # Pre-flight checks
    if not check_python_version():
        sys.exit(1)

    # Ensure we're inside a venv (creates one and re-launches if needed)
    ensure_venv()

    # System requirements check (RAM, GPU, disk, Docker)
    total_ram_gb = check_system_requirements()

    # Firewall — ensure localhost ports are allowed before services start
    configure_firewall()

    if not check_dependencies():
        sys.exit(1)

    # Ollama setup (install, start service, pull model)
    chosen_model = setup_ollama(total_ram_gb=total_ram_gb)
    if not chosen_model:
        print_warning(
            "Ollama setup incomplete - AI features will not work until Ollama is configured"
        )
        if not ask_yes_no("Continue with the rest of the setup anyway?", default=True):
            sys.exit(1)

    # Child safety -- ask whether to download the LLM safety model
    safety_model_enabled = setup_safety_model(ollama_available=bool(chosen_model))

    # Off-host backup tooling (optional, opt-in via .env)
    setup_offhost_backup()

    # Choose deployment type
    print_header("Choose Your Deployment Type")

    print("1. Family/USB Deployment (Privacy-First)")
    print("   → Data stored on USB drive or local computer")
    print("   → Perfect for families and homeschools")
    print("   → Works 100% offline")
    print("   → Simple plug-and-play setup\n")

    print("2. Enterprise/Server Deployment (Scale)")
    print("   → Data stored on PostgreSQL server")
    print("   → Perfect for schools and institutions")
    print("   → Supports hundreds of concurrent users")
    print("   → Advanced features and analytics\n")

    choice = ask_question("Select deployment type (1 or 2)", "1")

    config = {}

    if choice == "1":
        config.update(setup_family_deployment())
    else:
        config.update(setup_enterprise_deployment())

    # Security configuration (both need this)
    config.update(setup_security())

    # Record the chosen model so startup scripts use it
    if chosen_model:
        config["OLLAMA_DEFAULT_MODEL"] = chosen_model

    # Record child safety model preference
    config["ENABLE_SAFETY_MODEL"] = safety_model_enabled

    # Create .env file
    create_env_file(config)

    # Save credentials reference file
    save_credentials_file(config)

    # Initialize database
    if not initialize_database():
        print_warning("Database initialization failed, but you can retry later")

    # Validate installation
    if run_validation():
        # Create desktop shortcut and launcher automatically
        create_desktop_shortcut()

        show_next_steps(config)

        # Offer to auto-launch
        if ask_yes_no("Start snflwr.ai now?", default=True):
            launch_snflwr()

        return 0
    else:
        print_error("\nSome validation checks failed. Please review errors above.")
        return 1
