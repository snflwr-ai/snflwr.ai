"""Platform/process helpers (PowerShell detection, Windows PATH refresh)."""

import os
import platform
import subprocess


def _is_powershell():
    """Detect whether the installer was launched from a PowerShell terminal.

    Walks up the process tree (parent, grandparent, ...) looking for pwsh.exe
    or powershell.exe.  This handles the common case where the chain is:

        PowerShell -> cmd.exe (setup.bat) -> python (install.py)

    Falls back to False (assume cmd.exe) if detection fails.
    """
    if platform.system() != "Windows":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_char * 260),
            ]

        # Take a snapshot of all processes
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

        # Build a lookup: pid -> (exe_name, parent_pid)
        procs = {}
        if kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                name = entry.szExeFile.decode("utf-8", errors="ignore").lower()
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
            if "pwsh" in name or "powershell" in name:
                return True
            pid = parent

    except (OSError, AttributeError, ValueError):
        pass
    return False


def _refresh_windows_path():
    """Refresh the current process PATH from the registry (Windows only).

    After winget/msi installs, the system/user PATH is updated in the
    registry but the running process still has the old value.
    """
    import ctypes
    from ctypes import wintypes

    # Read the current Machine and User PATH from the registry
    machine_path = subprocess.run(
        [
            "reg",
            "query",
            r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "/v",
            "Path",
        ],
        capture_output=True,
        text=True,
    )
    user_path = subprocess.run(
        ["reg", "query", r"HKCU\Environment", "/v", "Path"],
        capture_output=True,
        text=True,
    )

    parts = []
    for output in [machine_path.stdout, user_path.stdout]:
        for line in output.splitlines():
            if "REG_" in line and "Path" in line:
                # Format: "    Path    REG_EXPAND_SZ    value"
                value = (
                    line.split("REG_EXPAND_SZ", 1)[-1].split("REG_SZ", 1)[-1].strip()
                )
                parts.append(value)

    if parts:
        os.environ["PATH"] = ";".join(parts)


def _windows_start_cmd():
    """Return the recommended start command for the user's current shell."""
    if _is_powershell():
        return ".\\start_snflwr.ps1"
    return "START_SNFLWR.bat"
