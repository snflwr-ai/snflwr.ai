"""snflwr.ai interactive installer package.

This package holds the implementation that used to live as a single
``install.py`` god-file at the repo root. ``install.py`` remains the
runnable entrypoint (``python install.py``) and is now a thin shim that
calls :func:`installer.cli.main`.

Every function that previously lived at module top level in ``install.py``
is re-exported here so that importing from ``installer`` exposes the full
original surface. Behavior is unchanged — this was a pure code move.
"""

from .cli import main, show_next_steps
from .config import create_env_file, initialize_database, save_credentials_file
from .dependencies import check_dependencies, ensure_venv
from .deployment import (
    setup_enterprise_deployment,
    setup_family_deployment,
    setup_security,
)
from .detection import (
    check_python_version,
    check_system_requirements,
    detect_existing_model,
    detect_usb_drives,
)
from .docker_setup import (
    _MIN_DOCKER_VERSION,
    _print_docker_daemon_help,
    _print_docker_smoke_help,
    _print_docker_upgrade_help,
    _try_start_docker_daemon,
    _validate_docker,
    _validate_wsl2,
)
from .firewall import (
    SNFLWR_PORTS,
    _configure_firewall_linux,
    _configure_firewall_macos,
    _configure_firewall_windows,
    configure_firewall,
)
from .ollama_setup import (
    build_snflwr_wrapper,
    check_ollama_installed,
    choose_model,
    ensure_ollama_running,
    install_ollama,
    pull_default_model,
    setup_ollama,
    setup_safety_model,
)
from .platform_utils import _is_powershell, _refresh_windows_path, _windows_start_cmd
from .rclone_setup import (
    check_rclone_installed,
    install_rclone,
    setup_offhost_backup,
)
from .shortcuts import create_desktop_shortcut, launch_snflwr
from .ui import (
    _mask_secret,
    ask_question,
    ask_yes_no,
    generate_secure_token,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)
from .validation import run_validation

__all__ = [
    # ui
    "_mask_secret",
    "print_header",
    "print_success",
    "print_error",
    "print_warning",
    "print_info",
    "ask_question",
    "ask_yes_no",
    "generate_secure_token",
    # platform_utils
    "_is_powershell",
    "_refresh_windows_path",
    "_windows_start_cmd",
    # detection
    "detect_usb_drives",
    "check_python_version",
    "check_system_requirements",
    "detect_existing_model",
    # docker_setup
    "_MIN_DOCKER_VERSION",
    "_validate_docker",
    "_validate_wsl2",
    "_try_start_docker_daemon",
    "_print_docker_daemon_help",
    "_print_docker_upgrade_help",
    "_print_docker_smoke_help",
    # firewall
    "SNFLWR_PORTS",
    "configure_firewall",
    "_configure_firewall_windows",
    "_configure_firewall_macos",
    "_configure_firewall_linux",
    # dependencies
    "check_dependencies",
    "ensure_venv",
    # ollama_setup
    "check_ollama_installed",
    "install_ollama",
    "ensure_ollama_running",
    "pull_default_model",
    "build_snflwr_wrapper",
    "choose_model",
    "setup_ollama",
    "setup_safety_model",
    # rclone_setup
    "check_rclone_installed",
    "install_rclone",
    "setup_offhost_backup",
    # deployment
    "setup_family_deployment",
    "setup_enterprise_deployment",
    "setup_security",
    # config
    "create_env_file",
    "save_credentials_file",
    "initialize_database",
    # validation
    "run_validation",
    # shortcuts
    "launch_snflwr",
    "create_desktop_shortcut",
    # cli / orchestration
    "show_next_steps",
    "main",
]
