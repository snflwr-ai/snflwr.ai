#!/usr/bin/env python3
"""
snflwr.ai Interactive Installer
Guides users through deployment setup with smart defaults
"""

import os
import sys
import secrets
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

def _is_powershell():
    """Detect whether the installer was launched from a PowerShell terminal.

    Walks up the process tree (parent, grandparent, ...) looking for pwsh.exe
    or powershell.exe.  This handles the common case where the chain is:

        PowerShell -> cmd.exe (setup.bat) -> python (install.py)

    Falls back to False (assume cmd.exe) if detection fails.
    """
    if platform.system() != 'Windows':
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ('dwSize', ctypes.c_ulong),
                ('cntUsage', ctypes.c_ulong),
                ('th32ProcessID', ctypes.c_ulong),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                ('th32ModuleID', ctypes.c_ulong),
                ('cntThreads', ctypes.c_ulong),
                ('th32ParentProcessID', ctypes.c_ulong),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', ctypes.c_ulong),
                ('szExeFile', ctypes.c_char * 260),
            ]

        # Take a snapshot of all processes
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

        # Build a lookup: pid -> (exe_name, parent_pid)
        procs = {}
        if kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                name = entry.szExeFile.decode('utf-8', errors='ignore').lower()
                procs[entry.th32ProcessID] = (name, entry.th32ParentProcessID)
                if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
        kernel32.CloseHandle(snap)

        # Walk up the ancestor chain (limit depth to avoid infinite loops)
        pid = os.getpid()
        for _ in range(10):
            if pid not in procs or pid == 0:
                break
            name, parent = procs[pid]
            if 'pwsh' in name or 'powershell' in name:
                return True
            pid = parent

    except (OSError, AttributeError, ValueError):
        pass
    return False


def _mask_secret(value: str, visible: int = 4) -> str:
    """Show only last N chars of a secret for verification."""
    s = str(value)
    if len(s) <= visible:
        return '***'
    return f"***{s[-visible:]}"

def print_header(text):
    """Print styled header"""
    print(f"\n{'='*70}")
    print(text.center(70))
    print(f"{'='*70}\n")

def print_success(text):
    print(f"  [OK] {text}")

def print_error(text):
    print(f"  [ERROR] {text}")

def print_warning(text):
    print(f"  [WARN] {text}")

def print_info(text):
    print(f"  [INFO] {text}")

def ask_question(question, default=None):
    """Ask user a question"""
    if default:
        prompt = f"? {question} [{default}]: "
    else:
        prompt = f"? {question}: "

    while True:
        answer = input(prompt).strip()
        if answer:
            return answer
        if default is not None:
            return default
        print_warning("A value is required. Please try again.")

def ask_yes_no(question, default=True):
    """Ask yes/no question"""
    default_text = "Y/n" if default else "y/N"
    prompt = f"? {question} [{default_text}]: "

    answer = input(prompt).strip().lower()
    if not answer:
        return default
    return answer in ['y', 'yes']

def generate_secure_token():
    """Generate secure random token"""
    return secrets.token_hex(32)

def detect_usb_drives():
    """Detect available USB drives"""
    drives = []
    system = platform.system()

    if system == 'Windows':
        # Check drive letters D-Z for removable drives
        try:
            import ctypes
            for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
                drive_path = f"{letter}:\\"
                if os.path.exists(drive_path):
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                    if drive_type == 2:  # Removable drive
                        drives.append(Path(drive_path))
        except (OSError, AttributeError, ValueError):
            pass

    elif system == 'Darwin':  # macOS
        volumes_path = Path('/Volumes')
        if volumes_path.exists():
            for volume in volumes_path.iterdir():
                if volume.is_dir() and volume.name not in ['Macintosh HD', 'Preboot', 'Recovery', 'VM']:
                    drives.append(volume)

    elif system == 'Linux':
        # USB drives are typically at /media/<user>/<drive_label>/
        media_path = Path('/media')
        if media_path.exists():
            for user_dir in media_path.iterdir():
                if user_dir.is_dir() and os.access(user_dir, os.R_OK):
                    for mount in user_dir.iterdir():
                        if mount.is_dir():
                            drives.append(mount)
        # Also check /mnt/ for manually mounted drives (one level only)
        mnt_path = Path('/mnt')
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
    if system == 'Windows':
        if not _validate_wsl2():
            # _validate_wsl2 already printed remediation steps
            return False

    # ── 2. Docker daemon reachable ────────────────────────────────────
    daemon_ok = False
    docker_info_stderr = ''
    try:
        result = subprocess.run(
            ['docker', 'info'],
            capture_output=True, text=True, timeout=30,
        )
        daemon_ok = (result.returncode == 0)
        docker_info_stderr = result.stderr or ''
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
                    ['docker', 'info'],
                    capture_output=True, text=True, timeout=30,
                )
                daemon_ok = (result.returncode == 0)
                docker_info_stderr = result.stderr or ''
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
            ['docker', 'version', '--format', '{{.Client.Version}}'],
            capture_output=True, text=True, timeout=10,
        )
        version_str = result.stdout.strip()
        # Parse "24.0.7" or "20.10.21" → (major, minor)
        parts = version_str.split('.')
        major, minor = int(parts[0]), int(parts[1])
        if (major, minor) >= _MIN_DOCKER_VERSION:
            print_success(f"Docker version: {version_str}")
        else:
            print_warning(f"Docker version {version_str} is outdated "
                          f"(need {_MIN_DOCKER_VERSION[0]}.{_MIN_DOCKER_VERSION[1]}+)")
            print_info("  snflwr.ai requires Docker 20.10+ for Compose v2 support.")
            _print_docker_upgrade_help(system)
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        print_warning("Could not determine Docker version — continuing anyway")

    # ── 4. Docker Compose v2 ──────────────────────────────────────────
    compose_ok = False
    try:
        result = subprocess.run(
            ['docker', 'compose', 'version'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            compose_ver = result.stdout.strip()
            print_success(f"Docker Compose: {compose_ver}")
            compose_ok = True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if not compose_ok:
        # Fall back to docker-compose v1
        if shutil.which('docker-compose'):
            print_warning("Docker Compose v2 plugin not found, but docker-compose v1 is available")
            print_info("  Consider upgrading: https://docs.docker.com/compose/install/")
            compose_ok = True
        else:
            print_error("Docker Compose is not available")
            print_info("  Install the Compose plugin:")
            if system == 'Linux':
                print_info("    sudo apt install docker-compose-plugin")
                print_info("    or: sudo dnf install docker-compose-plugin")
            elif system == 'Darwin':
                print_info("    Docker Desktop for Mac includes Compose — restart Docker Desktop")
            elif system == 'Windows':
                print_info("    Docker Desktop for Windows includes Compose — restart Docker Desktop")
            return False

    # ── 5. Smoke test ─────────────────────────────────────────────────
    print_info("Running Docker smoke test (docker run hello-world)...")
    try:
        result = subprocess.run(
            ['docker', 'run', '--rm', 'hello-world'],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and 'Hello from Docker' in result.stdout:
            print_success("Docker smoke test: passed (containers work)")
        else:
            print_error("Docker smoke test failed")
            stderr = (result.stderr or '').strip()
            if stderr:
                # Show the first few lines of the error
                for line in stderr.splitlines()[:5]:
                    print_info(f"  {line}")
            _print_docker_smoke_help(system, result.stderr or '')
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
    if not shutil.which('wsl'):
        print_error("WSL is not installed — Docker Desktop requires WSL 2")
        print_info("  Install WSL 2 from an elevated PowerShell:")
        print_info("    wsl --install")
        print_info("  Then restart your computer and re-run the installer.")
        return False

    # Check WSL version
    try:
        result = subprocess.run(
            ['wsl', '--status'],
            capture_output=True, text=True, timeout=15,
        )
        # wsl --status may output UTF-16LE with null bytes on some Windows builds
        output = (result.stdout + result.stderr).replace('\x00', '')

        # Check for WSL 2 as default
        if 'default version: 1' in output.lower():
            print_error("WSL default version is 1 — Docker requires WSL 2")
            print_info("  Upgrade from an elevated PowerShell:")
            print_info("    wsl --set-default-version 2")
            print_info("  Then restart Docker Desktop and re-run the installer.")
            return False

        # Check kernel version
        kernel_line = ''
        for line in output.splitlines():
            if 'kernel' in line.lower() and 'version' in line.lower():
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
            ['wsl', '-l', '-v'],
            capture_output=True, text=True, timeout=15,
        )
        # Output has null bytes on some Windows builds
        output = result.stdout.replace('\x00', '')
        has_v2_distro = False
        for line in output.splitlines():
            # Lines look like: "* Ubuntu    Running  2"
            parts = line.split()
            if parts and parts[-1] == '2':
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

    if system == 'Darwin':
        # macOS: launch Docker Desktop
        try:
            subprocess.run(['open', '-a', 'Docker'], check=False, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    elif system == 'Windows':
        # Windows: try Docker Desktop
        for path in [
            os.path.join(os.environ.get('ProgramFiles', ''), 'Docker', 'Docker', 'Docker Desktop.exe'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Docker', 'Docker Desktop.exe'),
        ]:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path], creationflags=subprocess.CREATE_NO_WINDOW)
                except (FileNotFoundError, PermissionError, OSError):
                    pass
                break

    elif system == 'Linux':
        # Linux: try systemctl
        try:
            subprocess.run(['systemctl', 'start', 'docker'], check=False, timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            # Try with sudo if passwordless
            try:
                subprocess.run(['sudo', '-n', 'systemctl', 'start', 'docker'],
                               check=False, timeout=15)
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
                pass

    # Wait for daemon to come up (check first, then sleep)
    for i in range(20):
        try:
            result = subprocess.run(
                ['docker', 'info'],
                capture_output=True, timeout=10,
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
    if 'permission denied' in stderr_lower:
        print()
        if system == 'Linux':
            username = os.environ.get('USER', 'your-user')
            print_info("  Your user doesn't have Docker permissions. Fix with:")
            print_info(f"    sudo usermod -aG docker {username}")
            print_info("  Then log out and back in, and re-run the installer.")
        elif system == 'Darwin':
            print_info("  Try restarting Docker Desktop, or reinstall it:")
            print_info("    https://docs.docker.com/desktop/setup/install/mac-install/")
        elif system == 'Windows':
            print_info("  Try restarting Docker Desktop, or run this installer as Administrator.")
        return

    # Connection refused / socket not found
    if 'connection refused' in stderr_lower or 'cannot connect' in stderr_lower:
        print()
        if system == 'Darwin':
            print_info("  Docker Desktop is not running. Start it:")
            print_info("    open -a Docker")
            print_info("  Wait for the whale icon to appear in the menu bar, then re-run.")
        elif system == 'Windows':
            print_info("  Docker Desktop is not running. Start it from the Start Menu.")
            print_info("  Wait for the whale icon to appear in the system tray, then re-run.")
        elif system == 'Linux':
            print_info("  The Docker daemon is not running. Start it:")
            print_info("    sudo systemctl start docker")
            print_info("  To auto-start on boot:")
            print_info("    sudo systemctl enable docker")
        return

    # WSL-related errors on Windows
    if system == 'Windows' and ('wsl' in stderr_lower or 'hyperv' in stderr_lower
                                 or 'hyper-v' in stderr_lower):
        print()
        print_info("  Docker Desktop is reporting a WSL / Hyper-V problem:")
        for line in stderr.strip().splitlines()[:5]:
            print_info(f"    {line.strip()}")
        print()
        print_info("  Common fixes:")
        print_info("    1. Update WSL:     wsl --update")
        print_info("    2. Restart:        Reboot your computer")
        print_info("    3. Enable Hyper-V: (from elevated PowerShell)")
        print_info("         Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All")
        return

    # Generic fallback
    print()
    if stderr.strip():
        for line in stderr.strip().splitlines()[:5]:
            print_info(f"  {line.strip()}")
    print_info("  Try restarting Docker and re-running the installer.")
    if system == 'Darwin':
        print_info("  If the problem persists, reinstall Docker Desktop:")
        print_info("    https://docs.docker.com/desktop/setup/install/mac-install/")
    elif system == 'Windows':
        print_info("  If the problem persists, reinstall Docker Desktop:")
        print_info("    https://docs.docker.com/desktop/setup/install/windows-install/")
    elif system == 'Linux':
        print_info("  If the problem persists, reinstall Docker:")
        print_info("    curl -fsSL https://get.docker.com | sh")


def _print_docker_upgrade_help(system: str):
    """Print help for upgrading an outdated Docker version."""
    if system == 'Darwin':
        print_info("  Update via Docker Desktop → Check for Updates, or:")
        print_info("    brew upgrade --cask docker")
    elif system == 'Windows':
        print_info("  Update via Docker Desktop → Check for Updates, or:")
        print_info("    winget upgrade Docker.DockerDesktop")
    elif system == 'Linux':
        print_info("  Update with the official install script:")
        print_info("    curl -fsSL https://get.docker.com | sh")


def _print_docker_smoke_help(system: str, stderr: str):
    """Print diagnostics when ``docker run hello-world`` fails."""
    stderr_lower = stderr.lower()

    if 'no space left' in stderr_lower:
        print_info("  Disk may be full.")
        print_info("  Free up space:  docker system prune -a")
    elif 'no such file' in stderr_lower:
        print_info("  Docker storage may be corrupted.")
        print_info("  Try resetting:  docker system prune -a")
        print_info("  If that fails, reinstall Docker.")
    elif 'permission denied' in stderr_lower:
        print_info("  Permission issue — see Docker daemon help above.")
    elif 'network' in stderr_lower or 'dial tcp' in stderr_lower:
        print_info("  Docker can't pull images — check your internet connection.")
        print_info("  If behind a proxy, configure Docker's proxy settings:")
        print_info("    https://docs.docker.com/network/proxy/")
    else:
        print_info("  Docker can run but something is wrong with the container runtime.")
        if system == 'Windows':
            print_info("  Try these steps:")
            print_info("    1. Open Docker Desktop → Settings → General")
            print_info("    2. Ensure 'Use the WSL 2 based engine' is checked")
            print_info("    3. Click 'Apply & restart'")
            print_info("    4. If that fails: wsl --update && wsl --shutdown")
            print_info("    5. Restart Docker Desktop and re-run the installer")
        elif system == 'Darwin':
            print_info("  Try: Docker Desktop → Troubleshoot → 'Clean / Purge data'")
            print_info("  Then restart Docker Desktop and re-run the installer.")
        elif system == 'Linux':
            print_info("  Try restarting the Docker daemon:")
            print_info("    sudo systemctl restart docker")
            print_info("  If that fails, reinstall: curl -fsSL https://get.docker.com | sh")


def check_system_requirements():
    """Check hardware and report system readiness."""
    print_header("System Check")

    system = platform.system()

    # --- RAM ---
    total_ram_gb = None
    try:
        if system == 'Darwin':
            result = subprocess.run(
                ['sysctl', '-n', 'hw.memsize'],
                capture_output=True, text=True, check=True,
            )
            total_ram_gb = int(result.stdout.strip()) / (1024 ** 3)
        elif system == 'Linux':
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemTotal'):
                        kb = int(line.split()[1])
                        total_ram_gb = kb / (1024 ** 2)
                        break
        elif system == 'Windows':
            result = subprocess.run(
                ['wmic', 'ComputerSystem', 'get', 'TotalPhysicalMemory', '/value'],
                capture_output=True, text=True, check=True,
            )
            for line in result.stdout.splitlines():
                if 'TotalPhysicalMemory' in line:
                    total_ram_gb = int(line.split('=')[1]) / (1024 ** 3)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, ValueError):
        pass

    if total_ram_gb is not None:
        if total_ram_gb >= 8:
            print_success(f"RAM: {total_ram_gb:.0f} GB (8 GB minimum met)")
        else:
            print_warning(f"RAM: {total_ram_gb:.1f} GB — 8 GB recommended for the default model")
            print_info("  The installer will offer a smaller model option later.")
    else:
        print_info("RAM: could not detect (8 GB recommended)")

    # --- GPU ---
    gpu_results = []

    if system == 'Darwin':
        try:
            result = subprocess.run(
                ['sysctl', '-n', 'machdep.cpu.brand_string'],
                capture_output=True, text=True, check=True,
            )
            cpu = result.stdout.strip()
            if 'Apple' in cpu:
                gpu_results.append(('ok', f"Apple Silicon ({cpu}) — Metal GPU acceleration"))
            else:
                gpu_results.append(('info', f"Intel Mac ({cpu}) — CPU-only (no GPU acceleration)"))
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            gpu_results.append(('info', "macOS detected — Metal GPU acceleration likely available"))

    elif system in ('Linux', 'Windows'):
        # Grab lspci once for hardware-level detection
        lspci_lines = []
        try:
            result = subprocess.run(['lspci'], capture_output=True, text=True)
            if result.returncode == 0:
                lspci_lines = [
                    line for line in result.stdout.splitlines()
                    if any(k in line.lower() for k in ('vga', '3d', 'display'))
                ]
        except FileNotFoundError:
            pass

        # --- NVIDIA ---
        nvidia_found = False
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True,
            )
            # nvidia-smi --query-gpu sends errors to stdout, not stderr
            output = ((result.stdout or '') + ' ' + (result.stderr or '')).lower()
            if result.returncode == 0 and result.stdout.strip():
                for name in result.stdout.strip().splitlines():
                    gpu_results.append(('ok', f"NVIDIA {name.strip()} — CUDA GPU acceleration"))
                nvidia_found = True
            elif 'version mismatch' in output:
                gpu_results.append(('warn', "NVIDIA GPU found but driver/library version mismatch — a reboot usually fixes this"))
                nvidia_found = True
            elif 'not found' in output or 'no devices' in output:
                gpu_results.append(('warn', "NVIDIA driver installed but no GPU devices found — check hardware connection"))
                nvidia_found = True
        except FileNotFoundError:
            pass

        # Fall back to lspci for NVIDIA hardware the driver tools can't see
        if not nvidia_found:
            for line in lspci_lines:
                if 'nvidia' in line.lower():
                    name = line.split(': ', 1)[-1] if ': ' in line else line
                    gpu_results.append(('warn', f"{name.strip()} — detected but nvidia-smi unavailable, drivers may need installing"))
                    nvidia_found = True

        # --- AMD ---
        amd_found = False
        try:
            result = subprocess.run(
                ['rocm-smi', '--showproductname'],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    # Only parse "Card series" or "Card model" lines for the GPU name
                    if 'Card series' in line or 'Card model' in line:
                        name = line.split(':', 2)[-1].strip()
                        if name:
                            gpu_results.append(('ok', f"AMD {name} — ROCm GPU acceleration"))
                            amd_found = True
        except FileNotFoundError:
            pass

        # Fall back to lspci for AMD hardware
        if not amd_found:
            for line in lspci_lines:
                if 'amd' in line.lower() or 'radeon' in line.lower():
                    name = line.split(': ', 1)[-1] if ': ' in line else line
                    gpu_results.append(('info', f"{name.strip()} — ROCm drivers may need installing for GPU acceleration"))
                    amd_found = True

        # --- Intel ---
        for line in lspci_lines:
            if 'intel' in line.lower():
                name = line.split(': ', 1)[-1] if ': ' in line else line
                gpu_results.append(('info', f"{name.strip()} — integrated GPU"))

    if gpu_results:
        for level, msg in gpu_results:
            if level == 'ok':
                print_success(f"GPU: {msg}")
            elif level == 'warn':
                print_warning(f"GPU: {msg}")
            else:
                print_info(f"GPU: {msg}")
    else:
        print_info("GPU: No GPU detected — CPU-only (slower, but works fine)")

    # --- Disk space ---
    try:
        import shutil as _shutil
        usage = _shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024 ** 3)
        # Minimum realistic need: ~2 GB (smallest model + deps + db)
        # Default model (8B): ~6 GB total. 8 GB free is comfortable.
        if free_gb >= 8:
            print_success(f"Disk: {free_gb:.0f} GB free")
        elif free_gb >= 3:
            print_info(f"Disk: {free_gb:.1f} GB free — enough for smaller models")
        else:
            print_warning(f"Disk: {free_gb:.1f} GB free — may be tight (smallest model needs ~2 GB)")
    except (OSError, ValueError):
        pass

    # --- macOS: Xcode Command Line Tools (needed for C extensions) ---
    if system == 'Darwin':
        xcode_clt_ok = False
        try:
            result = subprocess.run(
                ['xcode-select', '-p'],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                xcode_clt_ok = True
                print_success("Xcode CLT: installed (C compiler available)")
        except FileNotFoundError:
            pass

        if not xcode_clt_ok:
            # Also check if gcc/clang is available (might be from Homebrew)
            if shutil.which('gcc') or shutil.which('clang'):
                print_success("C compiler: available (gcc/clang found)")
            else:
                print_warning("Xcode Command Line Tools: not installed")
                print_info("  Required to compile native Python packages (argon2-cffi, aiohttp, etc.)")
                if ask_yes_no("Install Xcode Command Line Tools now?", default=True):
                    print_info("Opening Xcode CLT installer (follow the on-screen dialog)...")
                    try:
                        subprocess.run(['xcode-select', '--install'], check=False)
                        print()
                        print_info("Waiting for Xcode Command Line Tools installation...")
                        print_info("A system dialog should appear. Click 'Install' and wait for it to finish.")
                        print_info("Press Enter here once the installation is complete.")
                        input()
                        # Verify it worked
                        verify = subprocess.run(
                            ['xcode-select', '-p'],
                            capture_output=True, text=True,
                        )
                        if verify.returncode == 0:
                            print_success("Xcode Command Line Tools installed")
                        else:
                            print_warning("Xcode CLT installation may not have completed")
                            print_info("  You can retry manually: xcode-select --install")
                    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                        print_warning(f"Could not launch Xcode CLT installer: {e}")
                        print_info("  Install manually: xcode-select --install")
                else:
                    print_warning("Some Python packages may fail to install without a C compiler")
                    print_info("  Install later: xcode-select --install")

    # --- Docker (required for Open WebUI chat interface) ---
    docker_found = shutil.which('docker') is not None
    if docker_found:
        print_success("Docker: installed")
    else:
        print_error("Docker: not installed (REQUIRED)")
        print_info("  snflwr.ai uses Open WebUI as its chat interface, which requires Docker.")
        print()

        # Try to auto-install Docker on each platform
        installed = False

        if system == 'Windows' and shutil.which('winget'):
            if ask_yes_no("Install Docker Desktop via winget?", default=True):
                try:
                    subprocess.run(
                        ['winget', 'install', 'Docker.DockerDesktop', '-e',
                         '--accept-source-agreements', '--accept-package-agreements'],
                        check=True,
                    )
                    _refresh_windows_path()
                    if shutil.which('docker'):
                        print_success("Docker Desktop installed")
                        installed = True
                    else:
                        print_success("Docker Desktop installed (restart may be required for PATH)")
                        print_info("  Docker will be available after you restart your computer.")
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker Desktop via winget")

        elif system == 'Darwin' and shutil.which('brew'):
            if ask_yes_no("Install Docker Desktop via Homebrew?", default=True):
                try:
                    subprocess.run(
                        ['brew', 'install', '--cask', 'docker'],
                        check=True,
                    )
                    if shutil.which('docker'):
                        print_success("Docker Desktop installed")
                        installed = True
                    else:
                        print_success("Docker Desktop installed")
                        print_info("  Open Docker Desktop from Applications to complete setup.")
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker Desktop via Homebrew")

        elif system == 'Linux':
            if ask_yes_no("Install Docker via the official install script?", default=True):
                try:
                    print_info("Downloading and running Docker install script...")
                    subprocess.run(
                        ['bash', '-c', 'curl -fsSL https://get.docker.com | sh'],
                        check=True,
                    )
                    # Add current user to docker group so they don't need sudo
                    username = os.environ.get('USER', os.environ.get('LOGNAME', ''))
                    if username:
                        subprocess.run(
                            ['sudo', 'usermod', '-aG', 'docker', username],
                            check=False,
                        )
                        print_info(f"  Added '{username}' to the docker group.")
                        print_info("  You may need to log out and back in for this to take effect.")
                    if shutil.which('docker'):
                        print_success("Docker installed")
                        installed = True
                    else:
                        print_success("Docker installed (PATH update may require a new terminal)")
                        installed = True
                except subprocess.CalledProcessError:
                    print_warning("Failed to install Docker via install script")
                    print_info("  You may need to run the installer with sudo.")

        if not installed:
            print()
            print_info("  Install Docker Desktop manually before continuing:")
            if system == 'Darwin':
                print_info("    https://docs.docker.com/desktop/setup/install/mac-install/")
            elif system == 'Windows':
                print_info("    https://docs.docker.com/desktop/setup/install/windows-install/")
            else:
                print_info("    https://docs.docker.com/desktop/setup/install/linux/")
                print_info("    Or: curl -fsSL https://get.docker.com | sh")
            print()
            print_error("Docker is required. Install it and re-run:  python3 install.py")
            sys.exit(1)

    # Deep-validate Docker: binary on PATH is necessary but not sufficient.
    # Verify the daemon, runtime, and compose actually work.
    if shutil.which('docker'):
        if not _validate_docker(system):
            print()
            if not ask_yes_no("Docker validation failed. Continue setup anyway?", default=False):
                print_error("Fix the Docker issues above and re-run:  python3 install.py")
                sys.exit(1)
            print_warning("Continuing — Open WebUI may not work until Docker is fixed")

    print()
    return total_ram_gb

# ── Firewall ──────────────────────────────────────────────────────────

# Ports used by snflwr.ai (port, description)
SNFLWR_PORTS = [
    (3000,  "Open WebUI (chat interface)"),
    (39150, "snflwr.ai API server"),
    (11434, "Ollama (local AI engine)"),
]


def configure_firewall():
    """Prompt the user to allow snflwr.ai ports through the system firewall.

    Detects the active firewall on the current platform and offers to add
    allow-rules for localhost traffic on the ports snflwr.ai uses.
    Skipped silently when no firewall is detected or if the user declines.
    """
    print_header("Firewall Configuration")

    system = platform.system()

    print("snflwr.ai uses the following local ports:\n")
    for port, desc in SNFLWR_PORTS:
        print(f"  * localhost:{port}  - {desc}")
    print()

    if system == 'Windows':
        _configure_firewall_windows()
    elif system == 'Darwin':
        _configure_firewall_macos()
    elif system == 'Linux':
        _configure_firewall_linux()
    else:
        print_info(f"Firewall configuration not supported on {system}.")
        print_info("If you experience connection issues, ensure the ports above are allowed.")


def _configure_firewall_windows():
    """Add Windows Firewall rules for snflwr.ai ports."""
    # Check if Windows Firewall is active
    try:
        result = subprocess.run(
            ['netsh', 'advfirewall', 'show', 'currentprofile', 'state'],
            capture_output=True, text=True, timeout=10,
        )
        if 'OFF' in result.stdout.upper():
            print_success("Windows Firewall is disabled — no rules needed")
            return
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Check which rules already exist (query each by name to avoid dumping all rules)
    existing = set()
    for port, _desc in SNFLWR_PORTS:
        rule_name = f"snflwr.ai - port {port}"
        try:
            result = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'show', 'rule',
                 f'name={rule_name}', 'dir=in'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and rule_name in result.stdout:
                existing.add(port)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    needed = [(p, d) for p, d in SNFLWR_PORTS if p not in existing]
    if not needed:
        print_success("Firewall rules already configured for all snflwr.ai ports")
        return

    print_info("Windows Firewall is active.")
    print_info("snflwr.ai needs firewall rules so its services can communicate.")
    print()
    if not ask_yes_no("Add Windows Firewall rules for these ports?", default=True):
        print_warning("Skipped — if you have connection issues, add the rules manually.")
        return

    for port, desc in needed:
        rule_name = f"snflwr.ai - port {port}"
        try:
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 f'name={rule_name}', 'dir=in', 'action=allow',
                 'protocol=tcp', f'localport={port}'],
                capture_output=True, check=True, timeout=10,
            )
            print_success(f"Allowed port {port} ({desc})")
        except subprocess.CalledProcessError:
            print_warning(f"Failed to add rule for port {port} — you may need to run as Administrator")
        except FileNotFoundError:
            print_warning("netsh not found — cannot configure firewall automatically")
            break


def _configure_firewall_macos():
    """Configure macOS Application Firewall for snflwr.ai.

    macOS's built-in firewall (socketfilterfw) does not block localhost
    traffic by default.  We check whether the firewall is on and inform
    the user; if it is on, we recommend allowing incoming connections for
    the relevant binaries so the "Do you want to allow?" popup is avoided.
    """
    fw_tool = '/usr/libexec/ApplicationFirewall/socketfilterfw'
    try:
        result = subprocess.run(
            [fw_tool, '--getglobalstate'],
            capture_output=True, text=True, timeout=10,
        )
        if 'disabled' in result.stdout.lower():
            print_success("macOS Firewall is disabled — no configuration needed")
            return
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        print_info("Could not detect macOS Firewall state — skipping")
        return

    print_info("macOS Firewall is active.")
    print_info("Localhost traffic is not blocked, but macOS may show an")
    print_info("\"Allow incoming connections\" dialog when services start.")
    print()

    # Detect binaries we can pre-authorize
    binaries = []
    ollama_path = shutil.which('ollama')
    if ollama_path:
        binaries.append(('Ollama', ollama_path))
    docker_path = shutil.which('docker')
    if docker_path:
        binaries.append(('Docker', docker_path))

    if not binaries:
        print_info("No services installed yet to pre-authorize. If macOS shows a")
        print_info("firewall dialog during startup, click \"Allow\" to permit the connection.")
        return

    if not ask_yes_no("Pre-authorize snflwr.ai services in the macOS Firewall?", default=True):
        print_info("Skipped — click \"Allow\" if macOS shows a firewall dialog during startup.")
        return

    for name, path in binaries:
        try:
            subprocess.run(
                ['sudo', fw_tool, '--add', path],
                check=False, timeout=30,
            )
            subprocess.run(
                ['sudo', fw_tool, '--unblockapp', path],
                check=False, timeout=10,
            )
            print_success(f"Authorized {name} ({path})")
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError) as e:
            print_warning(f"Could not authorize {name}: {e}")

    print_info("If macOS still shows a firewall dialog, click \"Allow\".")


def _configure_firewall_linux():
    """Add firewall rules on Linux (ufw or firewalld)."""
    # Detect active firewall
    fw_type = None

    # Check ufw
    if shutil.which('ufw'):
        # Try without sudo first (works on some distros), then with passwordless sudo
        for ufw_cmd in [['ufw', 'status'], ['sudo', '-n', 'ufw', 'status']]:
            try:
                result = subprocess.run(
                    ufw_cmd,
                    capture_output=True, text=True, timeout=10,
                )
                if 'active' in result.stdout.lower():
                    fw_type = 'ufw'
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
                continue

    # Check firewalld
    if not fw_type and shutil.which('firewall-cmd'):
        try:
            result = subprocess.run(
                ['firewall-cmd', '--state'],
                capture_output=True, text=True, timeout=10,
            )
            if 'running' in result.stdout.lower():
                fw_type = 'firewalld'
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            pass

    if not fw_type:
        print_success("No active firewall detected (ufw/firewalld) — no rules needed")
        print_info("If you use iptables/nftables directly, ensure these ports are allowed:")
        for port, desc in SNFLWR_PORTS:
            print_info(f"  tcp/{port}  ({desc})")
        return

    print_info(f"Detected active firewall: {fw_type}")
    print()
    if not ask_yes_no(f"Add {fw_type} rules to allow snflwr.ai ports?", default=True):
        print_warning("Skipped — if you have connection issues, add the rules manually.")
        return

    if fw_type == 'ufw':
        for port, desc in SNFLWR_PORTS:
            try:
                subprocess.run(
                    ['sudo', 'ufw', 'allow', f'{port}/tcp',
                     'comment', f'snflwr.ai - {desc}'],
                    check=True, timeout=15,
                )
                print_success(f"ufw: allowed port {port} ({desc})")
            except subprocess.CalledProcessError:
                print_warning(f"Failed to add ufw rule for port {port}")
            except FileNotFoundError:
                print_warning("sudo not found — run manually: sudo ufw allow {port}/tcp")
                break

    elif fw_type == 'firewalld':
        for port, desc in SNFLWR_PORTS:
            try:
                subprocess.run(
                    ['sudo', 'firewall-cmd', '--permanent',
                     f'--add-port={port}/tcp'],
                    check=True, timeout=15,
                )
                print_success(f"firewalld: allowed port {port} ({desc})")
            except subprocess.CalledProcessError:
                print_warning(f"Failed to add firewalld rule for port {port}")
            except FileNotFoundError:
                print_warning("sudo not found — run manually: "
                              f"sudo firewall-cmd --permanent --add-port={port}/tcp")
                break

        # Reload to apply permanent rules
        try:
            subprocess.run(
                ['sudo', 'firewall-cmd', '--reload'],
                check=False, timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            print_info("Run 'sudo firewall-cmd --reload' to activate the new rules.")


def check_dependencies():
    """Check if required packages are installed"""
    print_info("Checking dependencies...")

    required = [
        'fastapi',
        'uvicorn',
        'argon2-cffi',
        'redis',
        'pydantic',
        'python-dotenv',
        'cryptography',
        'requests',
        'aiohttp',
        'structlog',
        'sentry-sdk',
        'psutil',
        'starlette',
    ]

    # Map package names to their import names (for packages where they differ)
    PACKAGE_IMPORT_MAP = {
        'argon2-cffi': 'argon2',
        'python-dotenv': 'dotenv',
        'sentry-sdk': 'sentry_sdk',
    }

    missing = []
    for package in required:
        try:
            import_name = PACKAGE_IMPORT_MAP.get(package, package.replace('-', '_'))
            __import__(import_name)
        except ImportError:
            missing.append(package)

    if missing:
        print_warning(f"Missing packages: {', '.join(missing)}")
        # Install everything from requirements.txt to ensure all deps are present
        # (not just the subset we check — other modules need structlog, etc.)
        req_file = Path('requirements.txt')
        if ask_yes_no("Install all dependencies from requirements.txt?", default=True):
            try:
                if req_file.exists():
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '-q', '-r', str(req_file)],
                        check=True,
                    )
                else:
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install'] + missing,
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

def check_ollama_installed():
    """Check if Ollama is installed"""
    return shutil.which('ollama') is not None


def _refresh_windows_path():
    """Refresh the current process PATH from the registry (Windows only).

    After winget/msi installs, the system/user PATH is updated in the
    registry but the running process still has the old value.
    """
    import ctypes
    from ctypes import wintypes

    # Read the current Machine and User PATH from the registry
    machine_path = subprocess.run(
        ['reg', 'query', r'HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment', '/v', 'Path'],
        capture_output=True, text=True
    )
    user_path = subprocess.run(
        ['reg', 'query', r'HKCU\Environment', '/v', 'Path'],
        capture_output=True, text=True
    )

    parts = []
    for output in [machine_path.stdout, user_path.stdout]:
        for line in output.splitlines():
            if 'REG_' in line and 'Path' in line:
                # Format: "    Path    REG_EXPAND_SZ    value"
                value = line.split('REG_EXPAND_SZ', 1)[-1].split('REG_SZ', 1)[-1].strip()
                parts.append(value)

    if parts:
        os.environ['PATH'] = ';'.join(parts)


def install_ollama():
    """Install Ollama based on the current platform"""
    system = platform.system()

    if system == 'Linux':
        print_info("Installing Ollama via official install script...")
        try:
            subprocess.run(
                ['bash', '-c', 'curl -fsSL https://ollama.com/install.sh | sh'],
                check=True
            )
            print_success("Ollama installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Automatic Ollama installation failed")
            print_info("Please install manually: https://ollama.com/download/linux")
            return False

    elif system == 'Darwin':
        # Check if Homebrew is available
        if shutil.which('brew'):
            print_info("Installing Ollama via Homebrew...")
            try:
                subprocess.run(['brew', 'install', 'ollama'], check=True)
                print_success("Ollama installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass

        print_info("Installing Ollama via official install script...")
        try:
            subprocess.run(
                ['bash', '-c', 'curl -fsSL https://ollama.com/install.sh | sh'],
                check=True
            )
            print_success("Ollama installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Automatic Ollama installation failed")
            print_info("Please install manually: https://ollama.com/download/mac")
            return False

    elif system == 'Windows':
        # Try winget first (available on Windows 10 1709+ and Windows 11)
        if shutil.which('winget'):
            print_info("Installing Ollama via winget...")
            try:
                subprocess.run(['winget', 'install', 'Ollama.Ollama', '-e', '--accept-source-agreements'], check=True)
                # Refresh PATH so the current process can find the new binary
                _refresh_windows_path()
                print_success("Ollama installed successfully")
                return True
            except subprocess.CalledProcessError:
                pass

        print_error("Automatic Ollama installation failed")
        print_info("Please download and install from: https://ollama.com/download/windows")
        print_info("After installing, re-run this installer")
        return False

    else:
        print_error(f"Unsupported platform: {system}")
        print_info("Please install Ollama manually: https://ollama.com/download")
        return False


def ensure_ollama_running():
    """Ensure the Ollama service is running, start it if needed"""
    # Check if already running
    try:
        urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)
        print_success("Ollama service is running")
        return True
    except (urllib.error.URLError, OSError):
        pass

    print_info("Starting Ollama service...")

    system = platform.system()

    def _api_reachable():
        try:
            urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            return False

    if system == 'Linux':
        # Try systemctl first
        try:
            subprocess.run(['systemctl', 'start', 'ollama'], check=False)
            time.sleep(3)
            if _api_reachable():
                print_success("Ollama service started via systemd")
                return True
        except FileNotFoundError:
            pass

    elif system == 'Windows':
        # On Windows, Ollama runs as a background app from the user's Start Menu.
        # Try launching 'ollama app' via the installed shortcut.
        app_dir = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama'
        app_exe = app_dir / 'ollama app.exe'
        if app_exe.exists():
            try:
                kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                subprocess.Popen([str(app_exe)], **kwargs)
                time.sleep(3)
                if _api_reachable():
                    print_success("Ollama app started")
                    return True
            except OSError:
                pass

    # Fall back to starting ollama serve in the background
    try:
        kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if system == 'Windows':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(['ollama', 'serve'], **kwargs)
    except FileNotFoundError:
        print_error("Could not start Ollama - binary not found")
        return False

    # Wait for it to come up
    for i in range(15):
        time.sleep(2)
        try:
            urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)
            print_success("Ollama service started")
            return True
        except (urllib.error.URLError, OSError):
            pass

    print_error("Ollama service did not start within 30 seconds")
    print_info("Try starting manually: ollama serve")
    return False


def pull_default_model(model='qwen3.5:9b'):
    """Pull the default AI model via Ollama"""
    print_info(f"Checking for model '{model}'...")

    try:
        result = subprocess.run(
            ['ollama', 'list'],
            capture_output=True, text=True, check=True
        )
        # Match the exact model name at the start of a line to avoid
        # false positives like "qwen3.5:9b-instruct" matching "qwen3.5:9b"
        for line in result.stdout.splitlines():
            # ollama list output: "NAME  ID  SIZE  MODIFIED"
            line_model = line.split()[0] if line.strip() else ''
            if line_model == model:
                print_success(f"Model '{model}' is already available")
                return True
    except (subprocess.CalledProcessError, IndexError):
        pass

    print_info(f"Pulling model '{model}' (this may take several minutes)...")
    try:
        subprocess.run(['ollama', 'pull', model], check=True)
        print_success(f"Model '{model}' downloaded successfully")
        return True
    except subprocess.CalledProcessError:
        print_error(f"Failed to pull model '{model}'")
        print_info(f"You can retry later with: ollama pull {model}")
        return False


def choose_model(total_ram_gb: Optional[float] = None) -> str:
    """Choose a qwen3.5 model size based on system RAM.

    Returns the model tag to pull (e.g. 'qwen3.5:9b').
    """
    # Model options: tag, param count, approximate download size, minimum RAM
    models = [
        ('qwen3.5:0.8b', '0.8B', '~0.5 GB download', 2),
        ('qwen3.5:2b',   '2B',   '~1.3 GB download', 4),
        ('qwen3.5:4b',   '4B',   '~2.5 GB download', 6),
        ('qwen3.5:9b',   '9B',   '~5.5 GB download', 8),
        ('qwen3.5:27b',  '27B',  '~16 GB download',  24),
        ('qwen3.5:35b',  '35B',  '~22 GB download',  32),
    ]

    # Pick recommended model based on detected RAM
    if total_ram_gb is not None:
        recommended = 'qwen3.5:0.8b'  # fallback
        for tag, _, _, min_ram in models:
            if total_ram_gb >= min_ram:
                recommended = tag
    else:
        recommended = 'qwen3.5:9b'  # assumes 8 GB+ when RAM detection fails

    # Allow env var override
    env_model = os.getenv('OLLAMA_DEFAULT_MODEL')
    if env_model:
        print_info(f"Using model from OLLAMA_DEFAULT_MODEL: {env_model}")
        return env_model

    print_info("Choose an AI model size (all use the qwen3.5 family):\n")

    for i, (tag, params, size, min_ram) in enumerate(models, 1):
        rec = " ← recommended" if tag == recommended else ""
        ram_note = f"needs ~{min_ram} GB RAM"
        print(f"  {i}. {tag:<14} {params:>4} params   {size:<18} ({ram_note}){rec}")

    print(f"\n  s. Skip model download for now")

    if total_ram_gb is not None:
        print(f"\n  Your system: {total_ram_gb:.0f} GB RAM")

    # Find the index of the recommended model (1-based)
    rec_idx = next(
        (i for i, (tag, *_) in enumerate(models, 1) if tag == recommended),
        4,  # fallback index if recommended not found
    )

    while True:
        choice = ask_question(f"Select model (1-{len(models)}, or s to skip)", str(rec_idx))
        if choice.lower() == 's':
            return ''  # empty string means skip
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                tag = models[idx][0]
                min_ram = models[idx][3]
                if total_ram_gb is not None and total_ram_gb < min_ram:
                    print_warning(
                        f"Your system has {total_ram_gb:.0f} GB RAM but {tag} needs ~{min_ram} GB."
                    )
                    if not ask_yes_no("Continue anyway?", default=False):
                        continue
                return tag
        except ValueError:
            pass
        print_error(f"Please enter a number 1-{len(models)} or 's' to skip")


def detect_existing_model() -> str:
    """Check if Ollama already has a model pulled. Returns the model tag or ''."""
    try:
        result = subprocess.run(
            ['ollama', 'list'], capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines()[1:]:  # skip header row
            parts = line.split()
            if parts:
                return parts[0]  # return the first available model
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass
    return ''


def setup_ollama(total_ram_gb: Optional[float] = None) -> str:
    """Full Ollama setup: install, start, and pull model.

    Returns the chosen model tag (e.g. 'qwen3.5:9b'), or empty string on
    failure/skip so callers can persist the choice to .env.
    """
    print_header("Ollama Setup")

    print("""
snflwr.ai uses Ollama to run AI models locally.
This ensures all data stays on your device - nothing is sent to the cloud.
    """)

    # Step 1: Check/install Ollama
    if check_ollama_installed():
        print_success("Ollama is installed")
    else:
        print_warning("Ollama is not installed")
        if ask_yes_no("Install Ollama now?", default=True):
            if not install_ollama():
                print_warning("Skipping Ollama setup - you can install it later")
                return ''
        else:
            print_warning("Skipping Ollama setup")
            print_info("Install Ollama later from: https://ollama.com/download")
            return ''

    # Step 2: Ensure Ollama is running
    if not ensure_ollama_running():
        print_warning("Could not start Ollama service")
        print_info("Start it manually with: ollama serve")
        return ''

    # Step 3: Check for existing models before prompting
    existing_model = detect_existing_model()
    if existing_model:
        print_success(f"Found existing model: {existing_model}")
        if ask_yes_no(f"Use '{existing_model}' as the default model?", default=True):
            print_success("Ollama setup complete")
            return existing_model

    # Step 4: Choose and pull a model
    model = choose_model(total_ram_gb)
    if not model:
        if existing_model:
            print_info(f"Keeping existing model: {existing_model}")
            return existing_model
        print_info("Skipping model download. You can pull one later:")
        print_info("  ollama pull qwen3.5:9b")
        print_success("Ollama setup complete (no model pulled)")
        return ''

    if not pull_default_model(model):
        print_warning(f"Model pull failed - you can retry with: ollama pull {model}")
        return model  # still return the choice so .env records it

    print_success("Ollama setup complete")
    return model


def setup_safety_model(ollama_available: bool = True) -> bool:
    """Ask if children will use the system and optionally pull the safety model.

    The safety model (llama-guard3:1b) powers the ML-based semantic classifier
    in the content-safety pipeline.  Without it the deterministic pattern-
    matching stages still protect, but the LLM layer adds significantly deeper
    coverage.

    Returns True if the safety model should be enabled.
    """
    print_header("Child Safety Configuration")

    print("""
snflwr.ai includes a multi-layer content safety pipeline that filters
every message for age-inappropriate content, PII, and harmful material.

If children will be using this system, an additional AI safety model
(llama-guard3:1b, ~1 GB download) can be installed. This model adds a
semantic classification layer on top of the existing pattern-matching
filters for significantly stronger protection.
    """)

    enable = ask_yes_no("Will children be using this system?", default=True)

    if not enable:
        print_info("Safety model will not be downloaded.")
        print_info("Deterministic content filters are still active.")
        return False

    if not ollama_available:
        print_warning("Ollama is not available - safety model cannot be downloaded now.")
        print_info("After installing Ollama, run:  ollama pull llama-guard3:1b")
        return True  # Mark as enabled so .env records it for startup scripts

    print_info("Downloading safety model (llama-guard3:1b)...")
    if pull_default_model('llama-guard3:1b'):
        print_success("Safety model installed - semantic content classifier is active")
    else:
        print_warning("Safety model download failed.")
        print_info("You can retry later with:  ollama pull llama-guard3:1b")

    return True


def setup_family_deployment():
    """Set up family/USB deployment with SQLite"""
    print_header("Family/USB Deployment Setup")

    print("""
This mode is perfect for:
  - Individual families and homeschools
  - Privacy-focused parents who want data control
  - Offline operation (no internet required)
  - Simple plug-and-play deployment
    """)

    config = {}
    config['DATABASE_TYPE'] = 'sqlite'

    # Detect USB drives
    usb_drives = detect_usb_drives()

    if usb_drives:
        print_info(f"Found {len(usb_drives)} removable drive(s):")
        for i, drive in enumerate(usb_drives, 1):
            print(f"  {i}. {drive}")

        use_usb = ask_yes_no("\nStore data on USB drive for maximum privacy?", default=True)

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
                            print_error(f"Invalid choice. Please enter a number between 1 and {len(usb_drives)}")
                    except ValueError:
                        print_error("Invalid input. Please enter a number")

            # Create snflwr directory on USB
            snflwr_dir = usb_path / "SnflwrAI"
            try:
                snflwr_dir.mkdir(exist_ok=True)
                # Verify the drive is actually writable
                probe = snflwr_dir / '.snflwr_write_test'
                probe.write_text('ok')
                probe.unlink()
            except OSError:
                print_error(f"USB drive {usb_path} is not writable (read-only or full)")
                print_info("  Falling back to local storage instead.")
                use_usb = False

            if use_usb:
                config['SNFLWR_DATA_DIR'] = str(snflwr_dir)
                config['ENCRYPTION_KEY_PATH'] = str(snflwr_dir)
                config['LOG_PATH'] = str(snflwr_dir / "logs")
                config['USB_STORAGE'] = True

                print_success(f"Data will be stored on: {snflwr_dir}")

        if not use_usb:
            # Use local directory (user declined USB or drive was not writable)
            local_dir = Path.home() / "SnflwrAI"
            local_dir.mkdir(exist_ok=True)

            config['SNFLWR_DATA_DIR'] = str(local_dir)
            config['ENCRYPTION_KEY_PATH'] = str(local_dir)
            config['LOG_PATH'] = str(local_dir / "logs")

            print_success(f"Data will be stored locally: {local_dir}")
    else:
        print_warning("No USB drives detected")
        local_dir = Path.home() / "SnflwrAI"
        local_dir.mkdir(exist_ok=True)

        config['SNFLWR_DATA_DIR'] = str(local_dir)
        config['ENCRYPTION_KEY_PATH'] = str(local_dir)
        config['LOG_PATH'] = str(local_dir / "logs")

        print_info(f"Using local storage: {local_dir}")

    return config

def setup_enterprise_deployment():
    """Set up enterprise/PostgreSQL deployment"""
    print_header("Enterprise/Server Deployment Setup")

    print("""
This mode is perfect for:
  - School districts and institutions
  - Multi-user deployments (100+ students)
  - Cloud hosting platforms
  - Advanced analytics needs
    """)

    config = {}
    config['DATABASE_TYPE'] = 'postgresql'

    # PostgreSQL configuration
    print_info("PostgreSQL Database Configuration")

    config['POSTGRES_HOST'] = ask_question("Database host", "localhost")
    config['POSTGRES_PORT'] = ask_question("Database port", "5432")
    config['POSTGRES_USER'] = ask_question("Database user", "snflwr")
    config['POSTGRES_DATABASE'] = ask_question("Database name", "snflwr_ai")

    # Password
    auto_password = ask_yes_no("Generate secure database password?", default=True)
    if auto_password:
        config['POSTGRES_PASSWORD'] = generate_secure_token()
        print_success("Secure password generated")
    else:
        config['POSTGRES_PASSWORD'] = ask_question("Database password")

    # Enterprise service credentials
    # These are needed for Docker Compose, Kubernetes, and monitoring stack
    print_info("\nGenerating credentials for enterprise services...")

    config['WEBUI_SECRET_KEY'] = generate_secure_token()
    config['REDIS_PASSWORD'] = generate_secure_token()
    config['GRAFANA_PASSWORD'] = generate_secure_token()
    config['KIBANA_ENCRYPTION_KEY'] = secrets.token_hex(16)  # 32 chars exactly
    config['FLOWER_USER'] = 'admin'
    config['FLOWER_PASSWORD'] = generate_secure_token()
    config['FLOWER_ENABLED'] = True
    config['DB_ENCRYPTION_KEY'] = generate_secure_token()
    config['INTERNAL_API_KEY'] = generate_secure_token()

    print_success("Enterprise service credentials generated")
    print_info("All credentials will be saved to CREDENTIALS.md — keep this file safe!")

    return config

def setup_security():
    """Configure security settings"""
    print_header("Security Configuration")

    config = {}

    # JWT Secret
    print_info("Generating JWT secret key...")
    config['JWT_SECRET_KEY'] = generate_secure_token()
    print_success("JWT secret generated")

    # Parent Dashboard Password
    print_info("\nParent Dashboard Access")
    auto_dashboard = ask_yes_no("Generate secure dashboard password?", default=True)
    if auto_dashboard:
        config['PARENT_DASHBOARD_PASSWORD'] = generate_secure_token()
        print_success("Dashboard password generated")
        print_warning(f"Save this password: {_mask_secret(config['PARENT_DASHBOARD_PASSWORD'])} (saved to CREDENTIALS.md)")
    else:
        config['PARENT_DASHBOARD_PASSWORD'] = ask_question("Dashboard password")

    return config

def create_env_file(config):
    """Create .env file with configuration"""
    print_info("Creating .env file...")

    env_path = Path('.env')

    # Backup existing .env
    if env_path.exists():
        backup_path = Path('.env.backup')
        env_path.rename(backup_path)
        print_warning(f"Backed up existing .env to {backup_path}")

    # Write new .env with secure permissions from creation
    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write("# snflwr.ai Configuration\n")
        f.write(f"# Generated on {platform.node()} at {os.path.basename(os.getcwd())}\n\n")

        # Database settings
        f.write("# Database Configuration\n")
        f.write(f"DB_TYPE={config['DATABASE_TYPE']}\n")

        if config['DATABASE_TYPE'] == 'sqlite':
            f.write(f"SNFLWR_DATA_DIR={config['SNFLWR_DATA_DIR']}\n")
            if 'ENCRYPTION_KEY_PATH' in config:
                f.write(f"ENCRYPTION_KEY_PATH={config['ENCRYPTION_KEY_PATH']}\n")
            if 'LOG_PATH' in config:
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
        if 'OLLAMA_DEFAULT_MODEL' in config:
            f.write(f"\n# AI Model\n")
            f.write(f"OLLAMA_DEFAULT_MODEL={config['OLLAMA_DEFAULT_MODEL']}\n")

        # Safety model (llama-guard3:1b for semantic content classification)
        safety_val = 'true' if config.get('ENABLE_SAFETY_MODEL') else 'false'
        f.write(f"\n# Child Safety Model (llama-guard3:1b)\n")
        f.write(f"ENABLE_SAFETY_MODEL={safety_val}\n")

        # Enterprise service credentials (only for server deployments)
        if config['DATABASE_TYPE'] == 'postgresql':
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

    os.chmod(str(env_path), 0o600)  # Restrict to owner-only access
    print_success(".env file created")


def save_credentials_file(config):
    """Save all generated credentials to a human-readable file.

    This gives admins a single reference document they can print or store
    securely (e.g. on a USB drive, in a password manager, or in a safe).
    """
    from datetime import datetime

    creds_path = Path('CREDENTIALS.md')

    with open(creds_path, 'w') as f:
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
        if config['DATABASE_TYPE'] == 'sqlite':
            f.write(f"- **Type:** SQLite\n")
            data_dir = config.get('SNFLWR_DATA_DIR', 'data')
            f.write(f"- **Data Directory:** `{data_dir}`\n")
            f.write(f"- **Database:** `{data_dir}/snflwr.db`\n\n")
        else:
            f.write(f"- **Type:** PostgreSQL\n")
            f.write(f"- **Host:** `{config['POSTGRES_HOST']}:{config['POSTGRES_PORT']}`\n")
            f.write(f"- **Database:** `{config['POSTGRES_DATABASE']}`\n")
            f.write(f"- **User:** `{config['POSTGRES_USER']}`\n")
            f.write(f"- **Password:** (see .env file)\n\n")

        # Enterprise-only credentials
        if config['DATABASE_TYPE'] == 'postgresql':
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
        if 'OLLAMA_DEFAULT_MODEL' in config:
            f.write("## AI Model\n\n")
            f.write(f"- **Model:** `{config['OLLAMA_DEFAULT_MODEL']}`\n\n")

        f.write("---\n\n")
        f.write("All of these values are also stored in the `.env` file in your\n")
        f.write("installation directory. This document is a backup reference.\n")

    os.chmod(str(creds_path), 0o600)  # Restrict to owner-only access
    print_success(f"Credentials saved to {creds_path}")

    # Also save a copy to the USB drive if the user chose USB storage
    if config.get('USB_STORAGE'):
        usb_creds_dir = Path(config['SNFLWR_DATA_DIR'])
        if usb_creds_dir.exists():
            import shutil
            usb_creds_path = usb_creds_dir / 'CREDENTIALS.md'
            try:
                shutil.copy2(creds_path, usb_creds_path)
                print_success(f"Credentials also saved to USB: {usb_creds_path}")
            except OSError as e:
                print_warning(f"Could not copy credentials to USB drive: {e}")
                print_info(f"  You can manually copy CREDENTIALS.md to {usb_creds_dir}")

    print_warning("Store CREDENTIALS.md somewhere safe — it contains all your passwords!")


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

def run_validation():
    """Validate installation"""
    print_header("Validating Installation")

    checks = []

    # Check .env exists
    if Path('.env').exists():
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
            urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3)
            print_success("Ollama service reachable")
        except (urllib.error.URLError, OSError):
            print_warning("Ollama service not reachable (start with: ollama serve)")
    else:
        print_warning("Ollama not installed (AI features unavailable)")

    return all(checks)

def _windows_start_cmd():
    """Return the recommended start command for the user's current shell."""
    if _is_powershell():
        return ".\\start_snflwr.ps1"
    return "START_SNFLWR.bat"


def show_next_steps(config):
    """Show next steps to user"""
    print_header("Installation Complete!")

    print("snflwr.ai is ready to use!\n")

    print("Next Steps:\n")

    system = platform.system()

    if config['DATABASE_TYPE'] == 'sqlite':
        print("1. Start the application:")
        if system == 'Windows':
            print(f"   {_windows_start_cmd()}\n")
        else:
            print("   ./start_snflwr.sh\n")

        print("2. Open snflwr.ai in your browser:")
        print("   http://localhost:3000")
        print("   (opens automatically when you start the application)\n")

        print("3. Parent Dashboard Password:")
        print(f"   {_mask_secret(config['PARENT_DASHBOARD_PASSWORD'])}")
        print("   (saved to CREDENTIALS.md — keep this safe!)\n")

        data_dir = config.get('SNFLWR_DATA_DIR', '')
        if 'SnflwrAI' in data_dir:
            print("4. Your data is stored at:")
            print(f"   {data_dir}")
            print("   Keep this USB drive safe — it contains all your data!\n")

    else:
        print("1. Set up PostgreSQL database:")
        print(f"   createdb {config['POSTGRES_DATABASE']}\n")

        print("2. Run database migrations:")
        print("   python -m database.init_db\n")

        print("3. Start the application:")
        if system == 'Windows':
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
    if config['DATABASE_TYPE'] == 'sqlite':
        print("  Keep your USB drive in a safe place\n")

def ensure_venv():
    """Create a virtual environment and re-launch inside it.

    Modern Python (3.12+ on macOS/Homebrew, Ubuntu 23.04+, Fedora 38+) enforces
    PEP 668 which forbids global pip install.  Running inside a venv avoids this
    entirely and keeps the user's system clean.
    """
    # Already inside a venv — nothing to do
    if sys.prefix != sys.base_prefix:
        return

    venv_dir = Path('venv')

    if not venv_dir.exists():
        print_info("Creating Python virtual environment...")
        try:
            subprocess.run([sys.executable, '-m', 'venv', str(venv_dir)], check=True)
            print_success("Virtual environment created")
        except subprocess.CalledProcessError:
            print_error("Failed to create virtual environment")
            print_info("Try: python3 -m venv venv   (you may need to install python3-venv)")
            sys.exit(1)

    # Determine the venv Python path
    if platform.system() == 'Windows':
        venv_python = venv_dir / 'Scripts' / 'python.exe'
    else:
        venv_python = venv_dir / 'bin' / 'python'

    if not venv_python.exists():
        print_error(f"Virtual environment python not found at {venv_python}")
        sys.exit(1)

    print_info("Re-launching installer inside virtual environment...")
    # Re-exec this script under the venv interpreter.
    # On Windows, os.execv() does not properly inherit the console stdin,
    # which causes interactive prompts to lose user input.  Use
    # subprocess.run() instead so stdin/stdout/stderr are inherited.
    if platform.system() == 'Windows':
        result = subprocess.run(
            [str(venv_python), __file__] + sys.argv[1:],
        )
        sys.exit(result.returncode)
    else:
        os.execv(str(venv_python), [str(venv_python), __file__] + sys.argv[1:])


def launch_snflwr():
    """Launch the startup script, replacing this process."""
    system = platform.system()
    install_dir = Path(__file__).resolve().parent

    if system == 'Windows':
        bat = install_dir / 'START_SNFLWR.bat'
        ps1 = install_dir / 'start_snflwr.ps1'
        use_ps = _is_powershell()

        if use_ps and ps1.exists():
            ps_exe = shutil.which('pwsh') or shutil.which('powershell')
            if ps_exe:
                print_info("Launching start_snflwr.ps1...")
                result = subprocess.run([ps_exe, '-ExecutionPolicy', 'Bypass', '-File', str(ps1)], cwd=str(install_dir))
                sys.exit(result.returncode)

        # cmd.exe or PowerShell fallback — .bat works in both
        if bat.exists():
            print_info("Launching START_SNFLWR.bat...")
            result = subprocess.run(['cmd', '/c', str(bat)], cwd=str(install_dir))
            sys.exit(result.returncode)

        print_warning("Startup script not found")
    else:
        script = install_dir / 'start_snflwr.sh'
        if script.exists():
            print_info("Launching start_snflwr.sh...\n")
            os.execv('/bin/bash', ['/bin/bash', str(script)])
        else:
            print_warning(f"Startup script not found: {script}")


def create_desktop_shortcut():
    """Create a desktop shortcut and launcher for snflwr.ai.

    Automatically creates platform-appropriate shortcuts that launch the
    GUI launcher (``launcher/app.py``) using the project's venv Python.
    No user prompt — this always runs at the end of a successful install.
    """
    print_header("Creating Desktop Shortcut & Launcher")

    install_dir = Path(__file__).resolve().parent
    desktop = Path.home() / "Desktop"
    icon_png = install_dir / "assets" / "icon.png"
    icon_ico = install_dir / "assets" / "icon.ico"
    launcher_py = install_dir / "launcher" / "app.py"

    system = platform.system()

    # ── Resolve the venv python path for each platform ──────────
    if system == "Windows":
        venv_python = install_dir / "venv" / "Scripts" / "pythonw.exe"
    else:
        venv_python = install_dir / "venv" / "bin" / "python3"

    # ── Linux ───────────────────────────────────────────────────
    if system == "Linux":
        # Check whether the venv Python has tkinter (may need python3-tk package)
        has_tkinter = False
        try:
            check = subprocess.run(
                [str(venv_python), "-c", "import tkinter"],
                capture_output=True, timeout=10,
            )
            has_tkinter = (check.returncode == 0)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        if not has_tkinter:
            print_warning("tkinter not available — attempting to install python3-tk...")
            try:
                pkg_cmds = []
                if shutil.which("apt-get"):
                    pkg_cmds = ["sudo", "apt-get", "install", "-y", "python3-tk"]
                elif shutil.which("dnf"):
                    pkg_cmds = ["sudo", "dnf", "install", "-y", "python3-tkinter"]
                elif shutil.which("yum"):
                    pkg_cmds = ["sudo", "yum", "install", "-y", "python3-tkinter"]
                elif shutil.which("pacman"):
                    pkg_cmds = ["sudo", "pacman", "-S", "--noconfirm", "tk"]
                elif shutil.which("zypper"):
                    pkg_cmds = ["sudo", "zypper", "install", "-y", "python3-tk"]
                elif shutil.which("apk"):
                    pkg_cmds = ["sudo", "apk", "add", "py3-tkinter"]

                if pkg_cmds:
                    subprocess.run(pkg_cmds, check=True, timeout=60)
                    check = subprocess.run(
                        [str(venv_python), "-c", "import tkinter"],
                        capture_output=True, timeout=10,
                    )
                    has_tkinter = (check.returncode == 0)

            except (subprocess.SubprocessError, OSError):
                pass

            if not has_tkinter:
                print_warning("Could not install tkinter — GUI launcher will fall back to terminal.")
                print_info("  To enable the GUI later: sudo apt install python3-tk")

        if has_tkinter:
            exec_line = f'Exec="{venv_python}" "{launcher_py}"'
            use_terminal = "false"
        else:
            exec_line = f'Exec=bash "{install_dir}/start_snflwr.sh"'
            use_terminal = "true"

        desktop_entry = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=snflwr.ai\n"
            "Comment=Start the snflwr.ai safe learning platform\n"
            f"{exec_line}\n"
            f"Path={install_dir}\n"
            f"Icon={icon_png}\n"
            f"Terminal={use_terminal}\n"
            "Categories=Education;\n"
            "StartupNotify=true\n"
        )

        # Desktop shortcut
        if desktop.exists():
            shortcut_path = desktop / "snflwr.ai.desktop"
            try:
                shortcut_path.write_text(desktop_entry)
                shortcut_path.chmod(0o755)
                print_success(f"Desktop shortcut created: {shortcut_path}")
            except (PermissionError, OSError) as e:
                print_warning(f"Could not create desktop shortcut: {e}")

        # Application menu entry (shows in Activities / app launcher)
        app_dir = Path.home() / ".local" / "share" / "applications"
        try:
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / "snflwr-ai.desktop").write_text(desktop_entry)
            print_success("Added to application menu")
        except (PermissionError, OSError) as e:
            print_warning(f"Could not add to app menu: {e}")

    # ── Windows ─────────────────────────────────────────────────
    elif system == "Windows":
        # Resolve the real Desktop folder — may differ from ~/Desktop
        # when OneDrive folder redirection is enabled (common on Win 10/11).
        ps_exe = shutil.which('pwsh') or shutil.which('powershell')
        if ps_exe:
            try:
                _desk_result = subprocess.run(
                    [ps_exe, "-Command",
                     "[Environment]::GetFolderPath('Desktop')"],
                    capture_output=True, text=True, timeout=10,
                )
                _resolved = _desk_result.stdout.strip()
                if _desk_result.returncode == 0 and _resolved:
                    desktop = Path(_resolved)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass  # fall back to ~/Desktop

        shortcut_path = desktop / "snflwr.ai.lnk"
        shortcut_created = False
        if ps_exe:
            # Use venv pythonw so the launcher runs without a console window
            target = str(venv_python) if venv_python.exists() else "pythonw.exe"
            ps_script = (
                '$WshShell = New-Object -ComObject WScript.Shell\n'
                f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
                f'$Shortcut.TargetPath = "{target}"\n'
                f"$Shortcut.Arguments = '\"{launcher_py}\"'\n"
                f'$Shortcut.WorkingDirectory = "{install_dir}"\n'
                f'$Shortcut.IconLocation = "{icon_ico},0"\n'
                '$Shortcut.Description = "snflwr.ai Safe Learning Platform"\n'
                '$Shortcut.Save()\n'
            )
            try:
                subprocess.run(
                    [ps_exe, "-Command", ps_script],
                    capture_output=True, timeout=10,
                )
                if shortcut_path.exists():
                    print_success(f"Desktop shortcut created: {shortcut_path}")
                    shortcut_created = True
                else:
                    print_warning("PowerShell shortcut creation did not produce a file")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                print_warning(f"Could not create .lnk shortcut: {e}")

        # Fallback: .bat file (always works on Windows, no PowerShell needed)
        if not shortcut_created:
            try:
                bat_path = desktop / "snflwr.ai.bat"
                target = str(venv_python) if venv_python.exists() else "pythonw"
                bat_path.write_text(
                    f'@echo off\n'
                    f'cd /d "{install_dir}"\n'
                    f'start "" "{target}" "launcher\\app.py"\n'
                )
                print_success(f"Desktop shortcut created: {bat_path}")
            except (PermissionError, OSError) as e2:
                print_warning(f"Could not create desktop shortcut: {e2}")

        # Also add to Start Menu
        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if start_menu.exists() and ps_exe:
            sm_shortcut = start_menu / "snflwr.ai.lnk"
            target = str(venv_python) if venv_python.exists() else "pythonw.exe"
            sm_ps = (
                '$WshShell = New-Object -ComObject WScript.Shell\n'
                f'$Shortcut = $WshShell.CreateShortcut("{sm_shortcut}")\n'
                f'$Shortcut.TargetPath = "{target}"\n'
                f"$Shortcut.Arguments = '\"{launcher_py}\"'\n"
                f'$Shortcut.WorkingDirectory = "{install_dir}"\n'
                f'$Shortcut.IconLocation = "{icon_ico},0"\n'
                '$Shortcut.Description = "snflwr.ai Safe Learning Platform"\n'
                '$Shortcut.Save()\n'
            )
            try:
                subprocess.run([ps_exe, "-Command", sm_ps], capture_output=True, timeout=10)
                if sm_shortcut.exists():
                    print_success("Added to Start Menu")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass  # Start Menu is a nice-to-have, don't warn

    # ── macOS ───────────────────────────────────────────────────
    elif system == "Darwin":
        # Check whether the venv Python has tkinter (Homebrew Python often omits it)
        has_tkinter = False
        try:
            check = subprocess.run(
                [str(venv_python), "-c", "import tkinter"],
                capture_output=True, timeout=10,
            )
            has_tkinter = (check.returncode == 0)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        if not has_tkinter:
            print_warning("tkinter not available in the venv Python")
            print_info("  The GUI launcher requires tkinter. Shortcuts will use the terminal startup instead.")
            print_info("  To enable the GUI later, install python-tk:")
            print_info("    brew install python-tk")

        # Create a .command file on the Desktop
        if desktop.exists():
            shortcut_path = desktop / "snflwr.ai.command"
            try:
                if has_tkinter:
                    # GUI launcher: background & disown so Terminal.app window can close
                    shortcut_path.write_text(
                        f'#!/bin/bash\n'
                        f'cd "{install_dir}"\n'
                        f'"{venv_python}" launcher/app.py &\n'
                        f'disown\n'
                    )
                else:
                    # Terminal fallback: exec keeps Terminal.app open for output + Ctrl+C
                    shortcut_path.write_text(
                        f'#!/bin/bash\n'
                        f'cd "{install_dir}"\n'
                        f'exec bash start_snflwr.sh\n'
                    )
                shortcut_path.chmod(0o755)
                print_success(f"Desktop shortcut created: {shortcut_path}")
            except (PermissionError, OSError) as e:
                print_warning(f"Could not create desktop shortcut: {e}")

        # Create a minimal .app bundle in ~/Applications (nicer Dock/Launchpad icon)
        # Use user-local Applications to avoid needing sudo
        user_apps = Path.home() / "Applications"
        user_apps.mkdir(exist_ok=True)
        app_bundle = user_apps / "snflwr.ai.app"
        macos_dir = app_bundle / "Contents" / "MacOS"
        resources_dir = app_bundle / "Contents" / "Resources"
        try:
            macos_dir.mkdir(parents=True, exist_ok=True)
            resources_dir.mkdir(parents=True, exist_ok=True)

            # Executable wrapper script
            wrapper = macos_dir / "SnflwrAI"
            if has_tkinter:
                wrapper.write_text(
                    f'#!/bin/bash\n'
                    f'cd "{install_dir}"\n'
                    f'exec "{venv_python}" launcher/app.py\n'
                )
            else:
                # No tkinter — open Terminal.app so the user can see output
                wrapper.write_text(
                    f'#!/bin/bash\n'
                    f'open -a Terminal "{install_dir}/start_snflwr.sh"\n'
                )
            wrapper.chmod(0o755)

            # Info.plist
            plist = app_bundle / "Contents" / "Info.plist"
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
                ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                '<dict>\n'
                '  <key>CFBundleName</key>\n'
                '  <string>snflwr.ai</string>\n'
                '  <key>CFBundleDisplayName</key>\n'
                '  <string>snflwr.ai</string>\n'
                '  <key>CFBundleIdentifier</key>\n'
                '  <string>ai.snflwr.launcher</string>\n'
                '  <key>CFBundleVersion</key>\n'
                '  <string>1.0</string>\n'
                '  <key>CFBundleExecutable</key>\n'
                '  <string>SnflwrAI</string>\n'
                '  <key>CFBundleIconFile</key>\n'
                '  <string>icon</string>\n'
                '  <key>LSUIElement</key>\n'
                '  <false/>\n'
                '</dict>\n'
                '</plist>\n'
            )

            # Copy icon into Resources (macOS uses .icns but .png works as fallback)
            if icon_png.exists():
                shutil.copy2(icon_png, resources_dir / "icon.png")

            print_success("Added to Applications (Launchpad)")

        except PermissionError:
            print_warning("Could not create app bundle (permission denied)")
            print_info("Drag 'snflwr.ai.command' from your Desktop to the Dock instead")
        except OSError as e:
            print_warning(f"Could not create macOS app bundle: {e}")

    else:
        print_warning(f"Desktop shortcuts not supported on {system}")


def main():
    """Main installer flow"""
    print_header("snflwr.ai Interactive Installer")

    print("""
Welcome to snflwr.ai - K-12 Safe AI Learning Platform

This installer will guide you through setting up snflwr.ai
for your specific needs. The process takes about 2 minutes.
    """)

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
        print_warning("Ollama setup incomplete - AI features will not work until Ollama is configured")
        if not ask_yes_no("Continue with the rest of the setup anyway?", default=True):
            sys.exit(1)

    # Child safety -- ask whether to download the LLM safety model
    safety_model_enabled = setup_safety_model(ollama_available=bool(chosen_model))

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
        config['OLLAMA_DEFAULT_MODEL'] = chosen_model

    # Record child safety model preference
    config['ENABLE_SAFETY_MODEL'] = safety_model_enabled

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

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"\nInstallation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
