# ui/parent_dashboard.py
"""
Parent Dashboard - Comprehensive Monitoring and Management Interface
Professional interface for parents to manage children, monitor safety, and view analytics
"""

import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import Optional, Dict, List, Callable
from datetime import datetime, timedelta, timezone

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

from config import system_config
from core.authentication import auth_manager
from core.profile_manager import ProfileManager
from safety.incident_logger import incident_logger
from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


class ParentDashboard:
    """
    Parent dashboard for monitoring and managing family learning
    Professional, clean design with comprehensive features
    """
    
    def __init__(
        self,
        parent_window: tk.Tk,
        session_data: Dict
    ):
        """
        Initialize parent dashboard

        Args:
            parent_window: Parent Tk window
            session_data: Authenticated session data
        """
        self.parent_window = parent_window
        self.session_data = session_data
        
        self.window = None
        self.profile_manager = ProfileManager(db_manager)
        
        # Current state
        self.profiles: List = []
        self.selected_profile = None
        self.current_tab = "overview"
        
        # Callback
        self.on_close: Optional[Callable[[], None]] = None
        
        # UI components
        self.tab_buttons = {}
        self.content_frame = None
        
        logger.info("Parent dashboard initialized")
    
    def show(self):
        """Show dashboard window"""
        try:
            # Create main window
            if ctk:
                self.window = ctk.CTkToplevel(self.parent_window)
            else:
                self.window = tk.Toplevel(self.parent_window)
            
            self.window.title(f"snflwr.ai - Parent Dashboard")
            self.window.geometry("1200x800")
            
            # Center window
            self._center_window()
            
            # Handle close
            self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)
            
            # Create UI
            self._create_ui()
            
            # Load initial data
            self._load_profiles()
            
            # Show overview tab
            self._show_tab("overview")
            
            logger.info("Parent dashboard shown")
            
        except Exception as e:
            logger.error(f"Failed to show dashboard: {e}")
            messagebox.showerror(
                "Dashboard Error",
                f"Failed to open dashboard:\n\n{str(e)}"
            )
    
    def _center_window(self):
        """Center window on screen"""
        self.window.update_idletasks()
        
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        
        x = (screen_width - 1200) // 2
        y = (screen_height - 800) // 2
        
        self.window.geometry(f"1200x800+{x}+{y}")
    
    def _create_ui(self):
        """Create dashboard UI"""
        # Header
        header_frame = self._create_header()
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 0))
        
        # Navigation tabs
        nav_frame = self._create_navigation()
        nav_frame.pack(fill=tk.X, padx=20, pady=20)
        
        # Content area
        if ctk:
            self.content_frame = ctk.CTkFrame(self.window)
        else:
            self.content_frame = tk.Frame(self.window, bg="white")
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
    
    def _create_header(self) -> tk.Frame:
        """Create header with user info"""
        if ctk:
            frame = ctk.CTkFrame(self.window, fg_color="transparent")
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Welcome message
        welcome_text = f"Welcome, {self.session_data.get('username', 'Admin')}!"
        if ctk:
            welcome = ctk.CTkLabel(
                frame,
                text=welcome_text,
                font=("Arial", 20, "bold")
            )
        else:
            welcome = tk.Label(
                frame,
                text=welcome_text,
                font=("Arial", 20, "bold"),
                bg="white"
            )
        welcome.pack(side=tk.LEFT)
        
        # Logout button
        if ctk:
            logout_btn = ctk.CTkButton(
                frame,
                text="Logout",
                width=100,
                command=self._logout
            )
        else:
            logout_btn = tk.Button(
                frame,
                text="Logout",
                width=10,
                command=self._logout
            )
        logout_btn.pack(side=tk.RIGHT)
        
        return frame
    
    def _create_navigation(self) -> tk.Frame:
        """Create tab navigation"""
        if ctk:
            frame = ctk.CTkFrame(self.window)
        else:
            frame = tk.Frame(self.window, bg="#f0f0f0", relief=tk.RIDGE, bd=2)
        
        tabs = [
            ("overview", "[STATS] Overview"),
            ("profiles", "[USERS] Profiles"),
            ("safety", "[SAFE] Safety"),
            ("analytics", "[CHART] Analytics"),
            ("settings", "[CONFIG] Settings")
        ]
        
        for tab_id, tab_name in tabs:
            if ctk:
                btn = ctk.CTkButton(
                    frame,
                    text=tab_name,
                    width=120,
                    command=lambda t=tab_id: self._show_tab(t)
                )
            else:
                btn = tk.Button(
                    frame,
                    text=tab_name,
                    width=15,
                    command=lambda t=tab_id: self._show_tab(t)
                )
            btn.pack(side=tk.LEFT, padx=5, pady=5)
            self.tab_buttons[tab_id] = btn
        
        return frame
    
    def _show_tab(self, tab_id: str):
        """Show specific tab"""
        self.current_tab = tab_id
        
        # Update button states
        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                if ctk:
                    btn.configure(fg_color="#007AFF")
                else:
                    btn.config(bg="#007AFF", fg="white")
            else:
                if ctk:
                    btn.configure(fg_color="#3B8ED0")
                else:
                    btn.config(bg="#f0f0f0", fg="black")
        
        # Clear content
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        
        # Show appropriate content
        if tab_id == "overview":
            self._show_overview_tab()
        elif tab_id == "profiles":
            self._show_profiles_tab()
        elif tab_id == "safety":
            self._show_safety_tab()
        elif tab_id == "analytics":
            self._show_analytics_tab()
        elif tab_id == "settings":
            self._show_settings_tab()
    
    def _show_overview_tab(self):
        """Show overview tab with summary"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Family Overview",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Family Overview",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 30))
        
        # Statistics frame
        if ctk:
            stats_frame = ctk.CTkFrame(self.content_frame)
        else:
            stats_frame = tk.Frame(self.content_frame, bg="#f8f8f8", relief=tk.RIDGE, bd=2)
        stats_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Get statistics
        total_profiles = len(self.profiles)
        active_today = self._get_active_profiles_today()
        total_sessions = self._get_total_sessions_today()
        pending_incidents = self._get_pending_incidents()
        
        # Create stat cards
        stats = [
            ("[USERS] Total Children", str(total_profiles)),
            ("[OK] Active Today", str(active_today)),
            ("[DOCS] Sessions Today", str(total_sessions)),
            ("[WARN] Safety Alerts", str(pending_incidents))
        ]
        
        for i, (label, value) in enumerate(stats):
            stat_card = self._create_stat_card(stats_frame, label, value)
            stat_card.grid(row=0, column=i, padx=10, pady=20, sticky=tk.NSEW)
        
        stats_frame.grid_columnconfigure(0, weight=1)
        stats_frame.grid_columnconfigure(1, weight=1)
        stats_frame.grid_columnconfigure(2, weight=1)
        stats_frame.grid_columnconfigure(3, weight=1)
        
        # Recent activity
        if ctk:
            activity_title = ctk.CTkLabel(
                self.content_frame,
                text="Recent Activity",
                font=("Arial", 18, "bold")
            )
        else:
            activity_title = tk.Label(
                self.content_frame,
                text="Recent Activity",
                font=("Arial", 18, "bold"),
                bg="white"
            )
        activity_title.pack(pady=(30, 10), anchor=tk.W, padx=20)
        
        # Activity list
        if ctk:
            activity_frame = ctk.CTkFrame(self.content_frame)
        else:
            activity_frame = tk.Frame(self.content_frame, bg="white")
        activity_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Get recent sessions
        recent_sessions = self._get_recent_sessions(5)
        
        if recent_sessions:
            for session in recent_sessions:
                self._create_activity_item(activity_frame, session)

            # "View All Sessions" button
            if ctk:
                view_all_btn = ctk.CTkButton(
                    self.content_frame,
                    text="View All Sessions",
                    width=160,
                    command=lambda: self._show_all_sessions()
                )
            else:
                view_all_btn = tk.Button(
                    self.content_frame,
                    text="View All Sessions",
                    width=18,
                    command=lambda: self._show_all_sessions()
                )
            view_all_btn.pack(pady=(5, 15))
        else:
            if ctk:
                no_activity = ctk.CTkLabel(
                    activity_frame,
                    text="No recent activity",
                    font=("Arial", 12),
                    text_color="gray"
                )
            else:
                no_activity = tk.Label(
                    activity_frame,
                    text="No recent activity",
                    font=("Arial", 12),
                    bg="white",
                    fg="gray"
                )
            no_activity.pack(pady=20)
    
    def _create_stat_card(self, parent, label: str, value: str) -> tk.Frame:
        """Create statistics card"""
        if ctk:
            card = ctk.CTkFrame(parent)
        else:
            card = tk.Frame(parent, bg="white", relief=tk.RIDGE, bd=1)
        
        # Value
        if ctk:
            value_label = ctk.CTkLabel(
                card,
                text=value,
                font=("Arial", 32, "bold")
            )
        else:
            value_label = tk.Label(
                card,
                text=value,
                font=("Arial", 32, "bold"),
                bg="white"
            )
        value_label.pack(pady=(10, 5))
        
        # Label
        if ctk:
            label_widget = ctk.CTkLabel(
                card,
                text=label,
                font=("Arial", 12)
            )
        else:
            label_widget = tk.Label(
                card,
                text=label,
                font=("Arial", 12),
                bg="white",
                fg="gray"
            )
        label_widget.pack(pady=(0, 10))
        
        return card
    
    def _create_activity_item(self, parent, session: Dict):
        """Create activity list item"""
        if ctk:
            item = ctk.CTkFrame(parent)
        else:
            item = tk.Frame(parent, bg="#f8f8f8", relief=tk.RIDGE, bd=1)
        item.pack(fill=tk.X, pady=5)
        
        # Profile name
        profile_name = session.get('profile_name', 'Unknown')
        time_str = session.get('started_at', '')
        questions = session.get('questions_asked', 0)
        
        text = f"{profile_name} - {questions} questions - {time_str}"
        
        if ctk:
            label = ctk.CTkLabel(
                item,
                text=text,
                font=("Arial", 12)
            )
        else:
            label = tk.Label(
                item,
                text=text,
                font=("Arial", 12),
                bg="#f8f8f8"
            )
        label.pack(anchor=tk.W, padx=10, pady=8)

    def _show_all_sessions(self, page: int = 0):
        """Show full session history with pagination"""
        page_size = 20

        # Clear content
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Session History",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Session History",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 10))

        # Back to overview link
        if ctk:
            back_btn = ctk.CTkButton(
                self.content_frame,
                text="← Back to Overview",
                width=160,
                fg_color="gray",
                command=lambda: self._show_tab("overview")
            )
        else:
            back_btn = tk.Button(
                self.content_frame,
                text="← Back to Overview",
                width=18,
                command=lambda: self._show_tab("overview")
            )
        back_btn.pack(anchor=tk.W, padx=20, pady=(0, 10))

        # Fetch one extra to know if there's a next page
        offset = page * page_size
        sessions = self._get_paginated_sessions(page_size + 1, offset)

        has_next = len(sessions) > page_size
        display_sessions = sessions[:page_size]

        # Session list
        if ctk:
            list_frame = ctk.CTkScrollableFrame(self.content_frame, height=450)
        else:
            scroll_container = tk.Frame(self.content_frame, bg="white")
            scroll_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
            canvas = tk.Canvas(scroll_container, bg="white")
            scrollbar = tk.Scrollbar(
                scroll_container, orient=tk.VERTICAL, command=canvas.yview
            )
            list_frame = tk.Frame(canvas, bg="white")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.create_window((0, 0), window=list_frame, anchor=tk.NW)
            list_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )

        if ctk:
            list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        if display_sessions:
            for session in display_sessions:
                self._create_activity_item(list_frame, session)
        else:
            if ctk:
                empty = ctk.CTkLabel(
                    list_frame, text="No sessions found.",
                    font=("Arial", 12), text_color="gray"
                )
            else:
                empty = tk.Label(
                    list_frame, text="No sessions found.",
                    font=("Arial", 12), bg="white", fg="gray"
                )
            empty.pack(pady=30)

        # Pagination controls
        if ctk:
            nav_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        else:
            nav_frame = tk.Frame(self.content_frame, bg="white")
        nav_frame.pack(pady=10)

        if page > 0:
            if ctk:
                prev_btn = ctk.CTkButton(
                    nav_frame, text="← Previous", width=100,
                    command=lambda: self._show_all_sessions(page - 1)
                )
            else:
                prev_btn = tk.Button(
                    nav_frame, text="← Previous", width=12,
                    command=lambda: self._show_all_sessions(page - 1)
                )
            prev_btn.pack(side=tk.LEFT, padx=10)

        # Page indicator
        if ctk:
            page_label = ctk.CTkLabel(
                nav_frame, text=f"Page {page + 1}",
                font=("Arial", 12)
            )
        else:
            page_label = tk.Label(
                nav_frame, text=f"Page {page + 1}",
                font=("Arial", 12), bg="white"
            )
        page_label.pack(side=tk.LEFT, padx=10)

        if has_next:
            if ctk:
                next_btn = ctk.CTkButton(
                    nav_frame, text="Next →", width=100,
                    command=lambda: self._show_all_sessions(page + 1)
                )
            else:
                next_btn = tk.Button(
                    nav_frame, text="Next →", width=12,
                    command=lambda: self._show_all_sessions(page + 1)
                )
            next_btn.pack(side=tk.LEFT, padx=10)

    def _get_paginated_sessions(self, limit: int, offset: int) -> List[Dict]:
        """Get sessions with offset for pagination"""
        try:
            profile_ids = self._get_profile_ids()
            if not profile_ids:
                return []
            placeholders = ",".join("?" for _ in profile_ids)
            results = db_manager.execute_query(
                f"""
                SELECT s.session_id, s.started_at, s.questions_asked,
                       cp.name as profile_name
                FROM sessions s
                LEFT JOIN child_profiles cp ON s.profile_id = cp.profile_id
                WHERE s.profile_id IN ({placeholders})
                ORDER BY s.started_at DESC
                LIMIT ? OFFSET ?
                """,
                (*profile_ids, limit, offset)
            )
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to get paginated sessions: {e}")
            return []

    def _show_profiles_tab(self):
        """Show profiles management tab"""
        # Title with add button
        if ctk:
            header_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        else:
            header_frame = tk.Frame(self.content_frame, bg="white")
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        if ctk:
            title = ctk.CTkLabel(
                header_frame,
                text="Child Profiles",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                header_frame,
                text="Child Profiles",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(side=tk.LEFT)
        
        # Add profile button
        if ctk:
            add_btn = ctk.CTkButton(
                header_frame,
                text="+ Add Child",
                width=120,
                command=self._add_profile
            )
        else:
            add_btn = tk.Button(
                header_frame,
                text="+ Add Child",
                width=12,
                command=self._add_profile
            )
        add_btn.pack(side=tk.RIGHT)
        
        # Profile cards
        if ctk:
            profiles_frame = ctk.CTkScrollableFrame(self.content_frame)
        else:
            # Create scrollable frame manually
            canvas = tk.Canvas(self.content_frame, bg="white")
            scrollbar = tk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=canvas.yview)
            profiles_frame = tk.Frame(canvas, bg="white")
            
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.create_window((0, 0), window=profiles_frame, anchor=tk.NW)
            
            profiles_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        if ctk:
            profiles_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Display profiles
        if self.profiles:
            for profile in self.profiles:
                self._create_profile_card(profiles_frame, profile)
        else:
            if ctk:
                no_profiles = ctk.CTkLabel(
                    profiles_frame,
                    text="No child profiles yet. Click '+ Add Child' to create one.",
                    font=("Arial", 14),
                    text_color="gray"
                )
            else:
                no_profiles = tk.Label(
                    profiles_frame,
                    text="No child profiles yet. Click '+ Add Child' to create one.",
                    font=("Arial", 14),
                    bg="white",
                    fg="gray"
                )
            no_profiles.pack(pady=50)
    
    def _create_profile_card(self, parent, profile):
        """Create profile card with actions"""
        if ctk:
            card = ctk.CTkFrame(parent)
        else:
            card = tk.Frame(parent, bg="#f8f8f8", relief=tk.RIDGE, bd=2)
        card.pack(fill=tk.X, pady=10, padx=10)
        
        # Left side - profile info
        if ctk:
            info_frame = ctk.CTkFrame(card, fg_color="transparent")
        else:
            info_frame = tk.Frame(card, bg="#f8f8f8")
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=20, pady=15)
        
        # Name and age
        name_text = f"{profile.name}, Age {profile.age}"
        if ctk:
            name_label = ctk.CTkLabel(
                info_frame,
                text=name_text,
                font=("Arial", 16, "bold")
            )
        else:
            name_label = tk.Label(
                info_frame,
                text=name_text,
                font=("Arial", 16, "bold"),
                bg="#f8f8f8"
            )
        name_label.pack(anchor=tk.W)
        
        # Grade
        grade_text = f"Grade: {profile.grade}"
        if ctk:
            grade_label = ctk.CTkLabel(
                info_frame,
                text=grade_text,
                font=("Arial", 12)
            )
        else:
            grade_label = tk.Label(
                info_frame,
                text=grade_text,
                font=("Arial", 12),
                bg="#f8f8f8",
                fg="gray"
            )
        grade_label.pack(anchor=tk.W)
        
        # Sessions
        sessions_text = f"Total Sessions: {profile.total_sessions}"
        if ctk:
            sessions_label = ctk.CTkLabel(
                info_frame,
                text=sessions_text,
                font=("Arial", 12)
            )
        else:
            sessions_label = tk.Label(
                info_frame,
                text=sessions_text,
                font=("Arial", 12),
                bg="#f8f8f8",
                fg="gray"
            )
        sessions_label.pack(anchor=tk.W)
        
        # Right side - actions
        if ctk:
            actions_frame = ctk.CTkFrame(card, fg_color="transparent")
        else:
            actions_frame = tk.Frame(card, bg="#f8f8f8")
        actions_frame.pack(side=tk.RIGHT, padx=10, pady=10)
        
        # Edit button
        if ctk:
            edit_btn = ctk.CTkButton(
                actions_frame,
                text="Edit",
                width=80,
                command=lambda p=profile: self._edit_profile(p)
            )
        else:
            edit_btn = tk.Button(
                actions_frame,
                text="Edit",
                width=8,
                command=lambda p=profile: self._edit_profile(p)
            )
        edit_btn.pack(side=tk.LEFT, padx=5)
        
        # Delete button
        if ctk:
            delete_btn = ctk.CTkButton(
                actions_frame,
                text="Delete",
                width=80,
                fg_color="red",
                command=lambda p=profile: self._delete_profile(p)
            )
        else:
            delete_btn = tk.Button(
                actions_frame,
                text="Delete",
                width=8,
                bg="red",
                fg="white",
                command=lambda p=profile: self._delete_profile(p)
            )
        delete_btn.pack(side=tk.LEFT, padx=5)
    
    def _show_safety_tab(self):
        """Show safety monitoring tab"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Safety Monitoring",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Safety Monitoring",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 30))
        
        # Get safety statistics
        stats = incident_logger.get_incident_statistics(days=30)
        
        # Summary
        if ctk:
            summary_frame = ctk.CTkFrame(self.content_frame)
        else:
            summary_frame = tk.Frame(self.content_frame, bg="#f8f8f8", relief=tk.RIDGE, bd=2)
        summary_frame.pack(fill=tk.X, padx=20, pady=10)
        
        summary_text = (
            f"Last 30 Days:\n"
            f"Total Incidents: {stats.get('total_incidents', 0)}\n"
            f"Critical: {stats.get('by_severity', {}).get('critical', {}).get('count', 0)}\n"
            f"Major: {stats.get('by_severity', {}).get('major', {}).get('count', 0)}\n"
            f"Minor: {stats.get('by_severity', {}).get('minor', {}).get('count', 0)}\n"
            f"Unresolved: {stats.get('unresolved', 0)}"
        )
        
        if ctk:
            summary_label = ctk.CTkLabel(
                summary_frame,
                text=summary_text,
                font=("Arial", 12),
                justify=tk.LEFT
            )
        else:
            summary_label = tk.Label(
                summary_frame,
                text=summary_text,
                font=("Arial", 12),
                bg="#f8f8f8",
                justify=tk.LEFT
            )
        summary_label.pack(padx=20, pady=15, anchor=tk.W)
        
        # Recent incidents
        if ctk:
            incidents_title = ctk.CTkLabel(
                self.content_frame,
                text="Recent Incidents",
                font=("Arial", 18, "bold")
            )
        else:
            incidents_title = tk.Label(
                self.content_frame,
                text="Recent Incidents",
                font=("Arial", 18, "bold"),
                bg="white"
            )
        incidents_title.pack(pady=(20, 10), anchor=tk.W, padx=20)
        
        # Get recent incidents
        if self.profiles:
            # Show incidents from all profiles
            all_incidents = []
            for profile in self.profiles:
                incidents = incident_logger.get_profile_incidents(
                    profile.profile_id,
                    days=30
                )
                all_incidents.extend(incidents)
            
            # Sort by timestamp
            all_incidents.sort(key=lambda x: x.timestamp, reverse=True)
            
            if all_incidents:
                # Create scrollable list
                if ctk:
                    incidents_frame = ctk.CTkScrollableFrame(self.content_frame, height=400)
                else:
                    incidents_frame = scrolledtext.ScrolledText(
                        self.content_frame,
                        height=20,
                        width=80,
                        wrap=tk.WORD
                    )
                incidents_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
                
                # Display incidents
                for incident in all_incidents[:20]:  # Show last 20
                    self._create_incident_item(incidents_frame, incident)
            else:
                if ctk:
                    no_incidents = ctk.CTkLabel(
                        self.content_frame,
                        text="No safety incidents in the last 30 days. Great job!",
                        font=("Arial", 14),
                        text_color="green"
                    )
                else:
                    no_incidents = tk.Label(
                        self.content_frame,
                        text="No safety incidents in the last 30 days. Great job!",
                        font=("Arial", 14),
                        bg="white",
                        fg="green"
                    )
                no_incidents.pack(pady=50)
        else:
            if ctk:
                no_profiles = ctk.CTkLabel(
                    self.content_frame,
                    text="No child profiles yet.",
                    font=("Arial", 14),
                    text_color="gray"
                )
            else:
                no_profiles = tk.Label(
                    self.content_frame,
                    text="No child profiles yet.",
                    font=("Arial", 14),
                    bg="white",
                    fg="gray"
                )
            no_profiles.pack(pady=50)
    
    def _create_incident_item(self, parent, incident):
        """Create incident display item"""
        if ctk:
            item = ctk.CTkFrame(parent)
            item.pack(fill=tk.X, pady=5, padx=5)
        else:
            # For scrolledtext, just insert text
            severity_color = {
                'critical': '',
                'major': '',
                'minor': ''
            }.get(incident.severity, '')
            
            text = (
                f"{severity_color} {incident.severity.upper()} - "
                f"{incident.incident_type} - "
                f"{incident.timestamp.strftime('%Y-%m-%d %H:%M')}\n"
                f"   {incident.content_snippet[:100]}...\n\n"
            )
            parent.insert(tk.END, text)
            return
        
        # For CTkFrame
        severity_color = {
            'critical': '#ff0000',
            'major': '#ff8800',
            'minor': '#ffcc00'
        }.get(incident.severity, '#cccccc')
        
        # Severity indicator
        indicator = ctk.CTkLabel(
            item,
            text="●",
            font=("Arial", 20),
            text_color=severity_color
        )
        indicator.pack(side=tk.LEFT, padx=5)
        
        # Info
        info_frame = ctk.CTkFrame(item, fg_color="transparent")
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Header
        header_text = f"{incident.severity.upper()} - {incident.incident_type}"
        header = ctk.CTkLabel(
            info_frame,
            text=header_text,
            font=("Arial", 12, "bold")
        )
        header.pack(anchor=tk.W)
        
        # Content snippet
        snippet = ctk.CTkLabel(
            info_frame,
            text=incident.content_snippet[:100] + "...",
            font=("Arial", 10),
            wraplength=600
        )
        snippet.pack(anchor=tk.W)
        
        # Timestamp
        time_text = incident.timestamp.strftime("%Y-%m-%d %H:%M")
        time_label = ctk.CTkLabel(
            info_frame,
            text=time_text,
            font=("Arial", 9),
            text_color="gray"
        )
        time_label.pack(anchor=tk.W)
    
    def _show_analytics_tab(self):
        """Show learning analytics tab"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Learning Analytics",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Learning Analytics",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 30))
        
        # Coming soon message
        message = (
            "Detailed learning analytics coming soon!\n\n"
            "This section will show:\n"
            "- Time spent by subject\n"
            "- Questions asked per topic\n"
            "- Learning progress over time\n"
            "- Engagement metrics\n"
            "- Recommended focus areas"
        )
        
        if ctk:
            msg_label = ctk.CTkLabel(
                self.content_frame,
                text=message,
                font=("Arial", 14),
                justify=tk.LEFT
            )
        else:
            msg_label = tk.Label(
                self.content_frame,
                text=message,
                font=("Arial", 14),
                bg="white",
                justify=tk.LEFT
            )
        msg_label.pack(pady=50)
    
    def _show_settings_tab(self):
        """Show settings tab"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Settings",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Settings",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 30))
        
        # Account info
        if ctk:
            account_frame = ctk.CTkFrame(self.content_frame)
        else:
            account_frame = tk.Frame(self.content_frame, bg="#f8f8f8", relief=tk.RIDGE, bd=2)
        account_frame.pack(fill=tk.X, padx=20, pady=10)
        
        account_info = (
            f"Account: {self.session_data.get('username', 'Unknown')}\n"
            f"Parent ID: {self.session_data.get('parent_id', 'N/A')}"
        )
        
        if ctk:
            account_label = ctk.CTkLabel(
                account_frame,
                text=account_info,
                font=("Arial", 12),
                justify=tk.LEFT
            )
        else:
            account_label = tk.Label(
                account_frame,
                text=account_info,
                font=("Arial", 12),
                bg="#f8f8f8",
                justify=tk.LEFT
            )
        account_label.pack(padx=20, pady=15, anchor=tk.W)
        
        # Actions
        if ctk:
            actions_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        else:
            actions_frame = tk.Frame(self.content_frame, bg="white")
        actions_frame.pack(pady=30)
        
        # Change password button
        if ctk:
            password_btn = ctk.CTkButton(
                actions_frame,
                text="Change Password",
                width=150,
                command=self._change_password
            )
        else:
            password_btn = tk.Button(
                actions_frame,
                text="Change Password",
                width=15,
                command=self._change_password
            )
        password_btn.pack(pady=5)
    
    def _load_profiles(self):
        """Load child profiles"""
        try:
            parent_id = self.session_data.get('parent_id', '')
            self.profiles = self.profile_manager.get_profiles_by_parent(parent_id)
            logger.info(f"Loaded {len(self.profiles)} profiles")
        except Exception as e:
            logger.error(f"Failed to load profiles: {e}")
            self.profiles = []
    
    def _add_profile(self):
        """Add new child profile"""
        self._show_profile_dialog()

    def _edit_profile(self, profile):
        """Edit existing child profile"""
        self._show_profile_dialog(profile=profile)

    def _show_profile_dialog(self, profile=None):
        """Show add/edit profile dialog window"""
        editing = profile is not None
        dialog_title = f"Edit {profile.name}" if editing else "Add Child Profile"

        if ctk:
            dialog = ctk.CTkToplevel(self.window)
        else:
            dialog = tk.Toplevel(self.window)

        dialog.title(dialog_title)
        dialog.geometry("450x520")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        # Center on parent window
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() - 450) // 2
        y = self.window.winfo_y() + (self.window.winfo_height() - 520) // 2
        dialog.geometry(f"450x520+{x}+{y}")

        # Title
        if ctk:
            ctk.CTkLabel(dialog, text=dialog_title, font=("Arial", 18, "bold")).pack(pady=(20, 15))
        else:
            tk.Label(dialog, text=dialog_title, font=("Arial", 18, "bold")).pack(pady=(20, 15))

        # Form frame
        if ctk:
            form = ctk.CTkFrame(dialog)
        else:
            form = tk.Frame(dialog)
        form.pack(fill=tk.X, padx=30, pady=10)

        # Name
        if ctk:
            ctk.CTkLabel(form, text="Name:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            name_entry = ctk.CTkEntry(form, width=380)
        else:
            tk.Label(form, text="Name:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            name_entry = tk.Entry(form, width=45)
        name_entry.pack(fill=tk.X)
        if editing:
            name_entry.insert(0, profile.name)

        # Age
        if ctk:
            ctk.CTkLabel(form, text="Age (5-18):", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            age_entry = ctk.CTkEntry(form, width=380)
        else:
            tk.Label(form, text="Age (5-18):", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            age_entry = tk.Entry(form, width=45)
        age_entry.pack(fill=tk.X)
        if editing:
            age_entry.insert(0, str(profile.age))

        # Grade
        grades = ['K', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12']
        grade_var = tk.StringVar(value=profile.grade if editing else 'K')
        if ctk:
            ctk.CTkLabel(form, text="Grade:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            grade_menu = ctk.CTkOptionMenu(form, variable=grade_var, values=grades, width=380)
        else:
            tk.Label(form, text="Grade:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            grade_menu = tk.OptionMenu(form, grade_var, *grades)
        grade_menu.pack(fill=tk.X)

        # Learning Level
        levels = ['adaptive', 'beginner', 'intermediate', 'advanced']
        level_var = tk.StringVar(value=profile.learning_level if editing else 'adaptive')
        if ctk:
            ctk.CTkLabel(form, text="Learning Level:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            level_menu = ctk.CTkOptionMenu(form, variable=level_var, values=levels, width=380)
        else:
            tk.Label(form, text="Learning Level:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            level_menu = tk.OptionMenu(form, level_var, *levels)
        level_menu.pack(fill=tk.X)

        # Daily Time Limit
        if ctk:
            ctk.CTkLabel(form, text="Daily Time Limit (minutes):", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            time_entry = ctk.CTkEntry(form, width=380)
        else:
            tk.Label(form, text="Daily Time Limit (minutes):", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            time_entry = tk.Entry(form, width=45)
        time_entry.pack(fill=tk.X)
        time_entry.insert(0, str(profile.daily_time_limit_minutes if editing else 120))

        # Error display
        if ctk:
            error_label = ctk.CTkLabel(form, text="", text_color="red", font=("Arial", 11))
        else:
            error_label = tk.Label(form, text="", fg="red", font=("Arial", 11))
        error_label.pack(pady=(10, 0))

        def set_error(msg):
            if ctk:
                error_label.configure(text=msg)
            else:
                error_label.config(text=msg)

        def on_save():
            name = name_entry.get().strip()
            age_str = age_entry.get().strip()
            grade = grade_var.get()
            level = level_var.get()
            time_str = time_entry.get().strip()

            if not name or len(name) < 2:
                set_error("Name must be at least 2 characters")
                return

            try:
                age = int(age_str)
                if age < 5 or age > 18:
                    raise ValueError
            except ValueError:
                set_error("Age must be a number between 5 and 18")
                return

            try:
                time_limit = int(time_str)
                if time_limit < 0 or time_limit > 1440:
                    raise ValueError
            except ValueError:
                set_error("Time limit must be 0-1440 minutes")
                return

            try:
                if editing:
                    self.profile_manager.update_profile(
                        profile.profile_id,
                        name=name,
                        age=age,
                        grade=grade,
                        learning_level=level,
                        daily_time_limit_minutes=time_limit
                    )
                    messagebox.showinfo("Success", f"{name}'s profile has been updated.")
                else:
                    parent_id = self.session_data.get('parent_id', '')
                    self.profile_manager.create_profile(
                        parent_id=parent_id,
                        name=name,
                        age=age,
                        grade=grade,
                        learning_level=level,
                        daily_time_limit_minutes=time_limit
                    )
                    messagebox.showinfo("Success", f"{name}'s profile has been created.")

                dialog.destroy()
                self._load_profiles()
                self._show_tab("profiles")
            except Exception as e:
                set_error(str(e))

        # Buttons
        if ctk:
            btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        else:
            btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=20)

        if ctk:
            ctk.CTkButton(btn_frame, text="Save", width=120, command=on_save).pack(side=tk.LEFT, padx=10)
            ctk.CTkButton(btn_frame, text="Cancel", width=120, fg_color="gray", command=dialog.destroy).pack(side=tk.LEFT, padx=10)
        else:
            tk.Button(btn_frame, text="Save", width=12, command=on_save).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="Cancel", width=12, command=dialog.destroy).pack(side=tk.LEFT, padx=10)
    
    def _delete_profile(self, profile):
        """Delete child profile"""
        result = messagebox.askyesno(
            "Delete Profile",
            f"Are you sure you want to delete {profile.name}'s profile?\n\n"
            "This will remove all conversation history and cannot be undone."
        )

        if result:
            try:
                self.profile_manager.delete_profile(profile.profile_id)
                messagebox.showinfo("Success", f"{profile.name}'s profile has been deleted.")
                self._load_profiles()
                self._show_tab("profiles")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete profile:\n\n{e}")
    
    def _change_password(self):
        """Change parent password via dialog"""
        if ctk:
            dialog = ctk.CTkToplevel(self.window)
        else:
            dialog = tk.Toplevel(self.window)

        dialog.title("Change Password")
        dialog.geometry("400x380")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()

        # Center on parent window
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() - 400) // 2
        y = self.window.winfo_y() + (self.window.winfo_height() - 380) // 2
        dialog.geometry(f"400x380+{x}+{y}")

        # Title
        if ctk:
            ctk.CTkLabel(dialog, text="Change Password", font=("Arial", 18, "bold")).pack(pady=(20, 15))
        else:
            tk.Label(dialog, text="Change Password", font=("Arial", 18, "bold")).pack(pady=(20, 15))

        # Form
        if ctk:
            form = ctk.CTkFrame(dialog)
        else:
            form = tk.Frame(dialog)
        form.pack(fill=tk.X, padx=30, pady=10)

        # Current password
        if ctk:
            ctk.CTkLabel(form, text="Current Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            current_entry = ctk.CTkEntry(form, width=330, show="*")
        else:
            tk.Label(form, text="Current Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            current_entry = tk.Entry(form, width=40, show="*")
        current_entry.pack(fill=tk.X)

        # New password
        if ctk:
            ctk.CTkLabel(form, text="New Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            new_entry = ctk.CTkEntry(form, width=330, show="*")
        else:
            tk.Label(form, text="New Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            new_entry = tk.Entry(form, width=40, show="*")
        new_entry.pack(fill=tk.X)

        # Confirm new password
        if ctk:
            ctk.CTkLabel(form, text="Confirm New Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            confirm_entry = ctk.CTkEntry(form, width=330, show="*")
        else:
            tk.Label(form, text="Confirm New Password:", font=("Arial", 12)).pack(anchor=tk.W, pady=(10, 2))
            confirm_entry = tk.Entry(form, width=40, show="*")
        confirm_entry.pack(fill=tk.X)

        # Error display
        if ctk:
            error_label = ctk.CTkLabel(form, text="", text_color="red", font=("Arial", 11))
        else:
            error_label = tk.Label(form, text="", fg="red", font=("Arial", 11))
        error_label.pack(pady=(10, 0))

        def set_error(msg):
            if ctk:
                error_label.configure(text=msg)
            else:
                error_label.config(text=msg)

        def on_save():
            current = current_entry.get()
            new = new_entry.get()
            confirm = confirm_entry.get()

            if not current or not new or not confirm:
                set_error("All fields are required")
                return

            if new != confirm:
                set_error("New passwords do not match")
                return

            parent_id = self.session_data.get('parent_id', '')
            success, error = auth_manager.change_password(parent_id, current, new)

            if success:
                dialog.destroy()
                messagebox.showinfo(
                    "Success",
                    "Password changed successfully.\n\n"
                    "You will need to log in again with your new password."
                )
                self._logout()
            else:
                set_error(error or "Password change failed")

        # Buttons
        if ctk:
            btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        else:
            btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=20)

        if ctk:
            ctk.CTkButton(btn_frame, text="Change Password", width=140, command=on_save).pack(side=tk.LEFT, padx=10)
            ctk.CTkButton(btn_frame, text="Cancel", width=100, fg_color="gray", command=dialog.destroy).pack(side=tk.LEFT, padx=10)
        else:
            tk.Button(btn_frame, text="Change Password", width=16, command=on_save).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="Cancel", width=10, command=dialog.destroy).pack(side=tk.LEFT, padx=10)
    
    def _logout(self):
        """Logout parent"""
        result = messagebox.askyesno(
            "Logout",
            "Are you sure you want to logout?"
        )
        
        if result:
            session_token = self.session_data.get('session_token', '')
            if session_token:
                auth_manager.logout(session_token)
            self._on_window_close()
    
    def _get_profile_ids(self) -> List[str]:
        """Get profile IDs for the logged-in parent's children"""
        return [p.profile_id for p in self.profiles]

    def _get_active_profiles_today(self) -> int:
        """Get count of this parent's profiles active today"""
        try:
            profile_ids = self._get_profile_ids()
            if not profile_ids:
                return 0
            today = datetime.now(timezone.utc).date().isoformat()
            placeholders = ",".join("?" for _ in profile_ids)
            results = db_manager.execute_query(
                f"""
                SELECT COUNT(DISTINCT profile_id) as count
                FROM sessions
                WHERE DATE(started_at) = ?
                  AND profile_id IN ({placeholders})
                """,
                (today, *profile_ids)
            )
            return results[0]['count'] if results else 0
        except Exception as e:
            logger.error(f"Failed to get active profiles: {e}")
            return 0

    def _get_total_sessions_today(self) -> int:
        """Get total sessions today for this parent's children"""
        try:
            profile_ids = self._get_profile_ids()
            if not profile_ids:
                return 0
            today = datetime.now(timezone.utc).date().isoformat()
            placeholders = ",".join("?" for _ in profile_ids)
            results = db_manager.execute_query(
                f"""
                SELECT COUNT(*) as count
                FROM sessions
                WHERE DATE(started_at) = ?
                  AND profile_id IN ({placeholders})
                """,
                (today, *profile_ids)
            )
            return results[0]['count'] if results else 0
        except Exception as e:
            logger.error(f"Failed to get sessions: {e}")
            return 0

    def _get_pending_incidents(self) -> int:
        """Get count of unresolved incidents for this parent's children"""
        try:
            profile_ids = self._get_profile_ids()
            if not profile_ids:
                return 0
            placeholders = ",".join("?" for _ in profile_ids)
            results = db_manager.execute_query(
                f"""
                SELECT COUNT(*) as count
                FROM safety_incidents
                WHERE resolved = 0
                  AND profile_id IN ({placeholders})
                """,
                tuple(profile_ids)
            )
            return results[0]['count'] if results else 0
        except Exception as e:
            logger.error(f"Failed to get incidents: {e}")
            return 0

    def _get_recent_sessions(self, limit: int = 5) -> List[Dict]:
        """Get recent sessions for this parent's children"""
        try:
            profile_ids = self._get_profile_ids()
            if not profile_ids:
                return []
            placeholders = ",".join("?" for _ in profile_ids)
            results = db_manager.execute_query(
                f"""
                SELECT s.session_id, s.started_at, s.questions_asked,
                       cp.name as profile_name
                FROM sessions s
                LEFT JOIN child_profiles cp ON s.profile_id = cp.profile_id
                WHERE s.profile_id IN ({placeholders})
                ORDER BY s.started_at DESC
                LIMIT ?
                """,
                (*profile_ids, limit)
            )
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"Failed to get recent sessions: {e}")
            return []
    
    def _on_window_close(self):
        """Handle window close"""
        try:
            self.window.destroy()
        except Exception:
            # Ignore errors during window destruction
            pass
        
        if self.on_close:
            self.on_close()


# Export public interface
__all__ = ['ParentDashboard']
