# ui/setup_wizard.py
"""
Setup Wizard - First-Time Family Setup
Step-by-step guided setup for non-technical parents (<5 minutes)
"""

import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Optional, Callable
import re

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
        self.child_name = ""
        self.child_age = 10
        self.child_grade = "5th"
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
                text="Welcome to snflwr.ai! 🌻",
                font=("Arial", 28, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Welcome to snflwr.ai! 🌻",
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
            "• At least 8 characters\n"
            "• Include uppercase and lowercase letters\n"
            "• Include at least one number"
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
                text="Set Up a Child Profile",
                font=("Arial", 24, "bold")
            )
        else:
            title = tk.Label(
                self.content_frame,
                text="Set Up a Child Profile",
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
        skip_button.pack(pady=(0, 20))
        
        # Form
        if ctk:
            form_frame = ctk.CTkFrame(self.content_frame)
        else:
            form_frame = tk.Frame(self.content_frame, bg="white")
        form_frame.pack(pady=20)
        
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
        self.child_name_entry.insert(0, self.child_name)
        
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
        
        # Note
        note = (
            "You can add or manage child profiles later from the dashboard."
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
        note_label.pack(pady=20)
    
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
    
    def _skip_child_profile(self):
        """Skip child profile creation and proceed to account creation"""
        self.skip_child_profile = True
        self.child_name = ""
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

        # If proceeding from child profile step via Next, they want a profile
        if self.current_step == 2:
            self.skip_child_profile = False

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
        
        elif self.current_step == 2:
            # Validate child profile
            name = self.child_name_entry.get().strip()
            
            if not name:
                messagebox.showwarning("Required", "Please enter your child's name.")
                return False
        
        return True
    
    def _save_current_step_data(self):
        """Save data from current step"""
        if self.current_step == 1:
            self.parent_username = self.username_entry.get().strip()
            self.parent_password = self.password_entry.get()
            self.parent_email = self.email_entry.get().strip()
        
        elif self.current_step == 2:
            self.child_name = self.child_name_entry.get().strip()
            # Age and grade saved when creating account
    
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

            if not self.skip_child_profile:
                # Update progress
                self._update_progress("Creating child profile...")

                # Get age and grade from entries
                try:
                    age = int(self.age_spinbox.get()) if hasattr(self, 'age_spinbox') else 10
                except (ValueError, TypeError, AttributeError):
                    age = 10

                grade = self.grade_combo.get() if hasattr(self, 'grade_combo') else "5th"

                # Create child profile
                profile_mgr = ProfileManager(auth_manager.db)

                # Authenticate to verify credentials before creating profile
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

                # Create profile
                parent_id = auth_result.get('parent_id')
                if not parent_id:
                    raise Exception("Missing parent_id from authentication result")

                profile = profile_mgr.create_profile(
                    parent_id=parent_id,
                    name=self.child_name,
                    age=age,
                    grade=grade
                )

                logger.info(f"Child profile created: {profile.profile_id}")

            # Update progress
            self._update_progress("Setup complete! 🌻")
            
            # Wait a moment then close
            self.window.after(1500, lambda: self._complete_setup(True))
            
        except Exception as e:
            logger.error(f"Account creation failed: {e}")
            messagebox.showerror(
                "Setup Error",
                f"Failed to create account:\n\n{str(e)}\n\n"
                "Please try again or contact support."
            )
            self._complete_setup(False)
    
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
