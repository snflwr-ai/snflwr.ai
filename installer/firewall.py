"""System firewall configuration for snflwr.ai's local ports."""

import platform
import shutil
import subprocess

from .ui import ask_yes_no, print_header, print_info, print_success, print_warning


# ── Firewall ──────────────────────────────────────────────────────────

# Ports used by snflwr.ai (port, description)
SNFLWR_PORTS = [
    (3000, "Open WebUI (chat interface)"),
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

    if system == "Windows":
        _configure_firewall_windows()
    elif system == "Darwin":
        _configure_firewall_macos()
    elif system == "Linux":
        _configure_firewall_linux()
    else:
        print_info(f"Firewall configuration not supported on {system}.")
        print_info(
            "If you experience connection issues, ensure the ports above are allowed."
        )


def _configure_firewall_windows():
    """Add Windows Firewall rules for snflwr.ai ports."""
    # Check if Windows Firewall is active
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "show", "currentprofile", "state"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "OFF" in result.stdout.upper():
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
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "show",
                    "rule",
                    f"name={rule_name}",
                    "dir=in",
                ],
                capture_output=True,
                text=True,
                timeout=10,
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
        print_warning(
            "Skipped — if you have connection issues, add the rules manually."
        )
        return

    for port, desc in needed:
        rule_name = f"snflwr.ai - port {port}"
        try:
            subprocess.run(
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "add",
                    "rule",
                    f"name={rule_name}",
                    "dir=in",
                    "action=allow",
                    "protocol=tcp",
                    f"localport={port}",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            print_success(f"Allowed port {port} ({desc})")
        except subprocess.CalledProcessError:
            print_warning(
                f"Failed to add rule for port {port} — you may need to run as Administrator"
            )
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
    fw_tool = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    try:
        result = subprocess.run(
            [fw_tool, "--getglobalstate"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "disabled" in result.stdout.lower():
            print_success("macOS Firewall is disabled — no configuration needed")
            return
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        print_info("Could not detect macOS Firewall state — skipping")
        return

    print_info("macOS Firewall is active.")
    print_info("Localhost traffic is not blocked, but macOS may show an")
    print_info('"Allow incoming connections" dialog when services start.')
    print()

    # Detect binaries we can pre-authorize
    binaries = []
    ollama_path = shutil.which("ollama")
    if ollama_path:
        binaries.append(("Ollama", ollama_path))
    docker_path = shutil.which("docker")
    if docker_path:
        binaries.append(("Docker", docker_path))

    if not binaries:
        print_info("No services installed yet to pre-authorize. If macOS shows a")
        print_info(
            'firewall dialog during startup, click "Allow" to permit the connection.'
        )
        return

    if not ask_yes_no(
        "Pre-authorize snflwr.ai services in the macOS Firewall?", default=True
    ):
        print_info(
            'Skipped — click "Allow" if macOS shows a firewall dialog during startup.'
        )
        return

    for name, path in binaries:
        try:
            subprocess.run(
                ["sudo", fw_tool, "--add", path],
                check=False,
                timeout=30,
            )
            subprocess.run(
                ["sudo", fw_tool, "--unblockapp", path],
                check=False,
                timeout=10,
            )
            print_success(f"Authorized {name} ({path})")
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            PermissionError,
            OSError,
        ) as e:
            print_warning(f"Could not authorize {name}: {e}")

    print_info('If macOS still shows a firewall dialog, click "Allow".')


def _configure_firewall_linux():
    """Add firewall rules on Linux (ufw or firewalld)."""
    # Detect active firewall
    fw_type = None

    # Check ufw
    if shutil.which("ufw"):
        # Try without sudo first (works on some distros), then with passwordless sudo
        for ufw_cmd in [["ufw", "status"], ["sudo", "-n", "ufw", "status"]]:
            try:
                result = subprocess.run(
                    ufw_cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if "active" in result.stdout.lower():
                    fw_type = "ufw"
                    break
            except (
                subprocess.TimeoutExpired,
                FileNotFoundError,
                PermissionError,
                OSError,
            ):
                continue

    # Check firewalld
    if not fw_type and shutil.which("firewall-cmd"):
        try:
            result = subprocess.run(
                ["firewall-cmd", "--state"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "running" in result.stdout.lower():
                fw_type = "firewalld"
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            PermissionError,
            OSError,
        ):
            pass

    if not fw_type:
        print_success("No active firewall detected (ufw/firewalld) — no rules needed")
        print_info(
            "If you use iptables/nftables directly, ensure these ports are allowed:"
        )
        for port, desc in SNFLWR_PORTS:
            print_info(f"  tcp/{port}  ({desc})")
        return

    print_info(f"Detected active firewall: {fw_type}")
    print()
    if not ask_yes_no(f"Add {fw_type} rules to allow snflwr.ai ports?", default=True):
        print_warning(
            "Skipped — if you have connection issues, add the rules manually."
        )
        return

    if fw_type == "ufw":
        for port, desc in SNFLWR_PORTS:
            try:
                subprocess.run(
                    [
                        "sudo",
                        "ufw",
                        "allow",
                        f"{port}/tcp",
                        "comment",
                        f"snflwr.ai - {desc}",
                    ],
                    check=True,
                    timeout=15,
                )
                print_success(f"ufw: allowed port {port} ({desc})")
            except subprocess.CalledProcessError:
                print_warning(f"Failed to add ufw rule for port {port}")
            except FileNotFoundError:
                print_warning(
                    "sudo not found — run manually: sudo ufw allow {port}/tcp"
                )
                break

    elif fw_type == "firewalld":
        for port, desc in SNFLWR_PORTS:
            try:
                subprocess.run(
                    ["sudo", "firewall-cmd", "--permanent", f"--add-port={port}/tcp"],
                    check=True,
                    timeout=15,
                )
                print_success(f"firewalld: allowed port {port} ({desc})")
            except subprocess.CalledProcessError:
                print_warning(f"Failed to add firewalld rule for port {port}")
            except FileNotFoundError:
                print_warning(
                    "sudo not found — run manually: "
                    f"sudo firewall-cmd --permanent --add-port={port}/tcp"
                )
                break

        # Reload to apply permanent rules
        try:
            subprocess.run(
                ["sudo", "firewall-cmd", "--reload"],
                check=False,
                timeout=15,
            )
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            PermissionError,
            OSError,
        ):
            print_info("Run 'sudo firewall-cmd --reload' to activate the new rules.")
