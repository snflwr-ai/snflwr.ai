"""Hardware / resource / environment detection."""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .docker_setup import _validate_docker
from .platform_utils import _refresh_windows_path
from .ui import (
    ask_yes_no,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)


def detect_usb_drives():
    """Detect available USB drives"""
    drives = []
    system = platform.system()

    if system == "Windows":
        # Check drive letters D-Z for removable drives
        try:
            import ctypes

            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                drive_path = f"{letter}:\\"
                if os.path.exists(drive_path):
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                    if drive_type == 2:  # Removable drive
                        drives.append(Path(drive_path))
        except (OSError, AttributeError, ValueError):
            pass

    elif system == "Darwin":  # macOS
        volumes_path = Path("/Volumes")
        if volumes_path.exists():
            for volume in volumes_path.iterdir():
                if volume.is_dir() and volume.name not in [
                    "Macintosh HD",
                    "Preboot",
                    "Recovery",
                    "VM",
                ]:
                    drives.append(volume)

    elif system == "Linux":
        # USB drives are typically at /media/<user>/<drive_label>/
        media_path = Path("/media")
        if media_path.exists():
            for user_dir in media_path.iterdir():
                if user_dir.is_dir() and os.access(user_dir, os.R_OK):
                    for mount in user_dir.iterdir():
                        if mount.is_dir():
                            drives.append(mount)
        # Also check /mnt/ for manually mounted drives (one level only)
        mnt_path = Path("/mnt")
        if mnt_path.exists():
            for mount in mnt_path.iterdir():
                if mount.is_dir() and os.access(mount, os.R_OK):
                    try:
                        if any(mount.iterdir()):
                            drives.append(mount)
                    except PermissionError:
                        pass

    return drives


def check_python_version():
    """Ensure Python 3.8+"""
    if sys.version_info < (3, 8):
        print_error(f"Python 3.8+ required. You have {sys.version}")
        return False
    print_success(f"Python {sys.version.split()[0]} detected")
    return True


def check_system_requirements():
    """Check hardware and report system readiness."""
    print_header("System Check")

    system = platform.system()

    # --- RAM ---
    total_ram_gb = None
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
            )
            total_ram_gb = int(result.stdout.strip()) / (1024**3)
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        total_ram_gb = kb / (1024**2)
                        break
        elif system == "Windows":
            result = subprocess.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                if "TotalPhysicalMemory" in line:
                    total_ram_gb = int(line.split("=")[1]) / (1024**3)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, ValueError):
        pass

    if total_ram_gb is not None:
        if total_ram_gb >= 8:
            print_success(f"RAM: {total_ram_gb:.0f} GB (8 GB minimum met)")
        else:
            print_warning(
                f"RAM: {total_ram_gb:.1f} GB — 8 GB recommended for the default model"
            )
            print_info("  The installer will offer a smaller model option later.")
    else:
        print_info("RAM: could not detect (8 GB recommended)")

    # --- GPU ---
    gpu_results = []

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            )
            cpu = result.stdout.strip()
            if "Apple" in cpu:
                gpu_results.append(
                    ("ok", f"Apple Silicon ({cpu}) — Metal GPU acceleration")
                )
            else:
                gpu_results.append(
                    ("info", f"Intel Mac ({cpu}) — CPU-only (no GPU acceleration)")
                )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            gpu_results.append(
                ("info", "macOS detected — Metal GPU acceleration likely available")
            )

    elif system in ("Linux", "Windows"):
        # Grab lspci once for hardware-level detection
        lspci_lines = []
        try:
            result = subprocess.run(["lspci"], capture_output=True, text=True)
            if result.returncode == 0:
                lspci_lines = [
                    line
                    for line in result.stdout.splitlines()
                    if any(k in line.lower() for k in ("vga", "3d", "display"))
                ]
        except FileNotFoundError:
            pass

        # --- NVIDIA ---
        nvidia_found = False
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
            )
            # nvidia-smi --query-gpu sends errors to stdout, not stderr
            output = ((result.stdout or "") + " " + (result.stderr or "")).lower()
            if result.returncode == 0 and result.stdout.strip():
                for name in result.stdout.strip().splitlines():
                    gpu_results.append(
                        ("ok", f"NVIDIA {name.strip()} — CUDA GPU acceleration")
                    )
                nvidia_found = True
            elif "version mismatch" in output:
                gpu_results.append(
                    (
                        "warn",
                        "NVIDIA GPU found but driver/library version mismatch — a reboot usually fixes this",
                    )
                )
                nvidia_found = True
            elif "not found" in output or "no devices" in output:
                gpu_results.append(
                    (
                        "warn",
                        "NVIDIA driver installed but no GPU devices found — check hardware connection",
                    )
                )
                nvidia_found = True
        except FileNotFoundError:
            pass

        # Fall back to lspci for NVIDIA hardware the driver tools can't see
        if not nvidia_found:
            for line in lspci_lines:
                if "nvidia" in line.lower():
                    name = line.split(": ", 1)[-1] if ": " in line else line
                    gpu_results.append(
                        (
                            "warn",
                            f"{name.strip()} — detected but nvidia-smi unavailable, drivers may need installing",
                        )
                    )
                    nvidia_found = True

        # --- AMD ---
        amd_found = False
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    # Only parse "Card series" or "Card model" lines for the GPU name
                    if "Card series" in line or "Card model" in line:
                        name = line.split(":", 2)[-1].strip()
                        if name:
                            gpu_results.append(
                                ("ok", f"AMD {name} — ROCm GPU acceleration")
                            )
                            amd_found = True
        except FileNotFoundError:
            pass

        # Fall back to lspci for AMD hardware
        if not amd_found:
            for line in lspci_lines:
                if "amd" in line.lower() or "radeon" in line.lower():
                    name = line.split(": ", 1)[-1] if ": " in line else line
                    gpu_results.append(
                        (
                            "info",
                            f"{name.strip()} — ROCm drivers may need installing for GPU acceleration",
                        )
                    )
                    amd_found = True

        # --- Intel ---
        for line in lspci_lines:
            if "intel" in line.lower():
                name = line.split(": ", 1)[-1] if ": " in line else line
                gpu_results.append(("info", f"{name.strip()} — integrated GPU"))

    if gpu_results:
        for level, msg in gpu_results:
            if level == "ok":
                print_success(f"GPU: {msg}")
            elif level == "warn":
                print_warning(f"GPU: {msg}")
            else:
                print_info(f"GPU: {msg}")
    else:
        print_info("GPU: No GPU detected — CPU-only (slower, but works fine)")

    # --- Disk space ---
    try:
        import shutil as _shutil

        usage = _shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024**3)
        # Minimum realistic need: ~2 GB (smallest model + deps + db)
        # Default model (8B): ~6 GB total. 8 GB free is comfortable.
        if free_gb >= 8:
            print_success(f"Disk: {free_gb:.0f} GB free")
        elif free_gb >= 3:
            print_info(f"Disk: {free_gb:.1f} GB free — enough for smaller models")
        else:
            print_warning(
                f"Disk: {free_gb:.1f} GB free — may be tight (smallest model needs ~2 GB)"
            )
    except (OSError, ValueError):
        pass

    # --- macOS: Xcode Command Line Tools (needed for C extensions) ---
    if system == "Darwin":
        xcode_clt_ok = False
        try:
            result = subprocess.run(
                ["xcode-select", "-p"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                xcode_clt_ok = True
                print_success("Xcode CLT: installed (C compiler available)")
        except FileNotFoundError:
            pass

        if not xcode_clt_ok:
            # Also check if gcc/clang is available (might be from Homebrew)
            if shutil.which("gcc") or shutil.which("clang"):
                print_success("C compiler: available (gcc/clang found)")
            else:
                print_warning("Xcode Command Line Tools: not installed")
                print_info(
                    "  Required to compile native Python packages (argon2-cffi, aiohttp, etc.)"
                )
                if ask_yes_no("Install Xcode Command Line Tools now?", default=True):
                    print_info(
                        "Opening Xcode CLT installer (follow the on-screen dialog)..."
                    )
                    try:
                        subprocess.run(["xcode-select", "--install"], check=False)
                        print()
                        print_info(
                            "Waiting for Xcode Command Line Tools installation..."
                        )
                        print_info(
                            "A system dialog should appear. Click 'Install' and wait for it to finish."
                        )
                        print_info(
                            "Press Enter here once the installation is complete."
                        )
                        input()
                        # Verify it worked
                        verify = subprocess.run(
                            ["xcode-select", "-p"],
                            capture_output=True,
                            text=True,
                        )
                        if verify.returncode == 0:
                            print_success("Xcode Command Line Tools installed")
                        else:
                            print_warning(
                                "Xcode CLT installation may not have completed"
                            )
                            print_info(
                                "  You can retry manually: xcode-select --install"
                            )
                    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                        print_warning(f"Could not launch Xcode CLT installer: {e}")
                        print_info("  Install manually: xcode-select --install")
                else:
                    print_warning(
                        "Some Python packages may fail to install without a C compiler"
                    )
                    print_info("  Install later: xcode-select --install")

    # --- Docker (required for Open WebUI chat interface) ---
    docker_found = shutil.which("docker") is not None
    if docker_found:
        print_success("Docker: installed")
    else:
        print_error("Docker: not installed (REQUIRED)")
        print_info(
            "  snflwr.ai uses Open WebUI as its chat interface, which requires Docker."
        )
        print()

        # Try to auto-install Docker on each platform
        installed = False

        if system == "Windows" and shutil.which("winget"):
            if ask_yes_no("Install Docker Desktop via winget?", default=True):
                try:
                    subprocess.run(
                        [
                            "winget",
                            "install",
                            "Docker.DockerDesktop",
                            "-e",
                            "--accept-source-agreements",
                            "--accept-package-agreements",
                        ],
                        check=True,
                    )
                    _refresh_windows_path()
                    if shutil.which("docker"):
                        print_success("Docker Desktop installed")
                        installed = True
                    else:
                        print_success(
                            "Docker Desktop installed (restart may be required for PATH)"
                        )
                        print_info(
                            "  Docker will be available after you restart your computer."
                        )
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker Desktop via winget")

        elif system == "Darwin" and shutil.which("brew"):
            if ask_yes_no("Install Docker Desktop via Homebrew?", default=True):
                try:
                    subprocess.run(
                        ["brew", "install", "--cask", "docker"],
                        check=True,
                    )
                    if shutil.which("docker"):
                        print_success("Docker Desktop installed")
                        installed = True
                    else:
                        print_success("Docker Desktop installed")
                        print_info(
                            "  Open Docker Desktop from Applications to complete setup."
                        )
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker Desktop via Homebrew")

        elif system == "Linux":
            if ask_yes_no(
                "Install Docker via the official install script?", default=True
            ):
                try:
                    print_info("Downloading and running Docker install script...")
                    subprocess.run(
                        ["bash", "-c", "curl -fsSL https://get.docker.com | sh"],
                        check=True,
                    )
                    # Add current user to docker group so they don't need sudo
                    username = os.environ.get("USER", os.environ.get("LOGNAME", ""))
                    if username:
                        subprocess.run(
                            ["sudo", "usermod", "-aG", "docker", username],
                            check=False,
                        )
                        print_info(f"  Added '{username}' to the docker group.")
                        print_info(
                            "  You may need to log out and back in for this to take effect."
                        )
                    if shutil.which("docker"):
                        print_success("Docker installed")
                        installed = True
                    else:
                        print_success(
                            "Docker installed (PATH update may require a new terminal)"
                        )
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker via install script")
                    print_info("  You may need to run the installer with sudo.")

        if not installed:
            print()
            print_info("  Install Docker Desktop manually before continuing:")
            if system == "Darwin":
                print_info(
                    "    https://docs.docker.com/desktop/setup/install/mac-install/"
                )
            elif system == "Windows":
                print_info(
                    "    https://docs.docker.com/desktop/setup/install/windows-install/"
                )
            else:
                print_info("    https://docs.docker.com/desktop/setup/install/linux/")
                print_info("    Or: curl -fsSL https://get.docker.com | sh")
            print()
            print_error(
                "Docker is required. Install it and re-run:  python3 install.py"
            )
            sys.exit(1)

    # Deep-validate Docker: binary on PATH is necessary but not sufficient.
    # Verify the daemon, runtime, and compose actually work.
    if shutil.which("docker"):
        if not _validate_docker(system):
            print()
            if not ask_yes_no(
                "Docker validation failed. Continue setup anyway?", default=False
            ):
                print_error(
                    "Fix the Docker issues above and re-run:  python3 install.py"
                )
                sys.exit(1)
            print_warning("Continuing — Open WebUI may not work until Docker is fixed")

    print()
    return total_ram_gb


def detect_existing_model() -> str:
    """Check if Ollama already has a model pulled. Returns the model tag or ''."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines()[1:]:  # skip header row
            parts = line.split()
            if parts:
                return parts[0]  # return the first available model
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass
    return ""
