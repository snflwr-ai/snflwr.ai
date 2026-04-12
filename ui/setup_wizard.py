# ui/setup_wizard.py
"""
Setup Wizard - First-Time Family Setup
Step-by-step guided setup for non-technical parents (<5 minutes)
"""

import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Optional, Callable, List, Dict
import re
import secrets
import string

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

from config import system_config
from core.authentication import auth_manager
from core.profile_manager import ProfileManager
from utils.logger import get_logger

logger = get_logger(__name__)


class SetupWizard:
    """
    Multi-step setup wizard for first-time users
    Guides families through account creation and first profile setup
    """
    
    def __init__(
        self,
        parent_window: tk.Tk,
        cdrom_path: Optional[Path] = None,
        usb_path: Optional[Path] = None
    ):
        """
        Initialize setup wizard

        Args:
            parent_window: Parent Tk window (to be hidden)
            cdrom_path: CD-ROM partition path (None for non-USB installs)
            usb_path: USB/data partition path (None for non-USB installs)
        """
        self.parent_window = parent_window
        self.cdrom_path = cdrom_path
        self.usb_path = usb_path
        
        self.window = None
        self.current_step = 0
        self.total_steps = 4
        
        # Collected data
        self.parent_username = ""
        self.parent_password = ""
        self.parent_email = ""
        self.child_profiles = []  # list of {"name": str, "age": int, "grade": str}
        self.skip_child_profile = False
        # Completion callback
        self.on_complete: Optional[Callable[[bool], None]] = None
        
        # UI components
        self.step_label = None
        self.content_frame = None
        self.back_button = None
        self.next_button = None
        
        logger.info("Setup wizard initialized")
    
    def show(self):
        """Show setup wizard window"""
        try:
            # Create wizard window
            if ctk:
                self.window = ctk.CTkToplevel(self.parent_window)
            else:
                self.window = tk.Toplevel(self.parent_window)
            
            self.window.title("snflwr.ai Setup - Welcome!")
            self.window.geometry("900x700")
            self.window.resizable(False, False)
            
            # Center window
            self._center_window()
            
            # Handle window close
            self.window.protocol("WM_DELETE_WINDOW", self._on_cancel)
            
            # Create UI
            self._create_ui()
            
            # Show first step
            self._show_step(0)
            
            logger.info("Setup wizard window shown")
            
        except Exception as e:
            logger.error(f"Failed to show setup wizard: {e}")
            messagebox.showerror(
                "Setup Error",
                f"Failed to start setup wizard:\n\n{str(e)}"
            )
    
    def _center_window(self):
        """Center window on screen"""
        self.window.update_idletasks()
        
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        
        x = (screen_width - 900) // 2
        y = (screen_height - 700) // 2
        
        self.window.geometry(f"900x700+{x}+{y}")
    
    def _create_ui(self):
        """Create wizard UI structure"""
        # Header with step indicator
        header_frame = self._create_header()
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 0))
        
        # Content area (changes per step)
        if ctk:
            self.content_frame = ctk.CTkFrame(self.window)
        else:
            self.content_frame = tk.Frame(self.window, bg="white")
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Navigation buttons
        nav_frame = self._create_navigation()
        nav_frame.pack(fill=tk.X, padx=20, pady=(0, 20))
    
    def _create_header(self) -> tk.Frame:
        """Create header with step indicator"""
        if ctk:
            frame = ctk.CTkFrame(self.window, fg_color="transparent")
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Step indicator
        if ctk:
            self.step_label = ctk.CTkLabel(
                frame,
                text="Step 1 of 4",
                font=("Arial", 14)
            )
        else:
            self.step_label = tk.Label(
                frame,
                text="Step 1 of 4",
                font=("Arial", 14),
                bg="white",
                fg="gray"
            )
        self.step_label.pack()
        
        return frame
    
    def _create_navigation(self) -> tk.Frame:
        """Create navigation button frame"""
        if ctk:
            frame = ctk.CTkFrame(self.window, fg_color="transparent")
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Back button
        if ctk:
            self.back_button = ctk.CTkButton(
                frame,
                text="← Back",
                font=("Arial", 13),
                width=120,
                command=self._on_back
            )
        else:
            self.back_button = tk.Button(
                frame,
                text="← Back",
                font=("Arial", 13),
                width=12,
                command=self._on_back
            )
        self.back_button.pack(side=tk.LEFT)
        
        # Next button
        if ctk:
            self.next_button = ctk.CTkButton(
                frame,
                text="Next →",
                font=("Arial", 13, "bold"),
                width=120,
                command=self._on_next
            )
        else:
            self.next_button = tk.Button(
                frame,
                text="Next →",
                font=("Arial", 13, "bold"),
                width=12,
                bg="#007AFF",
                fg="white",
                command=self._on_next
            )
        self.next_button.pack(side=tk.RIGHT)
        
        return frame
    
    def _show_step(self, step: int):
        """Show specific setup step"""
        self.current_step = step
        
        # Update step indicator
        if self.step_label:
            step_text = f"Step {step + 1} of {self.total_steps}"
            if ctk:
                self.step_label.configure(text=step_text)
            else:
                self.step_label.config(text=step_text)
        
        # Clear content frame
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        
        # Show appropriate step content
        if step == 0:
            self._show_welcome_step()
        elif step == 1:
            self._show_parent_account_step()
        elif step == 2:
            self._show_child_profile_step()
        elif step == 3:
            self._show_completion_step()
        
        # Update navigation buttons
        self._update_navigation()
    
    def _show_welcome_step(self):
        """Step 0: Welcome and overview"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Welcome to snflwr.ai!",
                font=("Arial", 28, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Welcome to snflwr.ai!",
                font=("Arial", 28, "bold"),
                bg="white"
            )
        title.pack(pady=(40, 20))
        
        # Description
        description = (
            "Let's get you set up for learning!\n\n"
            "This will take about 3 minutes. We'll:\n\n"
            "1. Create your account\n"
            "2. Optionally set up a child's profile\n"
            "3. Get you ready to start learning\n\n"
            "Ready to begin?"
        )
        
        if ctk:
            desc_label = ctk.CTkLabel(
                self.content_frame,
                text=description,
                font=("Arial", 14),
                wraplength=700
            )
        else:
            desc_label = tk.Label(
                self.content_frame,
                text=description,
                font=("Arial", 14),
                bg="white",
                wraplength=700,
                justify=tk.LEFT
            )
        desc_label.pack(pady=20)
    
    def _show_parent_account_step(self):
        """Step 1: Create parent account"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Create Your Parent Account",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Create Your Parent Account",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 30))
        
        # Form frame
        if ctk:
            form_frame = ctk.CTkFrame(self.content_frame)
        else:
            form_frame = tk.Frame(self.content_frame, bg="white")
        form_frame.pack(pady=20)
        
        # Username
        if ctk:
            username_label = ctk.CTkLabel(form_frame, text="Username:", font=("Arial", 13))
        else:
            username_label = tk.Label(form_frame, text="Username:", font=("Arial", 13), bg="white")
        username_label.grid(row=0, column=0, sticky=tk.W, padx=10, pady=10)
        
        if ctk:
            self.username_entry = ctk.CTkEntry(form_frame, width=300, font=("Arial", 13))
        else:
            self.username_entry = tk.Entry(form_frame, width=30, font=("Arial", 13))
        self.username_entry.grid(row=0, column=1, padx=10, pady=10)
        self.username_entry.insert(0, self.parent_username)
        
        # Password
        if ctk:
            password_label = ctk.CTkLabel(form_frame, text="Password:", font=("Arial", 13))
        else:
            password_label = tk.Label(form_frame, text="Password:", font=("Arial", 13), bg="white")
        password_label.grid(row=1, column=0, sticky=tk.W, padx=10, pady=10)
        
        if ctk:
            self.password_entry = ctk.CTkEntry(form_frame, width=300, font=("Arial", 13), show="*")
        else:
            self.password_entry = tk.Entry(form_frame, width=30, font=("Arial", 13), show="*")
        self.password_entry.grid(row=1, column=1, padx=10, pady=10)
        self.password_entry.insert(0, self.parent_password)
        
        # Confirm password
        if ctk:
            confirm_label = ctk.CTkLabel(form_frame, text="Confirm Password:", font=("Arial", 13))
        else:
            confirm_label = tk.Label(form_frame, text="Confirm Password:", font=("Arial", 13), bg="white")
        confirm_label.grid(row=2, column=0, sticky=tk.W, padx=10, pady=10)
        
        if ctk:
            self.confirm_entry = ctk.CTkEntry(form_frame, width=300, font=("Arial", 13), show="*")
        else:
            self.confirm_entry = tk.Entry(form_frame, width=30, font=("Arial", 13), show="*")
        self.confirm_entry.grid(row=2, column=1, padx=10, pady=10)
        
        # Email (optional)
        if ctk:
            email_label = ctk.CTkLabel(form_frame, text="Email (optional):", font=("Arial", 13))
        else:
            email_label = tk.Label(form_frame, text="Email (optional):", font=("Arial", 13), bg="white")
        email_label.grid(row=3, column=0, sticky=tk.W, padx=10, pady=10)
        
        if ctk:
            self.email_entry = ctk.CTkEntry(form_frame, width=300, font=("Arial", 13))
        else:
            self.email_entry = tk.Entry(form_frame, width=30, font=("Arial", 13))
        self.email_entry.grid(row=3, column=1, padx=10, pady=10)
        self.email_entry.insert(0, self.parent_email)
        
        # Password requirements
        requirements = (
            "Password requirements:\n"
            "- At least 8 characters\n"
            "- Include uppercase and lowercase letters\n"
            "- Include at least one number\n"
            "- Include at least one special character (!@#$%^&* etc.)"
        )
        
        if ctk:
            req_label = ctk.CTkLabel(
                self.content_frame,
                text=requirements,
                font=("Arial", 11),
                text_color="gray"
            )
        else:
            req_label = tk.Label(
                self.content_frame,
                text=requirements,
                font=("Arial", 11),
                bg="white",
                fg="gray",
                justify=tk.LEFT
            )
        req_label.pack(pady=20)
    
    def _show_child_profile_step(self):
        """Step 2: Create first child profile (optional)"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Set Up Child Profiles",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Set Up Child Profiles",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(20, 10))

        # Skip option
        if ctk:
            skip_button = ctk.CTkButton(
                self.content_frame,
                text="Skip — I'm using this myself",
                font=("Arial", 12),
                fg_color="transparent",
                text_color="gray",
                hover_color="#f0f0f0",
                command=self._skip_child_profile
            )
        else:
            skip_button = tk.Button(
                self.content_frame,
                text="Skip — I'm using this myself",
                font=("Arial", 12),
                fg="gray",
                relief=tk.FLAT,
                cursor="hand2",
                command=self._skip_child_profile
            )
        skip_button.pack(pady=(0, 10))

        # Already-added profiles list
        if ctk:
            self.profiles_list_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        else:
            self.profiles_list_frame = tk.Frame(self.content_frame, bg=self.content_frame.cget("bg"))
        self.profiles_list_frame.pack(fill=tk.X, padx=40)
        self._refresh_profiles_list()

        # Form
        if ctk:
            form_frame = ctk.CTkFrame(self.content_frame)
        else:
            form_frame = tk.Frame(self.content_frame, bg="white")
        form_frame.pack(pady=10)

        # Child name
        if ctk:
            name_label = ctk.CTkLabel(form_frame, text="Child's Name:", font=("Arial", 13))
        else:
            name_label = tk.Label(form_frame, text="Child's Name:", font=("Arial", 13), bg="white")
        name_label.grid(row=0, column=0, sticky=tk.W, padx=10, pady=10)

        if ctk:
            self.child_name_entry = ctk.CTkEntry(form_frame, width=300, font=("Arial", 13))
        else:
            self.child_name_entry = tk.Entry(form_frame, width=30, font=("Arial", 13))
        self.child_name_entry.grid(row=0, column=1, padx=10, pady=10)

        # Age
        if ctk:
            age_label = ctk.CTkLabel(form_frame, text="Age:", font=("Arial", 13))
        else:
            age_label = tk.Label(form_frame, text="Age:", font=("Arial", 13), bg="white")
        age_label.grid(row=1, column=0, sticky=tk.W, padx=10, pady=10)

        if ctk:
            self.age_spinbox = ctk.CTkEntry(form_frame, width=100, font=("Arial", 13))
        else:
            self.age_spinbox = tk.Spinbox(form_frame, from_=5, to=18, width=10, font=("Arial", 13))
        self.age_spinbox.grid(row=1, column=1, sticky=tk.W, padx=10, pady=10)

        # Grade
        if ctk:
            grade_label = ctk.CTkLabel(form_frame, text="Grade:", font=("Arial", 13))
        else:
            grade_label = tk.Label(form_frame, text="Grade:", font=("Arial", 13), bg="white")
        grade_label.grid(row=2, column=0, sticky=tk.W, padx=10, pady=10)

        grades = ["K", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"]

        if ctk:
            self.grade_combo = ctk.CTkComboBox(form_frame, values=grades, width=150, font=("Arial", 13))
        else:
            from tkinter import ttk
            self.grade_combo = ttk.Combobox(form_frame, values=grades, width=15, font=("Arial", 13))
        self.grade_combo.grid(row=2, column=1, sticky=tk.W, padx=10, pady=10)

        # Add Another Child button
        if ctk:
            add_btn = ctk.CTkButton(
                self.content_frame,
                text="+ Add This Child",
                font=("Arial", 13),
                command=self._add_child_to_list
            )
        else:
            add_btn = tk.Button(
                self.content_frame,
                text="+ Add This Child",
                font=("Arial", 13),
                cursor="hand2",
                command=self._add_child_to_list
            )
        add_btn.pack(pady=(10, 5))

        # Note
        note = (
            "Add each child, then click Next when done.\n"
            "You can also manage profiles later from the dashboard."
        )

        if ctk:
            note_label = ctk.CTkLabel(
                self.content_frame,
                text=note,
                font=("Arial", 11),
                text_color="gray"
            )
        else:
            note_label = tk.Label(
                self.content_frame,
                text=note,
                font=("Arial", 11),
                bg="white",
                fg="gray"
            )
        note_label.pack(pady=10)
    
    def _show_completion_step(self):
        """Step 4: Creating account and finalizing"""
        # Title
        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Creating Your Account...",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Creating Your Account...",
                font=("Arial", 24, "bold"),
                bg="white"
            )
        title.pack(pady=(40, 30))
        
        # Progress
        if ctk:
            self.progress_label = ctk.CTkLabel(
                self.content_frame,
                text="Please wait...",
                font=("Arial", 14)
            )
        else:
            self.progress_label = tk.Label(
                self.content_frame,
                text="Please wait...",
                font=("Arial", 14),
                bg="white"
            )
        self.progress_label.pack(pady=20)
        
        # Start account creation in background
        self.window.after(500, self._create_account)
    
    def _update_navigation(self):
        """Update navigation button states"""
        # Back button
        if self.current_step == 0:
            if ctk:
                self.back_button.configure(state=tk.DISABLED)
            else:
                self.back_button.config(state=tk.DISABLED)
        else:
            if ctk:
                self.back_button.configure(state=tk.NORMAL)
            else:
                self.back_button.config(state=tk.NORMAL)
        
        # Next button
        if self.current_step == self.total_steps - 1:
            # Hide next button on completion step
            self.next_button.pack_forget()
        else:
            # Re-pack next button if it was hidden
            self.next_button.pack(side=tk.RIGHT)
            if ctk:
                self.next_button.configure(state=tk.NORMAL)
            else:
                self.next_button.config(state=tk.NORMAL)
    
    def _add_child_to_list(self):
        """Add current form data to the child profiles list"""
        name = self.child_name_entry.get().strip()
        if not name or len(name) < 2:
            messagebox.showwarning("Required", "Please enter the child's name (at least 2 characters).")
            return

        try:
            age = int(self.age_spinbox.get())
            if age < 5 or age > 18:
                raise ValueError()
        except (ValueError, TypeError):
            messagebox.showwarning("Invalid Age", "Age must be between 5 and 18.")
            return

        grade = self.grade_combo.get() if hasattr(self, 'grade_combo') else "5th"
        if not grade:
            messagebox.showwarning("Required", "Please select a grade.")
            return

        # Check for duplicate name
        for p in self.child_profiles:
            if p["name"].lower() == name.lower():
                messagebox.showwarning("Duplicate", f"A profile for '{name}' has already been added.")
                return

        self.child_profiles.append({"name": name, "age": age, "grade": grade})
        logger.info(f"Added child profile to wizard list: {name}, age {age}, grade {grade}")

        # Clear form for next child
        self.child_name_entry.delete(0, tk.END)
        if ctk:
            self.age_spinbox.delete(0, tk.END)
        # Refresh the displayed list
        self._refresh_profiles_list()

    def _remove_child_from_list(self, index):
        """Remove a child profile from the list by index"""
        if 0 <= index < len(self.child_profiles):
            removed = self.child_profiles.pop(index)
            logger.info(f"Removed child profile from wizard list: {removed['name']}")
            self._refresh_profiles_list()

    def _refresh_profiles_list(self):
        """Refresh the displayed list of added child profiles"""
        if not hasattr(self, 'profiles_list_frame'):
            return
        for widget in self.profiles_list_frame.winfo_children():
            widget.destroy()

        if not self.child_profiles:
            return

        if ctk:
            header = ctk.CTkLabel(self.profiles_list_frame, text="Added children:", font=("Arial", 12, "bold"))
        else:
            header = tk.Label(self.profiles_list_frame, text="Added children:", font=("Arial", 12, "bold"),
                              bg=self.profiles_list_frame.cget("bg"))
        header.pack(anchor=tk.W)

        for i, p in enumerate(self.child_profiles):
            if ctk:
                row = ctk.CTkFrame(self.profiles_list_frame, fg_color="transparent")
            else:
                row = tk.Frame(self.profiles_list_frame, bg=self.profiles_list_frame.cget("bg"))
            row.pack(fill=tk.X, pady=2)

            text = f"  {p['name']}  —  Age {p['age']}, {p['grade']} grade"
            if ctk:
                lbl = ctk.CTkLabel(row, text=text, font=("Arial", 12))
            else:
                lbl = tk.Label(row, text=text, font=("Arial", 12), bg=row.cget("bg"))
            lbl.pack(side=tk.LEFT)

            idx = i  # capture for closure
            if ctk:
                rm_btn = ctk.CTkButton(row, text="Remove", width=70, font=("Arial", 11),
                                        fg_color="transparent", text_color="gray",
                                        command=lambda j=idx: self._remove_child_from_list(j))
            else:
                rm_btn = tk.Button(row, text="Remove", font=("Arial", 10), fg="gray",
                                   relief=tk.FLAT, cursor="hand2",
                                   command=lambda j=idx: self._remove_child_from_list(j))
            rm_btn.pack(side=tk.RIGHT)

    def _skip_child_profile(self):
        """Skip child profile creation and proceed to account creation"""
        self.skip_child_profile = True
        self.child_profiles = []
        self._show_step(3)

    def _on_back(self):
        """Handle back button"""
        if self.current_step > 0:
            # Save current step data before going back
            self._save_current_step_data()
            self._show_step(self.current_step - 1)
    
    def _on_next(self):
        """Handle next button"""
        # Validate current step
        if not self._validate_current_step():
            return

        # If proceeding from child profile step via Next, check if they
        # have a partially filled form that hasn't been added yet
        if self.current_step == 2:
            name = self.child_name_entry.get().strip() if hasattr(self, 'child_name_entry') else ""
            if name and len(name) >= 2:
                # Auto-add the current form entry before proceeding
                self._add_child_to_list()
            self.skip_child_profile = len(self.child_profiles) == 0

        # Save data
        self._save_current_step_data()

        # Move to next step
        if self.current_step < self.total_steps - 1:
            self._show_step(self.current_step + 1)
    
    def _validate_current_step(self) -> bool:
        """Validate current step before proceeding"""
        if self.current_step == 1:
            # Validate parent account
            username = self.username_entry.get().strip()
            password = self.password_entry.get()
            confirm = self.confirm_entry.get()
            
            if not username:
                messagebox.showwarning("Required", "Please enter a username.")
                return False
            
            if len(username) < 3:
                messagebox.showwarning("Invalid Username", "Username must be at least 3 characters.")
                return False
            
            if not password:
                messagebox.showwarning("Required", "Please enter a password.")
                return False
            
            if len(password) < 8:
                messagebox.showwarning("Weak Password", "Password must be at least 8 characters.")
                return False
            
            if password != confirm:
                messagebox.showwarning("Password Mismatch", "Passwords do not match.")
                return False
            
            if not any(c.isupper() for c in password):
                messagebox.showwarning("Weak Password", "Password must include an uppercase letter.")
                return False
            
            if not any(c.islower() for c in password):
                messagebox.showwarning("Weak Password", "Password must include a lowercase letter.")
                return False
            
            if not any(c.isdigit() for c in password):
                messagebox.showwarning("Weak Password", "Password must include a number.")
                return False

            special_chars = set('!@#$%^&*()_+-=[]{}|;:,.<>?')
            if not any(c in special_chars for c in password):
                messagebox.showwarning("Weak Password", "Password must include a special character (!@#$%^&* etc.).")
                return False

        elif self.current_step == 2:
            # Valid if profiles already added or form has a name (will be auto-added)
            name = self.child_name_entry.get().strip() if hasattr(self, 'child_name_entry') else ""
            if not self.child_profiles and not name:
                messagebox.showwarning("Required", "Please add at least one child profile, or click Skip.")
                return False
        
        return True
    
    def _save_current_step_data(self):
        """Save data from current step"""
        if self.current_step == 1:
            self.parent_username = self.username_entry.get().strip()
            self.parent_password = self.password_entry.get()
            self.parent_email = self.email_entry.get().strip()
        
        elif self.current_step == 2:
            pass  # profiles are added via _add_child_to_list
    
    def _create_account(self):
        """Create parent account and child profile"""
        try:
            # Update progress
            self._update_progress("Creating parent account...")
            
            # Create parent account (first account is always admin)
            success, result = auth_manager.create_parent_account(
                username=self.parent_username,
                password=self.parent_password,
                email=self.parent_email or None,
                role='admin',
            )

            if not success:
                raise Exception(f"Account creation failed: {result}")
            parent_id = result
            
            logger.info(f"Parent account created: {parent_id}")

            if not self.skip_child_profile and self.child_profiles:
                # Create child profiles
                profile_mgr = ProfileManager(auth_manager.db)

                # Authenticate to verify credentials before creating profiles
                auth_success, auth_result = auth_manager.authenticate_parent(
                    self.parent_username,
                    self.parent_password
                )

                if not auth_success:
                    raise Exception(f"Authentication failed: {auth_result}")

                # Immediately invalidate this session token — it was only
                # used to verify credentials.  The user will get a fresh
                # session when they log in through the launcher.
                wizard_token = auth_result.get('session_token')
                if wizard_token:
                    auth_manager.logout(wizard_token)

                parent_id = auth_result.get('parent_id')
                if not parent_id:
                    raise Exception("Missing parent_id from authentication result")

                # Get Open WebUI admin token for creating student accounts
                owui_token = self._get_owui_admin_token()

                self.created_credentials = []

                for i, child in enumerate(self.child_profiles):
                    self._update_progress(
                        f"Creating child profile ({i + 1}/{len(self.child_profiles)})..."
                    )
                    profile = profile_mgr.create_profile(
                        parent_id=parent_id,
                        name=child["name"],
                        age=child["age"],
                        grade=child["grade"],
                    )
                    logger.info(f"Child profile created: {profile.profile_id} ({child['name']})")

                    # Create Open WebUI login for this child
                    creds = self._create_owui_student_account(
                        owui_token, child["name"], profile.profile_id
                    )
                    if creds:
                        self.created_credentials.append(creds)

            # Update progress
            self._update_progress("Setup complete!")

            # Show credentials summary if any were created
            if hasattr(self, 'created_credentials') and self.created_credentials:
                self.window.after(500, self._show_credentials_summary)
            else:
                self.window.after(1500, lambda: self._complete_setup(True))
            
        except Exception as e:
            logger.error(f"Account creation failed: {e}")
            messagebox.showerror(
                "Setup Error",
                f"Failed to create account:\n\n{str(e)}\n\n"
                "Please try again or contact support."
            )
            # Go back to the account creation step so the user can fix it
            self.current_step = 1
            self._show_step(1)
    
    @staticmethod
    def _generate_password(length=12):
        """Generate a random password that meets all requirements"""
        # Ensure at least one of each required character type
        pwd = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%^&*"),
        ]
        # Fill remaining length with mixed characters
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        pwd += [secrets.choice(alphabet) for _ in range(length - len(pwd))]
        # Shuffle so the required chars aren't always at the start
        import random
        random.shuffle(pwd)
        return "".join(pwd)

    def _get_owui_admin_token(self) -> str:
        """Sign into Open WebUI as the parent (admin) and return the JWT token.

        If the parent doesn't have an Open WebUI account yet, create one via
        the signup endpoint first.
        """
        import requests as http_client

        open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
        email = self.parent_email or f"{self.parent_username}@snflwr.local"

        # Try signing in first (account may already exist)
        try:
            resp = http_client.post(
                f"{open_webui_url}/api/v1/auths/signin",
                json={"email": email, "password": self.parent_password},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("token", "")
        except Exception as e:
            logger.warning(f"Open WebUI signin attempt failed: {e}")

        # Account doesn't exist — create via signup
        try:
            resp = http_client.post(
                f"{open_webui_url}/api/v1/auths/signup",
                json={
                    "name": self.parent_username,
                    "email": email,
                    "password": self.parent_password,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("token", "")
            logger.warning(f"Open WebUI signup failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.warning(f"Open WebUI signup failed: {e}")

        return ""

    def _create_owui_student_account(
        self, owui_token: str, child_name: str, profile_id: str
    ) -> Optional[Dict[str, str]]:
        """Create an Open WebUI account for a child and link it to the snflwr profile.

        Returns a dict with login credentials, or None on failure.
        """
        import requests as http_client

        if not owui_token:
            logger.warning("No Open WebUI admin token — skipping student account creation")
            return None

        open_webui_url = system_config.OPEN_WEBUI_URL.rstrip("/")
        # Generate credentials
        safe_name = re.sub(r"[^a-zA-Z0-9]", "", child_name).lower()
        email = f"{safe_name}@snflwr.local"
        password = self._generate_password()

        try:
            resp = http_client.post(
                f"{open_webui_url}/api/v1/auths/add",
                json={
                    "name": child_name,
                    "email": email,
                    "password": password,
                    "role": "user",
                },
                headers={"Authorization": f"Bearer {owui_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                owui_user_id = resp.json().get("id", "")
                # Link the Open WebUI user ID to the snflwr profile
                try:
                    from storage.database import db_manager
                    db_manager.execute_write(
                        "UPDATE child_profiles SET owui_user_id = ? WHERE profile_id = ?",
                        (owui_user_id, profile_id),
                    )
                except Exception as e:
                    logger.warning(f"Failed to link owui_user_id to profile: {e}")

                logger.info(f"Created Open WebUI account for {child_name}: {email}")
                return {"name": child_name, "email": email, "password": password}
            else:
                logger.warning(
                    f"Failed to create Open WebUI account for {child_name}: "
                    f"{resp.status_code} {resp.text}"
                )
        except Exception as e:
            logger.warning(f"Open WebUI account creation failed for {child_name}: {e}")

        return None

    def _show_credentials_summary(self):
        """Show auto-generated login credentials so the parent can save them."""
        # Clear the content frame
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if ctk:
            title = ctk.CTkLabel(
                self.content_frame,
                text="Setup Complete!",
                font=("Arial", 24, "bold"),
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Setup Complete!",
                font=("Arial", 24, "bold"),
                bg="white",
            )
        title.pack(pady=(20, 10))

        if ctk:
            subtitle = ctk.CTkLabel(
                self.content_frame,
                text="Save these login credentials for your children:",
                font=("Arial", 14),
            )
        else:
            subtitle = tk.Label(
                self.content_frame,
                text="Save these login credentials for your children:",
                font=("Arial", 14),
                bg="white",
            )
        subtitle.pack(pady=(0, 15))

        # Credentials list
        for cred in self.created_credentials:
            if ctk:
                frame = ctk.CTkFrame(self.content_frame)
            else:
                frame = tk.Frame(self.content_frame, bg="#f0f0f0", relief=tk.RIDGE, bd=1)
            frame.pack(fill=tk.X, padx=60, pady=5)

            text = f"  {cred['name']}    Email: {cred['email']}    Password: {cred['password']}"
            if ctk:
                lbl = ctk.CTkLabel(frame, text=text, font=("Courier", 12), anchor="w")
            else:
                lbl = tk.Label(frame, text=text, font=("Courier", 12), bg="#f0f0f0", anchor="w")
            lbl.pack(fill=tk.X, padx=10, pady=8)

        # Note
        note_text = (
            "You can change these passwords later from the parent dashboard.\n"
            "Write them down or take a screenshot before closing."
        )
        if ctk:
            note = ctk.CTkLabel(
                self.content_frame, text=note_text, font=("Arial", 12), text_color="gray"
            )
        else:
            note = tk.Label(
                self.content_frame, text=note_text, font=("Arial", 12), bg="white", fg="gray"
            )
        note.pack(pady=20)

        # Done button
        if ctk:
            done_btn = ctk.CTkButton(
                self.content_frame,
                text="Done",
                font=("Arial", 14),
                command=lambda: self._complete_setup(True),
            )
        else:
            done_btn = tk.Button(
                self.content_frame,
                text="Done",
                font=("Arial", 14),
                cursor="hand2",
                command=lambda: self._complete_setup(True),
            )
        done_btn.pack(pady=10)

        # Hide navigation buttons
        if hasattr(self, 'back_button'):
            self.back_button.pack_forget()
        if hasattr(self, 'next_button'):
            self.next_button.pack_forget()

    def _update_progress(self, text: str):
        """Update progress label"""
        if hasattr(self, 'progress_label') and self.progress_label:
            if ctk:
                self.progress_label.configure(text=text)
            else:
                self.progress_label.config(text=text)
            self.window.update()
    
    def _complete_setup(self, success: bool):
        """Complete setup and callback"""
        try:
            self.window.destroy()
        except Exception:
            # Ignore errors during window destruction
            pass
        
        if self.on_complete:
            self.on_complete(success)
    
    def _on_cancel(self):
        """Handle window close/cancel"""
        if self.current_step > 0:
            result = messagebox.askyesno(
                "Cancel Setup",
                "Are you sure you want to cancel setup?\n\n"
                "Your progress will not be saved."
            )
            
            if not result:
                return
        
        self._complete_setup(False)


# Export public interface
__all__ = ['SetupWizard']
