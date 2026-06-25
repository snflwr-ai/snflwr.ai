"""Launch the startup script and create desktop/app shortcuts."""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .platform_utils import _is_powershell
from .ui import print_header, print_info, print_success, print_warning


def _install_dir() -> Path:
    """Repo root (the directory containing install.py and this package)."""
    return Path(__file__).resolve().parent.parent


def launch_snflwr():
    """Launch the startup script, replacing this process."""
    system = platform.system()
    install_dir = _install_dir()

    if system == "Windows":
        bat = install_dir / "START_SNFLWR.bat"
        ps1 = install_dir / "start_snflwr.ps1"
        use_ps = _is_powershell()

        if use_ps and ps1.exists():
            ps_exe = shutil.which("pwsh") or shutil.which("powershell")
            if ps_exe:
                print_info("Launching start_snflwr.ps1...")
                result = subprocess.run(
                    [ps_exe, "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                    cwd=str(install_dir),
                )
                sys.exit(result.returncode)

        # cmd.exe or PowerShell fallback — .bat works in both
        if bat.exists():
            print_info("Launching START_SNFLWR.bat...")
            result = subprocess.run(["cmd", "/c", str(bat)], cwd=str(install_dir))
            sys.exit(result.returncode)

        print_warning("Startup script not found")
    else:
        script = install_dir / "start_snflwr.sh"
        if script.exists():
            print_info("Launching start_snflwr.sh...\n")
            os.execv("/bin/bash", ["/bin/bash", str(script)])
        else:
            print_warning(f"Startup script not found: {script}")


def create_desktop_shortcut():
    """Create a desktop shortcut and launcher for snflwr.ai.

    Automatically creates platform-appropriate shortcuts that launch the
    GUI launcher (``launcher/app.py``) using the project's venv Python.
    No user prompt — this always runs at the end of a successful install.
    """
    print_header("Creating Desktop Shortcut & Launcher")

    install_dir = _install_dir()
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
                capture_output=True,
                timeout=10,
            )
            has_tkinter = check.returncode == 0
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
                        capture_output=True,
                        timeout=10,
                    )
                    has_tkinter = check.returncode == 0

            except (subprocess.SubprocessError, OSError):
                pass

            if not has_tkinter:
                print_warning(
                    "Could not install tkinter — GUI launcher will fall back to terminal."
                )
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
        ps_exe = shutil.which("pwsh") or shutil.which("powershell")
        if ps_exe:
            try:
                _desk_result = subprocess.run(
                    [ps_exe, "-Command", "[Environment]::GetFolderPath('Desktop')"],
                    capture_output=True,
                    text=True,
                    timeout=10,
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
                "$WshShell = New-Object -ComObject WScript.Shell\n"
                f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
                f'$Shortcut.TargetPath = "{target}"\n'
                f"$Shortcut.Arguments = '\"{launcher_py}\"'\n"
                f'$Shortcut.WorkingDirectory = "{install_dir}"\n'
                f'$Shortcut.IconLocation = "{icon_ico},0"\n'
                '$Shortcut.Description = "snflwr.ai Safe Learning Platform"\n'
                "$Shortcut.Save()\n"
            )
            try:
                subprocess.run(
                    [ps_exe, "-Command", ps_script],
                    capture_output=True,
                    timeout=10,
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
                    f"@echo off\n"
                    f'cd /d "{install_dir}"\n'
                    f'start "" "{target}" "launcher\\app.py"\n'
                )
                print_success(f"Desktop shortcut created: {bat_path}")
            except (PermissionError, OSError) as e2:
                print_warning(f"Could not create desktop shortcut: {e2}")

        # Also add to Start Menu
        start_menu = (
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
        )
        if start_menu.exists() and ps_exe:
            sm_shortcut = start_menu / "snflwr.ai.lnk"
            target = str(venv_python) if venv_python.exists() else "pythonw.exe"
            sm_ps = (
                "$WshShell = New-Object -ComObject WScript.Shell\n"
                f'$Shortcut = $WshShell.CreateShortcut("{sm_shortcut}")\n'
                f'$Shortcut.TargetPath = "{target}"\n'
                f"$Shortcut.Arguments = '\"{launcher_py}\"'\n"
                f'$Shortcut.WorkingDirectory = "{install_dir}"\n'
                f'$Shortcut.IconLocation = "{icon_ico},0"\n'
                '$Shortcut.Description = "snflwr.ai Safe Learning Platform"\n'
                "$Shortcut.Save()\n"
            )
            try:
                subprocess.run(
                    [ps_exe, "-Command", sm_ps], capture_output=True, timeout=10
                )
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
                capture_output=True,
                timeout=10,
            )
            has_tkinter = check.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        if not has_tkinter:
            print_warning("tkinter not available in the venv Python")
            print_info(
                "  The GUI launcher requires tkinter. Shortcuts will use the terminal startup instead."
            )
            print_info("  To enable the GUI later, install python-tk:")
            print_info("    brew install python-tk")

        # Create a .command file on the Desktop
        if desktop.exists():
            shortcut_path = desktop / "snflwr.ai.command"
            try:
                if has_tkinter:
                    # GUI launcher: background & disown so Terminal.app window can close
                    shortcut_path.write_text(
                        f"#!/bin/bash\n"
                        f'cd "{install_dir}"\n'
                        f'"{venv_python}" launcher/app.py &\n'
                        f"disown\n"
                    )
                else:
                    # Terminal fallback: exec keeps Terminal.app open for output + Ctrl+C
                    shortcut_path.write_text(
                        f"#!/bin/bash\n"
                        f'cd "{install_dir}"\n'
                        f"exec bash start_snflwr.sh\n"
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
                    f"#!/bin/bash\n"
                    f'cd "{install_dir}"\n'
                    f'exec "{venv_python}" launcher/app.py\n'
                )
            else:
                # No tkinter — open Terminal.app so the user can see output
                wrapper.write_text(
                    f"#!/bin/bash\n"
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
                "<dict>\n"
                "  <key>CFBundleName</key>\n"
                "  <string>snflwr.ai</string>\n"
                "  <key>CFBundleDisplayName</key>\n"
                "  <string>snflwr.ai</string>\n"
                "  <key>CFBundleIdentifier</key>\n"
                "  <string>ai.snflwr.launcher</string>\n"
                "  <key>CFBundleVersion</key>\n"
                "  <string>1.0</string>\n"
                "  <key>CFBundleExecutable</key>\n"
                "  <string>SnflwrAI</string>\n"
                "  <key>CFBundleIconFile</key>\n"
                "  <string>icon</string>\n"
                "  <key>LSUIElement</key>\n"
                "  <false/>\n"
                "</dict>\n"
                "</plist>\n"
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
