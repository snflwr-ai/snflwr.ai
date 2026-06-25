"""Deep validation and remediation for Docker / WSL 2 / Compose."""

import os
import shutil
import subprocess
import time

from .ui import print_error, print_info, print_success, print_warning


# Minimum Docker versions: client 20.10+ (compose v2 plugin support),
# server API 1.41+ (compose spec).  These shipped in late 2020.
_MIN_DOCKER_VERSION = (20, 10)


def _validate_docker(system: str):
    """Deep-validate that Docker is actually functional, not just on PATH.

    Checks, in order:
      1. Windows-only: WSL 2 is installed and the correct version.
      2. Docker daemon is reachable (``docker info``).
      3. Docker client/server version is recent enough.
      4. Docker Compose v2 plugin is available.
      5. Smoke test: ``docker run --rm hello-world`` proves the full
         pull → create → start → remove lifecycle works.

    On failure, prints specific diagnostics and remediation steps.
    Returns True if Docker is healthy, False otherwise (the caller
    decides whether to abort or continue).
    """
    print()
    print_info("Validating Docker installation...")

    # ── 1. Windows: WSL 2 pre-check ──────────────────────────────────
    if system == "Windows":
        if not _validate_wsl2():
            # _validate_wsl2 already printed remediation steps
            return False

    # ── 2. Docker daemon reachable ────────────────────────────────────
    daemon_ok = False
    docker_info_stderr = ""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        daemon_ok = result.returncode == 0
        docker_info_stderr = result.stderr or ""
    except subprocess.TimeoutExpired:
        print_error("Docker daemon did not respond within 30 seconds")
    except FileNotFoundError:
        pass  # already handled by caller

    if not daemon_ok:
        # Try to start it
        started = _try_start_docker_daemon(system)
        if started:
            # Re-check
            try:
                result = subprocess.run(
                    ["docker", "info"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                daemon_ok = result.returncode == 0
                docker_info_stderr = result.stderr or ""
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    if not daemon_ok:
        print_error("Docker daemon is not running")
        _print_docker_daemon_help(system, docker_info_stderr)
        return False

    print_success("Docker daemon: reachable")

    # ── 3. Version check ──────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Client.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_str = result.stdout.strip()
        # Parse "24.0.7" or "20.10.21" → (major, minor)
        parts = version_str.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if (major, minor) >= _MIN_DOCKER_VERSION:
            print_success(f"Docker version: {version_str}")
        else:
            print_warning(
                f"Docker version {version_str} is outdated "
                f"(need {_MIN_DOCKER_VERSION[0]}.{_MIN_DOCKER_VERSION[1]}+)"
            )
            print_info("  snflwr.ai requires Docker 20.10+ for Compose v2 support.")
            _print_docker_upgrade_help(system)
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        print_warning("Could not determine Docker version — continuing anyway")

    # ── 4. Docker Compose v2 ──────────────────────────────────────────
    compose_ok = False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            compose_ver = result.stdout.strip()
            print_success(f"Docker Compose: {compose_ver}")
            compose_ok = True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if not compose_ok:
        # Fall back to docker-compose v1
        if shutil.which("docker-compose"):
            print_warning(
                "Docker Compose v2 plugin not found, but docker-compose v1 is available"
            )
            print_info("  Consider upgrading: https://docs.docker.com/compose/install/")
            compose_ok = True
        else:
            print_error("Docker Compose is not available")
            print_info("  Install the Compose plugin:")
            if system == "Linux":
                print_info("    sudo apt install docker-compose-plugin")
                print_info("    or: sudo dnf install docker-compose-plugin")
            elif system == "Darwin":
                print_info(
                    "    Docker Desktop for Mac includes Compose — restart Docker Desktop"
                )
            elif system == "Windows":
                print_info(
                    "    Docker Desktop for Windows includes Compose — restart Docker Desktop"
                )
            return False

    # ── 5. Smoke test ─────────────────────────────────────────────────
    print_info("Running Docker smoke test (docker run hello-world)...")
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "hello-world"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and "Hello from Docker" in result.stdout:
            print_success("Docker smoke test: passed (containers work)")
        else:
            print_error("Docker smoke test failed")
            stderr = (result.stderr or "").strip()
            if stderr:
                # Show the first few lines of the error
                for line in stderr.splitlines()[:5]:
                    print_info(f"  {line}")
            _print_docker_smoke_help(system, result.stderr or "")
            return False
    except subprocess.TimeoutExpired:
        print_error("Docker smoke test timed out (120 s)")
        print_info("  This usually means the Docker daemon is overloaded or stuck.")
        print_info("  Try restarting Docker and re-running the installer.")
        return False
    except (FileNotFoundError, PermissionError, OSError) as e:
        print_warning(f"Could not run smoke test: {e}")

    return True


def _validate_wsl2() -> bool:
    """Check that WSL 2 is installed and functional (Windows only).

    Docker Desktop on Windows requires WSL 2 with a kernel >= 5.10.
    Returns True if WSL 2 is ready, False with remediation printed.
    """
    # Check if wsl.exe is available
    if not shutil.which("wsl"):
        print_error("WSL is not installed — Docker Desktop requires WSL 2")
        print_info("  Install WSL 2 from an elevated PowerShell:")
        print_info("    wsl --install")
        print_info("  Then restart your computer and re-run the installer.")
        return False

    # Check WSL version
    try:
        result = subprocess.run(
            ["wsl", "--status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # wsl --status may output UTF-16LE with null bytes on some Windows builds
        output = (result.stdout + result.stderr).replace("\x00", "")

        # Check for WSL 2 as default
        if "default version: 1" in output.lower():
            print_error("WSL default version is 1 — Docker requires WSL 2")
            print_info("  Upgrade from an elevated PowerShell:")
            print_info("    wsl --set-default-version 2")
            print_info("  Then restart Docker Desktop and re-run the installer.")
            return False

        # Check kernel version
        kernel_line = ""
        for line in output.splitlines():
            if "kernel" in line.lower() and "version" in line.lower():
                kernel_line = line
                break

        if kernel_line:
            print_success(f"WSL 2: {kernel_line.strip()}")
        else:
            print_success("WSL 2: detected")

    except subprocess.TimeoutExpired:
        print_warning("WSL status check timed out — continuing anyway")
    except (FileNotFoundError, PermissionError, OSError):
        # wsl --status may fail on older Windows builds; don't block on it
        print_info("Could not verify WSL version — continuing")

    # Verify a WSL 2 distro is actually registered (Docker needs one)
    try:
        result = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Output has null bytes on some Windows builds
        output = result.stdout.replace("\x00", "")
        has_v2_distro = False
        for line in output.splitlines():
            # Lines look like: "* Ubuntu    Running  2"
            parts = line.split()
            if parts and parts[-1] == "2":
                has_v2_distro = True
                break

        if not has_v2_distro:
            print_warning("No WSL 2 distribution found")
            print_info("  Docker Desktop requires a WSL 2 distro. Install one:")
            print_info("    wsl --install -d Ubuntu")
            print_info("  Then restart and re-run the installer.")
            # Don't return False — Docker Desktop can install its own minimal distro
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # non-critical; Docker Desktop can install its own minimal distro

    return True


def _try_start_docker_daemon(system: str) -> bool:
    """Attempt to start the Docker daemon. Returns True if it came up."""
    print_info("Attempting to start Docker...")

    if system == "Darwin":
        # macOS: launch Docker Desktop
        try:
            subprocess.run(["open", "-a", "Docker"], check=False, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    elif system == "Windows":
        # Windows: try Docker Desktop
        for path in [
            os.path.join(
                os.environ.get("ProgramFiles", ""),
                "Docker",
                "Docker",
                "Docker Desktop.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Docker", "Docker Desktop.exe"
            ),
        ]:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path], creationflags=subprocess.CREATE_NO_WINDOW)
                except (FileNotFoundError, PermissionError, OSError):
                    pass
                break

    elif system == "Linux":
        # Linux: try systemctl
        try:
            subprocess.run(["systemctl", "start", "docker"], check=False, timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            # Try with sudo if passwordless
            try:
                subprocess.run(
                    ["sudo", "-n", "systemctl", "start", "docker"],
                    check=False,
                    timeout=15,
                )
            except (
                subprocess.TimeoutExpired,
                FileNotFoundError,
                PermissionError,
                OSError,
            ):
                pass

    # Wait for daemon to come up (check first, then sleep)
    for i in range(20):
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        time.sleep(2)

    return False


def _print_docker_daemon_help(system: str, stderr: str):
    """Print platform-specific help for a Docker daemon that won't start."""
    stderr_lower = stderr.lower()

    # Permission denied
    if "permission denied" in stderr_lower:
        print()
        if system == "Linux":
            username = os.environ.get("USER", "your-user")
            print_info("  Your user doesn't have Docker permissions. Fix with:")
            print_info(f"    sudo usermod -aG docker {username}")
            print_info("  Then log out and back in, and re-run the installer.")
        elif system == "Darwin":
            print_info("  Try restarting Docker Desktop, or reinstall it:")
            print_info("    https://docs.docker.com/desktop/setup/install/mac-install/")
        elif system == "Windows":
            print_info(
                "  Try restarting Docker Desktop, or run this installer as Administrator."
            )
        return

    # Connection refused / socket not found
    if "connection refused" in stderr_lower or "cannot connect" in stderr_lower:
        print()
        if system == "Darwin":
            print_info("  Docker Desktop is not running. Start it:")
            print_info("    open -a Docker")
            print_info(
                "  Wait for the whale icon to appear in the menu bar, then re-run."
            )
        elif system == "Windows":
            print_info("  Docker Desktop is not running. Start it from the Start Menu.")
            print_info(
                "  Wait for the whale icon to appear in the system tray, then re-run."
            )
        elif system == "Linux":
            print_info("  The Docker daemon is not running. Start it:")
            print_info("    sudo systemctl start docker")
            print_info("  To auto-start on boot:")
            print_info("    sudo systemctl enable docker")
        return

    # WSL-related errors on Windows
    if system == "Windows" and (
        "wsl" in stderr_lower or "hyperv" in stderr_lower or "hyper-v" in stderr_lower
    ):
        print()
        print_info("  Docker Desktop is reporting a WSL / Hyper-V problem:")
        for line in stderr.strip().splitlines()[:5]:
            print_info(f"    {line.strip()}")
        print()
        print_info("  Common fixes:")
        print_info("    1. Update WSL:     wsl --update")
        print_info("    2. Restart:        Reboot your computer")
        print_info("    3. Enable Hyper-V: (from elevated PowerShell)")
        print_info(
            "         Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All"
        )
        return

    # Generic fallback
    print()
    if stderr.strip():
        for line in stderr.strip().splitlines()[:5]:
            print_info(f"  {line.strip()}")
    print_info("  Try restarting Docker and re-running the installer.")
    if system == "Darwin":
        print_info("  If the problem persists, reinstall Docker Desktop:")
        print_info("    https://docs.docker.com/desktop/setup/install/mac-install/")
    elif system == "Windows":
        print_info("  If the problem persists, reinstall Docker Desktop:")
        print_info("    https://docs.docker.com/desktop/setup/install/windows-install/")
    elif system == "Linux":
        print_info("  If the problem persists, reinstall Docker:")
        print_info("    curl -fsSL https://get.docker.com | sh")


def _print_docker_upgrade_help(system: str):
    """Print help for upgrading an outdated Docker version."""
    if system == "Darwin":
        print_info("  Update via Docker Desktop → Check for Updates, or:")
        print_info("    brew upgrade --cask docker")
    elif system == "Windows":
        print_info("  Update via Docker Desktop → Check for Updates, or:")
        print_info("    winget upgrade Docker.DockerDesktop")
    elif system == "Linux":
        print_info("  Update with the official install script:")
        print_info("    curl -fsSL https://get.docker.com | sh")


def _print_docker_smoke_help(system: str, stderr: str):
    """Print diagnostics when ``docker run hello-world`` fails."""
    stderr_lower = stderr.lower()

    if "no space left" in stderr_lower:
        print_info("  Disk may be full.")
        print_info("  Free up space:  docker system prune -a")
    elif "no such file" in stderr_lower:
        print_info("  Docker storage may be corrupted.")
        print_info("  Try resetting:  docker system prune -a")
        print_info("  If that fails, reinstall Docker.")
    elif "permission denied" in stderr_lower:
        print_info("  Permission issue — see Docker daemon help above.")
    elif "network" in stderr_lower or "dial tcp" in stderr_lower:
        print_info("  Docker can't pull images — check your internet connection.")
        print_info("  If behind a proxy, configure Docker's proxy settings:")
        print_info("    https://docs.docker.com/network/proxy/")
    else:
        print_info(
            "  Docker can run but something is wrong with the container runtime."
        )
        if system == "Windows":
            print_info("  Try these steps:")
            print_info("    1. Open Docker Desktop → Settings → General")
            print_info("    2. Ensure 'Use the WSL 2 based engine' is checked")
            print_info("    3. Click 'Apply & restart'")
            print_info("    4. If that fails: wsl --update && wsl --shutdown")
            print_info("    5. Restart Docker Desktop and re-run the installer")
        elif system == "Darwin":
            print_info("  Try: Docker Desktop → Troubleshoot → 'Clean / Purge data'")
            print_info("  Then restart Docker Desktop and re-run the installer.")
        elif system == "Linux":
            print_info("  Try restarting the Docker daemon:")
            print_info("    sudo systemctl restart docker")
            print_info(
                "  If that fails, reinstall: curl -fsSL https://get.docker.com | sh"
            )
