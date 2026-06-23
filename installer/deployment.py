"""Deployment-type configuration (family/USB, enterprise/PostgreSQL, security)."""

import secrets
from pathlib import Path

from .detection import detect_usb_drives
from .ui import (
    ask_question,
    ask_yes_no,
    generate_secure_token,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


def setup_family_deployment():
    """Set up family/USB deployment with SQLite"""
    print_header("Family/USB Deployment Setup")

    print(
        """
This mode is perfect for:
  - Individual families and homeschools
  - Privacy-focused parents who want data control
  - Offline operation (no internet required)
  - Simple plug-and-play deployment
    """
    )

    config = {}
    config["DATABASE_TYPE"] = "sqlite"

    # Detect USB drives
    usb_drives = detect_usb_drives()

    if usb_drives:
        print_info(f"Found {len(usb_drives)} removable drive(s):")
        for i, drive in enumerate(usb_drives, 1):
            print(f"  {i}. {drive}")

        use_usb = ask_yes_no(
            "\nStore data on USB drive for maximum privacy?", default=True
        )

        if use_usb:
            if len(usb_drives) == 1:
                usb_path = usb_drives[0]
            else:
                # Validate user input for drive selection
                while True:
                    choice = ask_question(f"Which drive? (1-{len(usb_drives)})", "1")
                    try:
                        choice_idx = int(choice) - 1
                        if 0 <= choice_idx < len(usb_drives):
                            usb_path = usb_drives[choice_idx]
                            break
                        else:
                            print_error(
                                f"Invalid choice. Please enter a number between 1 and {len(usb_drives)}"
                            )
                    except ValueError:
                        print_error("Invalid input. Please enter a number")

            # Create snflwr directory on USB
            snflwr_dir = usb_path / "SnflwrAI"
            try:
                snflwr_dir.mkdir(exist_ok=True)
                # Verify the drive is actually writable
                probe = snflwr_dir / ".snflwr_write_test"
                probe.write_text("ok")
                probe.unlink()
            except OSError:
                print_error(f"USB drive {usb_path} is not writable (read-only or full)")
                print_info("  Falling back to local storage instead.")
                use_usb = False

            if use_usb:
                config["SNFLWR_DATA_DIR"] = str(snflwr_dir)
                config["ENCRYPTION_KEY_PATH"] = str(snflwr_dir)
                config["LOG_PATH"] = str(snflwr_dir / "logs")
                config["USB_STORAGE"] = True

                print_success(f"Data will be stored on: {snflwr_dir}")

        if not use_usb:
            # Use local directory (user declined USB or drive was not writable)
            local_dir = Path.home() / "SnflwrAI"
            local_dir.mkdir(exist_ok=True)

            config["SNFLWR_DATA_DIR"] = str(local_dir)
            config["ENCRYPTION_KEY_PATH"] = str(local_dir)
            config["LOG_PATH"] = str(local_dir / "logs")

            print_success(f"Data will be stored locally: {local_dir}")
    else:
        print_warning("No USB drives detected")
        local_dir = Path.home() / "SnflwrAI"
        local_dir.mkdir(exist_ok=True)

        config["SNFLWR_DATA_DIR"] = str(local_dir)
        config["ENCRYPTION_KEY_PATH"] = str(local_dir)
        config["LOG_PATH"] = str(local_dir / "logs")

        print_info(f"Using local storage: {local_dir}")

    return config


def setup_enterprise_deployment():
    """Set up enterprise/PostgreSQL deployment"""
    print_header("Enterprise/Server Deployment Setup")

    print(
        """
This mode is perfect for:
  - School districts and institutions
  - Multi-user deployments (100+ students)
  - Cloud hosting platforms
  - Advanced analytics needs
    """
    )

    config = {}
    config["DATABASE_TYPE"] = "postgresql"

    # PostgreSQL configuration
    print_info("PostgreSQL Database Configuration")

    config["POSTGRES_HOST"] = ask_question("Database host", "localhost")
    config["POSTGRES_PORT"] = ask_question("Database port", "5432")
    config["POSTGRES_USER"] = ask_question("Database user", "snflwr")
    config["POSTGRES_DATABASE"] = ask_question("Database name", "snflwr_ai")

    # Password
    auto_password = ask_yes_no("Generate secure database password?", default=True)
    if auto_password:
        config["POSTGRES_PASSWORD"] = generate_secure_token()
        print_success("Secure password generated")
    else:
        config["POSTGRES_PASSWORD"] = ask_question("Database password")

    # Enterprise service credentials
    # These are needed for Docker Compose, Kubernetes, and monitoring stack
    print_info("\nGenerating credentials for enterprise services...")

    config["WEBUI_SECRET_KEY"] = generate_secure_token()
    config["REDIS_PASSWORD"] = generate_secure_token()
    config["GRAFANA_PASSWORD"] = generate_secure_token()
    config["KIBANA_ENCRYPTION_KEY"] = secrets.token_hex(16)  # 32 chars exactly
    config["FLOWER_USER"] = "admin"
    config["FLOWER_PASSWORD"] = generate_secure_token()
    config["FLOWER_ENABLED"] = True
    config["DB_ENCRYPTION_KEY"] = generate_secure_token()
    config["INTERNAL_API_KEY"] = generate_secure_token()

    print_success("Enterprise service credentials generated")
    print_info("All credentials will be saved to CREDENTIALS.md — keep this file safe!")

    return config


def setup_security():
    """Configure security settings"""
    print_header("Security Configuration")

    config = {}

    # JWT Secret
    print_info("Generating JWT secret key...")
    config["JWT_SECRET_KEY"] = generate_secure_token()
    print_success("JWT secret generated")

    # Parent Dashboard Password
    print_info("\nParent Dashboard Access")
    auto_dashboard = ask_yes_no("Generate secure dashboard password?", default=True)
    if auto_dashboard:
        config["PARENT_DASHBOARD_PASSWORD"] = generate_secure_token()
        print_success("Dashboard password generated")
        print_warning("Dashboard password generated and saved to .env file")
    else:
        config["PARENT_DASHBOARD_PASSWORD"] = ask_question("Dashboard password")

    return config
