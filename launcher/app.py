#!/usr/bin/env python3
"""snflwr.ai Launcher — simple GUI for starting/stopping the platform."""

import os
import sys
import signal
import platform
import subprocess
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:
    system = platform.system()
    if system == "Darwin":
        print("tkinter is required. Install: brew install python-tk@3 (or brew install python)")
    elif system == "Windows":
        print("tkinter is required. Reinstall Python and check 'tcl/tk' in optional features.")
    else:
        print("tkinter is required. Install: sudo apt install python3-tk")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
SERVICES = [
    ("Ollama", "http://localhost:11434/api/tags"),
    ("snflwr.ai API", "http://localhost:39150/health"),
    ("Open WebUI", "http://localhost:3000"),
]
_SERVICE_INDEX = {name: i for i, (name, _url) in enumerate(SERVICES)}
POLL_MS = 3000
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_GRAY = "#9ca3af"
COLOR_BG = "#fefce8"         # warm cream background
COLOR_HEADER = "#ca8a04"     # snflwr amber
COLOR_BTN = "#eab308"        # yellow-500
COLOR_BTN_STOP = "#dc2626"   # red-600


class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.process = None
        self._running = False
        self._alive = True   # set to False on window close to stop polling
        self._dots = []      # list of (canvas, dot_id) tuples for status dots
        self._labels = []    # list of status label StringVars
        self._service_health = [False] * len(SERVICES)  # live health per service

        self._build_ui()
        self._poll()         # start first health check

    def _build_ui(self):
        self.root.title("snflwr.ai")
        self.root.geometry("400x480")
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        # Try to set window icon
        icon_path = BASE_DIR / "assets" / "icon.png"
        if icon_path.exists():
            try:
                self._icon = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self._icon)
            except Exception:
                pass

        # ── Header ───────────────────────────────────────────
        header_frame = tk.Frame(self.root, bg=COLOR_BG)
        header_frame.pack(pady=(30, 10))

        try:
            hfont = tkfont.Font(family="Helvetica", size=28, weight="bold")
        except Exception:
            hfont = tkfont.Font(size=28, weight="bold")

        tk.Label(
            header_frame, text="\U0001f33b", font=("", 40), bg=COLOR_BG
        ).pack()
        tk.Label(
            header_frame, text="snflwr.ai",
            font=hfont, fg=COLOR_HEADER, bg=COLOR_BG,
        ).pack()
        tk.Label(
            header_frame, text="Safe Learning Platform",
            font=("Helvetica", 11), fg="#78716c", bg=COLOR_BG,
        ).pack(pady=(2, 0))

        # ── Status indicators ────────────────────────────────
        status_frame = tk.Frame(self.root, bg=COLOR_BG)
        status_frame.pack(pady=20, padx=40, fill="x")

        for name, url in SERVICES:
            row = tk.Frame(status_frame, bg=COLOR_BG)
            row.pack(fill="x", pady=6)

            canvas = tk.Canvas(
                row, width=18, height=18, bg=COLOR_BG, highlightthickness=0
            )
            dot = canvas.create_oval(3, 3, 15, 15, fill=COLOR_GRAY, outline="")
            canvas.pack(side="left", padx=(0, 10))

            status_var = tk.StringVar(value="checking...")
            tk.Label(
                row, text=name, font=("Helvetica", 12, "bold"),
                fg="#1c1917", bg=COLOR_BG, width=14, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, textvariable=status_var,
                font=("Helvetica", 10), fg="#78716c", bg=COLOR_BG,
            ).pack(side="right")

            self._dots.append((canvas, dot))
            self._labels.append(status_var)

        # ── Buttons ──────────────────────────────────────────
        btn_frame = tk.Frame(self.root, bg=COLOR_BG)
        btn_frame.pack(pady=20)

        self._start_btn = tk.Button(
            btn_frame, text="Start Snflwr", command=self._toggle,
            font=("Helvetica", 14, "bold"),
            bg=COLOR_BTN, fg="white", activebackground="#d69e2e",
            relief="flat", padx=30, pady=12, cursor="hand2",
        )
        self._start_btn.pack(pady=6)

        self._browser_btn = tk.Button(
            btn_frame, text="Open in Browser", command=self._open_browser,
            font=("Helvetica", 11),
            bg="#f5f5f4", fg="#44403c", activebackground="#e7e5e4",
            relief="flat", padx=20, pady=8, cursor="hand2",
        )
        self._browser_btn.pack(pady=6)

        # ── Footer ───────────────────────────────────────────
        tk.Label(
            self.root, text="snflwr.ai v1.0",
            font=("Helvetica", 9), fg="#a8a29e", bg=COLOR_BG,
        ).pack(side="bottom", pady=10)

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Health checking ──────────────────────────────────────
    def _poll(self):
        """Kick off a background thread to check service health."""
        if not self._alive:
            return
        thread = threading.Thread(target=self._check_health, daemon=True)
        thread.start()
        self.root.after(POLL_MS, self._poll)

    def _check_health(self):
        """Check each service URL (runs in background thread)."""
        if not self._alive:
            return
        results = []
        for name, url in SERVICES:
            try:
                resp = urlopen(url, timeout=2)
                ok = 200 <= resp.getcode() < 400
            except Exception:
                ok = False
            results.append(ok)
        # Schedule UI update on main thread (only if window is still alive)
        if self._alive:
            try:
                self.root.after(0, self._update_status, results)
            except Exception:
                pass  # window already destroyed

    def _update_status(self, results: list):
        """Update the status dots, labels, and browser button (must run on main thread)."""
        self._service_health = list(results)
        for i, ok in enumerate(results):
            canvas, dot = self._dots[i]
            color = COLOR_GREEN if ok else COLOR_RED
            canvas.itemconfig(dot, fill=color)
            self._labels[i].set("running" if ok else "stopped")

        # Update browser button to reflect what will actually open
        webui_idx = _SERVICE_INDEX["Open WebUI"]
        api_idx = _SERVICE_INDEX["snflwr.ai API"]
        webui_up = results[webui_idx] if len(results) > webui_idx else False
        api_up = results[api_idx] if len(results) > api_idx else False
        if webui_up:
            self._browser_btn.config(text="Open Chat UI", state="normal")
        elif api_up:
            self._browser_btn.config(text="Open Admin Dashboard", state="normal")
        else:
            self._browser_btn.config(text="Open in Browser", state="disabled")

    # ── Start / Stop ─────────────────────────────────────────
    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.process and self.process.poll() is None:
            return  # already running

        system = platform.system()
        if system == "Windows":
            cmd = [str(BASE_DIR / "START_SNFLWR.bat"), "/headless"]
            self.process = subprocess.Popen(
                cmd, cwd=str(BASE_DIR),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            cmd = [str(BASE_DIR / "start_snflwr.sh"), "--headless"]
            self.process = subprocess.Popen(
                cmd, cwd=str(BASE_DIR),
                start_new_session=True,
            )

        self._running = True
        self._start_btn.config(
            text="Stop Snflwr", bg=COLOR_BTN_STOP,
            activebackground="#b91c1c",
        )

    def _stop(self):
        if self.process and self.process.poll() is None:
            try:
                system = platform.system()
                if system == "Windows":
                    subprocess.run(
                        ["taskkill", "/t", "/f", "/pid", str(self.process.pid)],
                        capture_output=True,
                    )
                else:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            # Reap the child process to avoid zombies
            try:
                self.process.wait(timeout=5)
            except Exception:
                pass

        # Stop Docker containers (detached, not killed by process group kill)
        compose_dir = BASE_DIR / "frontend" / "open-webui"
        compose_file = compose_dir / "docker-compose.yaml"
        hostnet_file = compose_dir / "docker-compose.hostnet.yaml"
        if compose_file.exists():
            try:
                cmd = ["docker", "compose", "-f", str(compose_file)]
                if platform.system() == "Linux" and hostnet_file.exists():
                    cmd.extend(["-f", str(hostnet_file)])
                cmd.append("down")
                subprocess.run(cmd, capture_output=True, timeout=15)
            except Exception:
                pass

        self.process = None
        self._running = False
        self._start_btn.config(
            text="Start Snflwr", bg=COLOR_BTN,
            activebackground="#d69e2e",
        )

    def _open_browser(self):
        webui_idx = _SERVICE_INDEX["Open WebUI"]
        api_idx = _SERVICE_INDEX["snflwr.ai API"]
        webui_up = self._service_health[webui_idx] if len(self._service_health) > webui_idx else False
        api_up = self._service_health[api_idx] if len(self._service_health) > api_idx else False
        if webui_up:
            webbrowser.open("http://localhost:3000")
        elif api_up:
            webbrowser.open("http://localhost:39150/admin")
        else:
            # Nothing is up yet — try the chat UI anyway in case it comes up
            webbrowser.open("http://localhost:3000")

    def _on_close(self):
        # Stop polling before destroying the window
        self._alive = False
        # Don't kill services on close — user may want them running
        self.root.destroy()


def main():
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
