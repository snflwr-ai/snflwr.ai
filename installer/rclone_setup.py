"""rclone install + off-host backup setup (opt-in)."""

import platform
import shutil
import subprocess

from .platform_utils import _refresh_windows_path
from .ui import (
    ask_yes_no,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


def check_rclone_installed():
    """Check if rclone is installed (needed for off-host backups)."""
    return shutil.which("rclone") is not None


def install_rclone():
    """Install rclone based on the current platform.

    rclone is the tool scripts/backup_database.py uses to push backups
    off-host when OFFHOST_BACKUP_ENABLED=true."""
    system = platform.system()

    if system in ("Linux", "Darwin"):
        if system == "Darwin" and shutil.which("brew"):
            print_info("Installing rclone via Homebrew...")
            try:
                subprocess.run(["brew", "install", "rclone"], check=True)
                print_success("rclone installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass

        print_info("Installing rclone via official install script...")
        try:
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://rclone.org/install.sh | sudo bash"],
                check=True,
            )
            print_success("rclone installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Automatic rclone installation failed")
            print_info("Please install manually: https://rclone.org/install/")
            return False

    elif system == "Windows":
        if shutil.which("winget"):
            print_info("Installing rclone via winget...")
            try:
                subprocess.run(
                    [
                        "winget",
                        "install",
                        "Rclone.Rclone",
                        "-e",
                        "--accept-source-agreements",
                    ],
                    check=True,
                )
                _refresh_windows_path()
                print_success("rclone installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass
        print_error("Automatic rclone installation failed")
        print_info("Please download and install from: https://rclone.org/downloads/")
        return False

    else:
        print_error(f"Unsupported platform: {system}")
        print_info("Please install rclone manually: https://rclone.org/install/")
        return False


def setup_offhost_backup():
    """Optionally set up rclone for off-host backups.

    Off-host backup is opt-in (OFFHOST_BACKUP_ENABLED defaults to false). Local
    backups don't survive host failure; pushing a copy off-host does. This step
    ensures rclone is present so the operator can enable it later."""
    print_header("Off-Host Backup (optional)")
    print(
        "Local backups protect against accidental deletion and corruption, but\n"
        "not against losing the whole machine. Off-host backup pushes each\n"
        "backup to remote storage (S3/Backblaze B2/etc.) via rclone.\n"
    )

    if check_rclone_installed():
        print_success("rclone is already installed")
    else:
        if not ask_yes_no(
            "Install rclone now so off-host backup can be enabled later?",
            default=True,
        ):
            print_info(
                "Skipped. Install rclone later and set OFFHOST_BACKUP_ENABLED=true "
                "to turn on off-host backups."
            )
            return

        if not install_rclone():
            print_warning(
                "rclone not installed — off-host backup will fail closed until it is."
            )
            return

    print_info(
        "To enable off-host backup: run `rclone config` to add a remote, then set\n"
        "  OFFHOST_BACKUP_ENABLED=true\n"
        "  RCLONE_REMOTE=<remote>:<bucket/path>\n"
        "in your .env. See docs/guides/DR_RUNBOOK.md for details."
    )
