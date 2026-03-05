# ui/launcher.py
"""
Universal Launcher - snflwr.ai Main Entry Point
Cross-platform GUI launcher with automatic detection and routing
"""

import sys
import threading
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Optional, Tuple
import platform

try:
    import customtkinter as ctk
except ImportError:
    # Fallback to standard tkinter if customtkinter not available
    ctk = None

from config import system_config
from core.authentication import auth_manager
from core.partition_detector import partition_detector
from utils.logger import get_logger, log_system_startup

logger = get_logger(__name__)


class LauncherWindow:
    """
    Universal launcher window with platform detection and routing
    Non-technical parents must succeed in <5 minutes
    """
    
    def __init__(self):
        """Initialize launcher window"""
        self.root = None
        self.status_label = None
        self.progress_label = None
        self.action_button = None
        self.error_frame = None

        # Detection results
        self.partitions_detected = False
        self.cdrom_path: Optional[Path] = None
        self.data_path: Optional[Path] = None
        self.has_existing_account = False

        # UI dimensions
        self.window_width = 800
        self.window_height = 600

        # Login form widgets (embedded inline)
        self.login_frame = None
        self.username_entry = None
        self.password_entry = None
        self.login_error_label = None
        self.sign_in_button = None
        self.browser_button = None

        # Authenticated session data
        self.session_data = None

        logger.info("Launcher initialized")
    
    def create_window(self):
        """Create main launcher window"""
        try:
            # Use customtkinter if available, fallback to tkinter
            if ctk:
                self.root = ctk.CTk()
                ctk.set_appearance_mode("light")
                ctk.set_default_color_theme("blue")
            else:
                self.root = tk.Tk()
            
            self.root.title(f"{system_config.APPLICATION_NAME} - Launcher")
            self.root.geometry(f"{self.window_width}x{self.window_height}")
            self.root.resizable(False, False)
            
            # Center window on screen
            self._center_window()

            # Ensure clicking X runs cleanup instead of just destroying widgets
            self.root.protocol("WM_DELETE_WINDOW", self._shutdown)

            # Create UI components
            self._create_ui()

            # Start detection process
            self._start_detection_process()

            logger.info("Launcher window created successfully")
            
        except Exception as e:
            logger.error(f"Failed to create launcher window: {e}")
            self._show_critical_error(
                "Startup Error",
                f"Failed to create application window.\n\nError: {str(e)}\n\n"
                "Please contact support if this problem persists."
            )
            sys.exit(1)
    
    def _center_window(self):
        """Center window on screen"""
        try:
            self.root.update_idletasks()
            
            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # Calculate position
            x = (screen_width - self.window_width) // 2
            y = (screen_height - self.window_height) // 2
            
            self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")
            
        except Exception as e:
            logger.warning(f"Failed to center window: {e}")
    
    def _create_ui(self):
        """Create launcher UI components"""
        try:
            # Main container
            if ctk:
                main_frame = ctk.CTkFrame(self.root)
            else:
                main_frame = tk.Frame(self.root, bg="white")
            main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
            
            # Logo/Title section
            title_frame = self._create_title_section(main_frame)
            title_frame.pack(fill=tk.X, pady=(0, 30))
            
            # Status section
            status_frame = self._create_status_section(main_frame)
            status_frame.pack(fill=tk.BOTH, expand=True)
            
            # Action button section
            button_frame = self._create_button_section(main_frame)
            button_frame.pack(fill=tk.X, pady=(20, 0))
            
            # Error display section (hidden by default)
            self.error_frame = self._create_error_section(main_frame)
            
        except Exception as e:
            logger.error(f"Failed to create UI: {e}")
            raise
    
    def _create_title_section(self, parent) -> tk.Frame:
        """Create title section with logo and app name"""
        if ctk:
            frame = ctk.CTkFrame(parent, fg_color="transparent")
        else:
            frame = tk.Frame(parent, bg="white")
        
        # Application title
        title_text = system_config.APPLICATION_NAME
        if ctk:
            title = ctk.CTkLabel(
                frame,
                text=title_text,
                font=("Arial", 28, "bold")
            )
        else:
            title = tk.Label(
                frame,
                text=title_text,
                font=("Arial", 28, "bold"),
                bg="white"
            )
        title.pack()
        
        # Version info
        version_text = f"Version {system_config.VERSION}"
        if ctk:
            version = ctk.CTkLabel(
                frame,
                text=version_text,
                font=("Arial", 12)
            )
        else:
            version = tk.Label(
                frame,
                text=version_text,
                font=("Arial", 12),
                bg="white",
                fg="gray"
            )
        version.pack()
        
        return frame
    
    def _create_status_section(self, parent) -> tk.Frame:
        """Create status display section"""
        if ctk:
            frame = ctk.CTkFrame(parent)
        else:
            frame = tk.Frame(parent, bg="#f0f0f0", relief=tk.RIDGE, bd=2)
        
        # Status title
        if ctk:
            status_title = ctk.CTkLabel(
                frame,
                text="System Status",
                font=("Arial", 18, "bold")
            )
        else:
            status_title = tk.Label(
                frame,
                text="System Status",
                font=("Arial", 18, "bold"),
                bg="#f0f0f0"
            )
        status_title.pack(pady=(20, 10))
        
        # Status message
        if ctk:
            self.status_label = ctk.CTkLabel(
                frame,
                text="Initializing...",
                font=("Arial", 14),
                wraplength=700
            )
        else:
            self.status_label = tk.Label(
                frame,
                text="Initializing...",
                font=("Arial", 14),
                bg="#f0f0f0",
                wraplength=700,
                justify=tk.CENTER
            )
        self.status_label.pack(pady=10)
        
        # Progress details
        if ctk:
            self.progress_label = ctk.CTkLabel(
                frame,
                text="",
                font=("Arial", 12),
                wraplength=700
            )
        else:
            self.progress_label = tk.Label(
                frame,
                text="",
                font=("Arial", 12),
                bg="#f0f0f0",
                fg="gray",
                wraplength=700,
                justify=tk.CENTER
            )
        self.progress_label.pack(pady=(0, 20))
        
        return frame
    
    def _create_button_section(self, parent) -> tk.Frame:
        """Create action button section"""
        if ctk:
            frame = ctk.CTkFrame(parent, fg_color="transparent")
        else:
            frame = tk.Frame(parent, bg="white")
        
        # Action button (initially hidden)
        if ctk:
            self.action_button = ctk.CTkButton(
                frame,
                text="Continue",
                font=("Arial", 14, "bold"),
                height=40,
                width=200,
                command=self._on_action_button_click
            )
        else:
            self.action_button = tk.Button(
                frame,
                text="Continue",
                font=("Arial", 14, "bold"),
                height=2,
                width=20,
                bg="#007AFF",
                fg="white",
                command=self._on_action_button_click
            )
        
        # Don't pack yet - will be shown after detection
        
        return frame
    
    def _create_error_section(self, parent) -> tk.Frame:
        """Create error display section (hidden by default)"""
        if ctk:
            frame = ctk.CTkFrame(parent, fg_color="#ffebee")
        else:
            frame = tk.Frame(parent, bg="#ffebee", relief=tk.RIDGE, bd=2)
        
        # Error title
        if ctk:
            error_title = ctk.CTkLabel(
                frame,
                text="[WARN] Setup Required",
                font=("Arial", 16, "bold"),
                text_color="#c62828"
            )
        else:
            error_title = tk.Label(
                frame,
                text="[WARN] Setup Required",
                font=("Arial", 16, "bold"),
                bg="#ffebee",
                fg="#c62828"
            )
        error_title.pack(pady=(10, 5))
        
        # Error message
        if ctk:
            error_message = ctk.CTkLabel(
                frame,
                text="",
                font=("Arial", 12),
                wraplength=700
            )
        else:
            error_message = tk.Label(
                frame,
                text="",
                font=("Arial", 12),
                bg="#ffebee",
                wraplength=700,
                justify=tk.LEFT
            )
        error_message.pack(pady=(5, 10), padx=20)
        
        return frame
    
    def _start_detection_process(self):
        """Start system detection in background thread"""
        detection_thread = threading.Thread(
            target=self._run_detection,
            daemon=True
        )
        detection_thread.start()
    
    def _run_detection(self):
        """Run detection sequence (runs in background thread).

        Supports four deployment modes (configured via SNFLWR_DEPLOY_MODE):
        - auto:        Try USB first, fall back to local install (default)
        - usb:         Strict USB-only (original behaviour)
        - local:       Skip USB, use local APP_DATA_DIR
        - thin_client: Connect to a management server for config/updates
        """
        try:
            deploy_mode = system_config.DEPLOY_MODE
            logger.info(f"Detection started — deploy mode: {deploy_mode}")

            # ── thin_client mode ──────────────────────────────────
            if deploy_mode == 'thin_client':
                self._run_thin_client_detection()
                return

            # ── USB detection (usb + auto modes) ──────────────────
            if deploy_mode in ('usb', 'auto'):
                self._update_status("Detecting snflwr.ai device...", "Checking for USB device...")
                usb = partition_detector.find_snflwr_usb()

                if usb is not None:
                    self.partitions_detected = True
                    self.data_path = usb
                    self._update_status("Device found!", f"Data: {usb}")

                    # Verify write access
                    self._update_status("Verifying device integrity...", "Checking system files...")
                    if not partition_detector.is_writable(usb):
                        self._show_partition_error(
                            "USB partition is not writable.\n\n"
                            "Please ensure the device is not write-protected."
                        )
                        return

                    self._check_existing_account()
                    return

                # Strict USB mode — fail if no USB found
                if deploy_mode == 'usb':
                    self._show_partition_error("No snflwr.ai USB device found.")
                    return

            # ── Local install detection (auto fallback + local mode) ──
            self._update_status("Checking local installation...", "Looking for snflwr.ai data...")
            local_path = partition_detector.find_local_install()

            if local_path is not None:
                self.partitions_detected = True
                self.data_path = local_path
                self._update_status("Local installation found!", f"Data: {local_path}")
                self._check_existing_account()
                return

            # Nothing found — create data dir and show first-time setup
            try:
                system_config.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
                self.data_path = system_config.APP_DATA_DIR
            except OSError as e:
                logger.warning(f"Could not create data directory: {e}")
            self._show_setup_ready()

        except Exception as e:
            logger.error(f"Detection process failed: {e}")
            self._show_detection_error(str(e))

    def _check_existing_account(self):
        """Check for existing accounts and route to login or setup."""
        self._update_status("Checking for existing account...", "")
        if self._accounts_exist():
            self.has_existing_account = True
            self._show_login_ready()
        else:
            self._show_setup_ready()

    def _run_thin_client_detection(self):
        """Connect to management server for thin-client deployments."""
        server_url = system_config.MANAGEMENT_SERVER_URL
        if not server_url:
            self._show_detection_error(
                "Thin client mode requires MANAGEMENT_SERVER_URL to be set.\n"
                "Contact your school's IT administrator."
            )
            return

        self._update_status("Connecting to management server...", server_url)

        try:
            from core.thin_client import ThinClientManager
        except ImportError:
            self._show_detection_error(
                "Thin client module not available.\n"
                "Please update your snflwr.ai installation."
            )
            return

        manager = ThinClientManager(server_url, system_config.APP_DATA_DIR)
        manifest = manager.fetch_manifest()

        if manifest is None:
            self._show_detection_error(
                "Could not connect to the management server.\n"
                "Check your network connection and try again."
            )
            return

        # Apply server-pushed configuration
        manager.apply_config(manifest)

        welcome = manifest.get('message', 'Connected to management server')
        self._update_status(welcome, f"Version: {manifest.get('version', 'unknown')}")

        # Check for launcher updates
        if manager.check_update_available(manifest):
            self._update_status("Updating launcher...", "Downloading latest version...")
            manager.download_update(manifest, system_config.APP_DATA_DIR)

        # Ensure data directory exists and proceed to account check
        system_config.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.partitions_detected = True
        self.data_path = system_config.APP_DATA_DIR
        self._check_existing_account()
    
    def _update_status(self, status: str, progress: str):
        """Update status labels (thread-safe)"""
        def update():
            if self.status_label:
                if ctk:
                    self.status_label.configure(text=status)
                else:
                    self.status_label.config(text=status)
            
            if self.progress_label:
                if ctk:
                    self.progress_label.configure(text=progress)
                else:
                    self.progress_label.config(text=progress)
            
            self.root.update()
        
        self.root.after(0, update)
    
    def _show_partition_error(self, error_message: str):
        """Show partition detection error"""
        def show():
            self._update_status(
                "snflwr.ai Device Not Found",
                ""
            )
            
            # Show error frame with helpful message
            if self.error_frame:
                # Update error message
                for widget in self.error_frame.winfo_children():
                    if isinstance(widget, (tk.Label, ctk.CTkLabel)):
                        if "[WARN]" not in widget.cget("text"):
                            error_text = (
                                f"{error_message}\n\n"
                                "Please:\n"
                                "1. Insert your snflwr.ai USB device\n"
                                "2. Wait a few seconds for it to be recognized\n"
                                "3. Click 'Retry' below\n\n"
                                "If you don't have a snflwr.ai device yet, "
                                "visit snflwr.ai to purchase."
                            )
                            if ctk:
                                widget.configure(text=error_text)
                            else:
                                widget.config(text=error_text)
                
                self.error_frame.pack(fill=tk.X, pady=(20, 0))
            
            # Show retry button
            if self.action_button:
                if ctk:
                    self.action_button.configure(text="Retry Detection")
                else:
                    self.action_button.config(text="Retry Detection")
                self.action_button.pack()
        
        self.root.after(0, show)
    
    def _show_detection_error(self, error_message: str):
        """Show general detection error"""
        def show():
            self._update_status(
                "Setup Error",
                ""
            )
            
            messagebox.showerror(
                "Detection Error",
                f"An error occurred during system detection:\n\n{error_message}\n\n"
                "Please try restarting the application. If the problem persists, "
                "contact support."
            )
            
            # Show exit button
            if self.action_button:
                if ctk:
                    self.action_button.configure(text="Exit")
                else:
                    self.action_button.config(text="Exit")
                self.action_button.pack()
        
        self.root.after(0, show)
    
    def _show_setup_ready(self):
        """Show setup wizard ready state"""
        def show():
            self._hide_login_form()
            self._hide_browser_button()
            self._update_status(
                "Welcome to snflwr.ai!",
                "Let's set up your family's account (takes about 3 minutes)"
            )

            if self.action_button:
                if ctk:
                    self.action_button.configure(text="Start Setup")
                else:
                    self.action_button.config(text="Start Setup")
                self.action_button.pack()

        self.root.after(0, show)

    def _show_login_ready(self):
        """Show inline login form — same window, same visual style as launcher"""
        def show():
            self._update_status(
                "Welcome Back!",
                "Sign in to start snflwr.ai"
            )

            # Hide the generic action button — login form has its own
            if self.action_button:
                self.action_button.pack_forget()

            self._build_login_form()

        self.root.after(0, show)

    # ── Inline login form ─────────────────────────────────────

    def _build_login_form(self):
        """Build login form embedded in the launcher status area"""
        if self.login_frame is not None:
            return  # already built

        parent = self.status_label.master  # the status section frame

        if ctk:
            self.login_frame = ctk.CTkFrame(parent, fg_color="transparent")
        else:
            self.login_frame = tk.Frame(parent, bg="#f0f0f0")
        self.login_frame.pack(fill=tk.X, padx=60, pady=(0, 10))

        # Username
        if ctk:
            ctk.CTkLabel(self.login_frame, text="Username", font=("Arial", 12),
                         anchor="w").pack(fill=tk.X)
            self.username_entry = ctk.CTkEntry(self.login_frame, height=36,
                                               font=("Arial", 13))
        else:
            tk.Label(self.login_frame, text="Username", font=("Arial", 12),
                     bg="#f0f0f0", anchor="w").pack(fill=tk.X)
            self.username_entry = tk.Entry(self.login_frame, font=("Arial", 13))
        self.username_entry.pack(fill=tk.X, pady=(2, 10))

        # Password
        if ctk:
            ctk.CTkLabel(self.login_frame, text="Password", font=("Arial", 12),
                         anchor="w").pack(fill=tk.X)
            self.password_entry = ctk.CTkEntry(self.login_frame, height=36,
                                               font=("Arial", 13), show="*")
        else:
            tk.Label(self.login_frame, text="Password", font=("Arial", 12),
                     bg="#f0f0f0", anchor="w").pack(fill=tk.X)
            self.password_entry = tk.Entry(self.login_frame, font=("Arial", 13),
                                           show="*")
        self.password_entry.pack(fill=tk.X, pady=(2, 14))

        # Error label (hidden until needed)
        if ctk:
            self.login_error_label = ctk.CTkLabel(
                self.login_frame, text="", font=("Arial", 11),
                text_color="#c62828", wraplength=500
            )
        else:
            self.login_error_label = tk.Label(
                self.login_frame, text="", font=("Arial", 11),
                bg="#f0f0f0", fg="#c62828", wraplength=500
            )
        self.login_error_label.pack(fill=tk.X, pady=(0, 6))

        # Sign-in button
        if ctk:
            self.sign_in_button = ctk.CTkButton(
                self.login_frame, text="Sign In", height=40,
                font=("Arial", 14, "bold"),
                command=self._attempt_login
            )
        else:
            self.sign_in_button = tk.Button(
                self.login_frame, text="Sign In",
                font=("Arial", 14, "bold"), height=2,
                bg="#007AFF", fg="white",
                command=self._attempt_login
            )
        self.sign_in_button.pack(fill=tk.X, pady=(0, 8))

        # Bind Enter key
        self.root.bind("<Return>", lambda _: self._attempt_login())

        # Focus username field
        if self.username_entry:
            self.username_entry.focus_set()

    def _hide_login_form(self):
        """Remove the inline login form if it exists"""
        if self.login_frame is not None:
            self.login_frame.pack_forget()
            self.login_frame.destroy()
            self.login_frame = None
            self.username_entry = None
            self.password_entry = None
            self.login_error_label = None
            self.sign_in_button = None
            # Unbind Enter key from login
            try:
                self.root.unbind("<Return>")
            except Exception:
                pass

    def _attempt_login(self):
        """Validate inputs and authenticate via auth_manager"""
        if self.username_entry is None or self.password_entry is None:
            return  # login form already torn down

        # Defense-in-depth: reject login if no accounts are registered
        if not self._accounts_exist():
            self._show_login_error(
                "No account registered. Please complete setup first."
            )
            return

        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self._show_login_error("Please enter both username and password.")
            return

        # Disable button while authenticating
        if ctk:
            self.sign_in_button.configure(state="disabled", text="Signing in...")
        else:
            self.sign_in_button.config(state="disabled", text="Signing in...")

        self._clear_login_error()

        try:
            success, result = auth_manager.authenticate_parent(username, password)
        except Exception as e:
            logger.error(f"Login error: {e}")
            self._show_login_error(
                "An unexpected error occurred. Please try again."
            )
            self._reset_sign_in_button()
            return

        if success:
            logger.info(f"Admin authenticated: {username}")
            # Enrich session_data with fields the dashboard expects
            result['username'] = username
            self.session_data = result
            self._show_authenticated_ready()
        else:
            error_msg = (
                result if isinstance(result, str)
                else "Invalid username or password"
            )
            self._show_login_error(error_msg)
            self._reset_sign_in_button()
            # Clear password field on failure
            self.password_entry.delete(0, tk.END)
            self.password_entry.focus_set()

    def _show_login_error(self, msg: str):
        """Show error message in inline login form"""
        if self.login_error_label:
            if ctk:
                self.login_error_label.configure(text=msg)
            else:
                self.login_error_label.config(text=msg)

    def _clear_login_error(self):
        self._show_login_error("")

    def _reset_sign_in_button(self):
        if self.sign_in_button:
            if ctk:
                self.sign_in_button.configure(state="normal", text="Sign In")
            else:
                self.sign_in_button.config(state="normal", text="Sign In")

    def _show_authenticated_ready(self):
        """Post-login: tear down login form, show launch and browser buttons"""
        self._hide_login_form()
        self._update_status(
            "Ready to Go!",
            "snflwr.ai is ready to launch"
        )

        if self.action_button:
            if ctk:
                self.action_button.configure(text="Start Snflwr")
            else:
                self.action_button.config(text="Start Snflwr")
            self.action_button.pack()

        # "Open in Browser" button — referenced by headless-mode scripts
        # Guard: destroy any existing button before creating a new one
        self._hide_browser_button()
        button_parent = self.action_button.master
        if ctk:
            self.browser_button = ctk.CTkButton(
                button_parent,
                text="Open in Browser",
                font=("Arial", 13),
                height=36,
                width=200,
                fg_color="#4CAF50",
                command=self._open_in_browser,
            )
        else:
            self.browser_button = tk.Button(
                button_parent,
                text="Open in Browser",
                font=("Arial", 13),
                height=2,
                width=20,
                bg="#4CAF50",
                fg="white",
                command=self._open_in_browser,
            )
        self.browser_button.pack(pady=(8, 0))

        # Start service health monitoring (same pattern as launcher/app.py)
        self._start_service_monitor()

    # ── Service health monitoring ──────────────────────────────────

    @staticmethod
    def _get_services():
        """Return service URLs derived from live config (respects thin-client overrides)."""
        return [
            ("Ollama", f"{system_config.OLLAMA_HOST}/api/tags"),
            ("Snflwr API", f"{system_config.BASE_URL}/health"),
            ("Open WebUI", system_config.OPEN_WEBUI_URL),
        ]

    def _start_service_monitor(self):
        """Begin polling service health in the background."""
        self._service_monitor_alive = True
        self._service_labels = []
        self._services = self._get_services()  # snapshot current config

        # Build a compact status row inside the status section
        parent = self.status_label.master
        if ctk:
            self._service_frame = ctk.CTkFrame(parent, fg_color="transparent")
        else:
            self._service_frame = tk.Frame(parent, bg="#f0f0f0")
        self._service_frame.pack(fill=tk.X, padx=40, pady=(0, 10))

        _STATUS_BG = "#f0f0f0"

        for name, _url in self._services:
            if ctk:
                row = ctk.CTkFrame(self._service_frame, fg_color="transparent")
            else:
                row = tk.Frame(self._service_frame, bg=_STATUS_BG)
            row.pack(fill=tk.X, pady=2)

            dot_canvas = tk.Canvas(row, width=14, height=14, bg=_STATUS_BG, highlightthickness=0)
            dot_id = dot_canvas.create_oval(2, 2, 12, 12, fill="#9ca3af", outline="")
            dot_canvas.pack(side="left", padx=(0, 8))

            if ctk:
                lbl = ctk.CTkLabel(row, text=f"{name}: checking...", font=("Arial", 11))
            else:
                lbl = tk.Label(row, text=f"{name}: checking...", font=("Arial", 11), bg="#f0f0f0", fg="#555")
            lbl.pack(side="left")

            self._service_labels.append((name, dot_canvas, dot_id, lbl))

        self._poll_services()

    def _poll_services(self):
        """Schedule a background health check every 3 seconds."""
        if not getattr(self, '_service_monitor_alive', False) or not self.root:
            return
        # Guard: skip if the previous check is still running
        if not getattr(self, '_check_in_progress', False):
            self._check_in_progress = True
            threading.Thread(target=self._check_services, daemon=True).start()
        self.root.after(3000, self._poll_services)

    def _check_services(self):
        """Check each service URL in parallel (runs in background thread)."""
        from urllib.request import urlopen
        from concurrent.futures import ThreadPoolExecutor

        def _probe(url):
            try:
                resp = urlopen(url, timeout=2)
                return 200 <= resp.getcode() < 400
            except Exception:
                return False

        try:
            with ThreadPoolExecutor(max_workers=len(self._services)) as pool:
                results = list(pool.map(_probe, [url for _, url in self._services]))
        finally:
            self._check_in_progress = False

        if getattr(self, '_service_monitor_alive', False):
            try:
                self.root.after(0, self._update_service_dots, results)
            except Exception:
                pass

    def _update_service_dots(self, results):
        """Update the service status dots on the main thread."""
        for i, ok in enumerate(results):
            if i < len(self._service_labels):
                name, canvas, dot_id, lbl = self._service_labels[i]
                color = "#22c55e" if ok else "#ef4444"
                canvas.itemconfig(dot_id, fill=color)
                status_text = "running" if ok else "stopped"
                if ctk:
                    lbl.configure(text=f"{name}: {status_text}")
                else:
                    lbl.config(text=f"{name}: {status_text}")

    def _hide_browser_button(self):
        """Destroy the 'Open in Browser' button if it exists"""
        if self.browser_button is not None:
            self.browser_button.destroy()
            self.browser_button = None

    def _open_in_browser(self):
        """Probe for a running web UI in a background thread, then open it"""
        # Disable button while probing to signal activity
        if self.browser_button:
            if ctk:
                self.browser_button.configure(state="disabled",
                                              text="Checking...")
            else:
                self.browser_button.config(state="disabled",
                                           text="Checking...")

        def probe():
            import urllib.request
            for url in ("http://localhost:3000",
                        "http://localhost:39150/docs"):
                try:
                    urllib.request.urlopen(url, timeout=2)
                    return url
                except Exception:
                    continue
            return None

        def on_result(found_url):
            import webbrowser
            # Re-enable button
            if self.browser_button:
                if ctk:
                    self.browser_button.configure(state="normal",
                                                  text="Open in Browser")
                else:
                    self.browser_button.config(state="normal",
                                               text="Open in Browser")
            if found_url:
                webbrowser.open(found_url)
            else:
                messagebox.showinfo(
                    "Open in Browser",
                    "Could not detect a running web interface.\n\n"
                    "Make sure snflwr.ai services are started\n"
                    "(run START_SNFLWR.bat or start_snflwr.sh "
                    "first),\nthen try again."
                )

        def background():
            result = probe()
            self.root.after(0, lambda: on_result(result))

        threading.Thread(target=background, daemon=True).start()

    def _on_action_button_click(self):
        """Handle action button click"""
        try:
            button_text = self.action_button.cget("text")

            if button_text == "Retry Detection":
                # Restart detection
                self.action_button.pack_forget()
                self._hide_browser_button()
                if self.error_frame:
                    self.error_frame.pack_forget()
                self._start_detection_process()

            elif button_text == "Start Setup":
                # Launch setup wizard
                self._launch_setup_wizard()

            elif button_text == "Start Snflwr":
                # Admin already authenticated — launch dashboard
                self._launch_parent_dashboard(self.session_data)

            elif button_text == "Exit":
                self.root.quit()

        except Exception as e:
            logger.error(f"Action button click failed: {e}")
            messagebox.showerror(
                "Error",
                f"An error occurred: {str(e)}"
            )
    
    def _launch_setup_wizard(self):
        """Launch setup wizard"""
        try:
            logger.info("Launching setup wizard")
            
            # Hide launcher window
            self.root.withdraw()
            
            # Import and launch setup wizard
            from ui.setup_wizard import SetupWizard
            
            wizard = SetupWizard(
                self.root,
                self.cdrom_path,
                self.data_path
            )
            
            # When wizard completes, either show login or exit
            def on_wizard_complete(success):
                if success:
                    # Show login window
                    self._launch_login()
                else:
                    # User cancelled - show launcher again
                    self.root.deiconify()
            
            wizard.on_complete = on_wizard_complete
            wizard.show()
            
        except ImportError as e:
            logger.error(f"Setup wizard not available: {e}")
            messagebox.showerror(
                "Setup Error",
                "Setup wizard is not available yet.\n\n"
                "This is a development version. Please check back later."
            )
            self.root.deiconify()
        except Exception as e:
            logger.error(f"Failed to launch setup wizard: {e}")
            messagebox.showerror(
                "Setup Error",
                f"Failed to launch setup wizard:\n\n{str(e)}"
            )
            self.root.deiconify()
    
    def _launch_login(self):
        """Show inline login form after wizard completes"""
        try:
            # Make sure the launcher is visible
            self.root.deiconify()

            # Guard: if no accounts exist, force setup instead of login
            if not self._accounts_exist():
                logger.info("No accounts registered — redirecting to setup wizard")
                self._show_setup_ready()
                return

            logger.info("Showing login form")
            self.has_existing_account = True
            self._show_login_ready()

        except Exception as e:
            logger.error(f"Failed to show login form: {e}")
            messagebox.showerror(
                "Login Error",
                f"Failed to show login form:\n\n{str(e)}"
            )
            self.root.deiconify()

    def _accounts_exist(self) -> bool:
        """Check if any admin/parent accounts are registered"""
        try:
            from storage.database import db_manager
            rows = db_manager.execute_query(
                "SELECT COUNT(*) as count FROM accounts"
            )
            return bool(rows and rows[0]['count'] > 0)
        except Exception as e:
            logger.warning(f"Could not check account registry: {e}")
            return False

    def _launch_parent_dashboard(self, session_data: dict):
        """Launch parent dashboard"""
        try:
            logger.info("Launching parent dashboard")
            
            # Hide launcher
            self.root.withdraw()
            
            # Import and launch dashboard
            from ui.parent_dashboard import ParentDashboard
            
            dashboard = ParentDashboard(
                self.root,
                session_data
            )
            
            # When dashboard closes, tear down everything
            def on_dashboard_close():
                self._shutdown()
            
            dashboard.on_close = on_dashboard_close
            dashboard.show()
            
        except ImportError as e:
            logger.error(f"Parent dashboard not available: {e}")
            messagebox.showerror(
                "Dashboard Error",
                "Parent dashboard is not available yet.\n\n"
                "This is a development version. Please check back later."
            )
            self.root.deiconify()
        except Exception as e:
            logger.error(f"Failed to launch parent dashboard: {e}")
            messagebox.showerror(
                "Dashboard Error",
                f"Failed to launch parent dashboard:\n\n{str(e)}"
            )
            self.root.deiconify()
    
    def _show_critical_error(self, title: str, message: str):
        """Show critical error message box"""
        try:
            # Use tkinter messagebox directly (works even if main window failed)
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(title, message)
            root.destroy()
        except Exception:
            # Last resort - print to console
            print(f"\n{'='*60}")
            print(f"CRITICAL ERROR: {title}")
            print(f"{'='*60}")
            print(message)
            print(f"{'='*60}\n")
    
    def _shutdown(self):
        """Release all resources (DB, loggers) and exit mainloop.

        Called by WM_DELETE_WINDOW, on_dashboard_close, and KeyboardInterrupt.
        Safe to call more than once.
        """
        # Stop service health polling
        self._service_monitor_alive = False

        try:
            logger.info("Launcher shutting down")
        except Exception:
            pass

        # Close database connections (releases SQLite/Postgres file locks)
        try:
            from storage.database import db_manager
            db_manager.close()
        except Exception:
            pass

        # Flush and close log file handlers
        try:
            from utils.logger import logger_manager
            logger_manager.cleanup()
        except Exception:
            pass

        # Exit the Tk event loop and destroy the window
        try:
            if self.root:
                self.root.quit()
                self.root.destroy()
        except Exception:
            pass

    def run(self):
        """Run the launcher application"""
        try:
            if not self.root:
                self.create_window()

            logger.info("Starting launcher main loop")
            self.root.mainloop()

        except KeyboardInterrupt:
            logger.info("Launcher interrupted by user")
        except Exception as e:
            logger.error(f"Launcher main loop failed: {e}")
            self._show_critical_error(
                "Application Error",
                f"The application encountered a critical error:\n\n{str(e)}\n\n"
                "Please contact support."
            )
        finally:
            self._shutdown()


def _atexit_cleanup():
    """Safety-net: release file handles even if _shutdown was never called."""
    try:
        from storage.database import db_manager
        db_manager.close()
    except Exception:
        pass
    try:
        from utils.logger import logger_manager
        logger_manager.cleanup()
    except Exception:
        pass


def main():
    """Main entry point for snflwr.ai"""
    import atexit
    atexit.register(_atexit_cleanup)

    try:
        # Log system startup
        log_system_startup()
        logger.info(f"Starting snflwr.ai v{system_config.VERSION}")
        logger.info(f"Platform: {system_config.PLATFORM}")
        logger.info(f"Python: {sys.version}")
        
        # Create and run launcher
        launcher = LauncherWindow()
        launcher.run()
        
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        
        # Show error to user
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Startup Error",
            f"snflwr.ai failed to start:\n\n{str(e)}\n\n"
            "Please try restarting the application. If the problem persists, "
            "contact support at support@snflwr.ai"
        )
        root.destroy()

        sys.exit(1)

    # Ensure the process terminates even if non-daemon threads (from tkinter,
    # customtkinter, or urllib) are still alive.  Without this, the interpreter
    # waits for all threads before exiting, which can hang indefinitely.
    sys.exit(0)


if __name__ == "__main__":
    main()


# Export public interface
__all__ = ['LauncherWindow', 'main']
