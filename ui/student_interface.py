# ui/student_interface.py
"""
Student Chat Interface - Child-Friendly AI Learning Environment
Age-adaptive interface with real-time safety monitoring and educational features
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
from typing import Optional, Callable, List, Dict
from datetime import datetime, timezone
import threading

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

from config import system_config
from core.profile_manager import ChildProfile
from core.session_manager import session_manager
from core.model_manager import model_manager
from safety.pipeline import safety_pipeline
from safety.safety_monitor import safety_monitor
from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


class StudentInterface:
    """
    Child-friendly chat interface for learning
    Age-adaptive UI with comprehensive safety features
    """
    
    def __init__(
        self,
        parent_window: tk.Tk,
        profile: ChildProfile
    ):
        """
        Initialize student interface

        Args:
            parent_window: Parent Tk window
            profile: Child profile
        """
        self.parent_window = parent_window
        self.profile = profile
        
        self.window = None
        self.session_id = None
        self.conversation_history: List[Dict] = []
        
        # UI components
        self.chat_display = None
        self.input_entry = None
        self.send_button = None
        self.subject_buttons = {}
        
        # State
        self.current_subject = "general"
        self.is_sending = False
        
        # Callback
        self.on_close: Optional[Callable[[], None]] = None
        
        logger.info(f"Student interface initialized for {profile.name}")
    
    def show(self):
        """Show student interface"""
        try:
            # Create window
            if ctk:
                self.window = ctk.CTkToplevel(self.parent_window)
            else:
                self.window = tk.Toplevel(self.parent_window)
            
            self.window.title(f"snflwr.ai - {self.profile.name}'s Learning")
            self.window.geometry("1000x700")
            
            # Center window
            self._center_window()
            
            # Handle close
            self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)
            
            # Create UI
            self._create_ui()
            
            # Start session
            self._start_session()
            
            # Start safety monitoring
            safety_monitor.start_monitoring(
                self.profile.profile_id,
                self.profile.parent_id
            )
            
            # Load model if needed
            self._load_model()
            
            # Show welcome message
            self._show_welcome_message()
            
            logger.info(f"Student interface shown for {self.profile.name}")
            
        except Exception as e:
            logger.error(f"Failed to show student interface: {e}")
            messagebox.showerror(
                "Error",
                f"Failed to open learning interface:\n\n{str(e)}"
            )
    
    def _center_window(self):
        """Center window on screen"""
        self.window.update_idletasks()
        
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        
        x = (screen_width - 1000) // 2
        y = (screen_height - 700) // 2
        
        self.window.geometry(f"1000x700+{x}+{y}")
    
    def _create_ui(self):
        """Create student interface UI"""
        # Header
        header_frame = self._create_header()
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        # Main content area
        content_frame = self._create_content_area()
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Input area
        input_frame = self._create_input_area()
        input_frame.pack(fill=tk.X, padx=20, pady=(10, 20))
    
    def _create_header(self) -> tk.Frame:
        """Create header with profile info"""
        if ctk:
            frame = ctk.CTkFrame(self.window, fg_color="transparent")
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Profile name
        welcome_text = f"Hi {self.profile.name}!"
        if ctk:
            name_label = ctk.CTkLabel(
                frame,
                text=welcome_text,
                font=("Arial", 20, "bold")
            )
        else:
            name_label = tk.Label(
                frame,
                text=welcome_text,
                font=("Arial", 20, "bold"),
                bg="white"
            )
        name_label.pack(side=tk.LEFT)
        
        # Exit button
        if ctk:
            exit_btn = ctk.CTkButton(
                frame,
                text="Exit",
                width=80,
                command=self._on_window_close
            )
        else:
            exit_btn = tk.Button(
                frame,
                text="Exit",
                width=8,
                command=self._on_window_close
            )
        exit_btn.pack(side=tk.RIGHT)
        
        return frame
    
    def _create_content_area(self) -> tk.Frame:
        """Create main content area with chat and sidebar"""
        if ctk:
            frame = ctk.CTkFrame(self.window)
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Chat area (left side)
        chat_frame = self._create_chat_area(frame)
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Sidebar (right side)
        sidebar_frame = self._create_sidebar(frame)
        sidebar_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        
        return frame
    
    def _create_chat_area(self, parent) -> tk.Frame:
        """Create chat display area"""
        if ctk:
            frame = ctk.CTkFrame(parent)
        else:
            frame = tk.Frame(parent, bg="white", relief=tk.RIDGE, bd=2)
        
        # Title
        if ctk:
            title = ctk.CTkLabel(
                frame,
                text="Chat with Snflwr",
                font=("Arial", 16, "bold")
            )
        else:
            title = tk.Label(
                frame,
                text="Chat with Snflwr",
                font=("Arial", 16, "bold"),
                bg="white"
            )
        title.pack(pady=(10, 5))
        
        # Chat display
        self.chat_display = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            width=60,
            height=25,
            font=("Arial", 12),
            state=tk.DISABLED
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Configure tags for styling
        self.chat_display.tag_config("user", foreground="#007AFF", font=("Arial", 12, "bold"))
        self.chat_display.tag_config("assistant", foreground="#34C759", font=("Arial", 12, "bold"))
        self.chat_display.tag_config("system", foreground="#666666", font=("Arial", 11, "italic"))
        
        return frame
    
    def _create_sidebar(self, parent) -> tk.Frame:
        """Create sidebar with subject selection"""
        if ctk:
            frame = ctk.CTkFrame(parent, width=200)
        else:
            frame = tk.Frame(parent, bg="#f8f8f8", width=200, relief=tk.RIDGE, bd=2)
        
        # Subjects title
        if ctk:
            subjects_title = ctk.CTkLabel(
                frame,
                text="Subjects",
                font=("Arial", 14, "bold")
            )
        else:
            subjects_title = tk.Label(
                frame,
                text="Subjects",
                font=("Arial", 14, "bold"),
                bg="#f8f8f8"
            )
        subjects_title.pack(pady=(10, 5))
        
        # Subject buttons
        subjects = [
            ("general", "[DOCS] General"),
            ("math", "[MATH] Math"),
            ("science", "[SCIENCE] Science"),
            ("technology", "[CODE] Technology"),
            ("engineering", "[CONFIG] Engineering")
        ]
        
        for subject_id, subject_name in subjects:
            if ctk:
                btn = ctk.CTkButton(
                    frame,
                    text=subject_name,
                    width=180,
                    command=lambda s=subject_id: self._select_subject(s)
                )
            else:
                btn = tk.Button(
                    frame,
                    text=subject_name,
                    width=20,
                    command=lambda s=subject_id: self._select_subject(s)
                )
            btn.pack(pady=5, padx=10)
            self.subject_buttons[subject_id] = btn
        
        # Help button
        if ctk:
            help_btn = ctk.CTkButton(
                frame,
                text="[?] Help",
                width=180,
                command=self._show_help
            )
        else:
            help_btn = tk.Button(
                frame,
                text="[?] Help",
                width=20,
                command=self._show_help
            )
        help_btn.pack(pady=(20, 10), padx=10)
        
        return frame
    
    def _create_input_area(self) -> tk.Frame:
        """Create message input area"""
        if ctk:
            frame = ctk.CTkFrame(self.window)
        else:
            frame = tk.Frame(self.window, bg="white")
        
        # Input field
        if ctk:
            self.input_entry = ctk.CTkEntry(
                frame,
                placeholder_text="Ask me anything about your studies...",
                font=("Arial", 13),
                height=40
            )
        else:
            self.input_entry = tk.Entry(
                frame,
                font=("Arial", 13)
            )
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        # Bind enter key
        self.input_entry.bind("<Return>", lambda e: self._send_message())
        
        # Send button
        if ctk:
            self.send_button = ctk.CTkButton(
                frame,
                text="Send",
                width=100,
                height=40,
                font=("Arial", 13, "bold"),
                command=self._send_message
            )
        else:
            self.send_button = tk.Button(
                frame,
                text="Send",
                width=10,
                font=("Arial", 13, "bold"),
                bg="#007AFF",
                fg="white",
                command=self._send_message
            )
        self.send_button.pack(side=tk.RIGHT)
        
        return frame
    
    def _start_session(self):
        """Start learning session"""
        try:
            session = session_manager.create_session(
                profile_id=self.profile.profile_id,
                parent_id=self.profile.parent_id,
                session_type='student'
            )
            self.session_id = session.session_id
            logger.info(f"Session started: {self.session_id}")
                
        except Exception as e:
            logger.error(f"Session start error: {e}")
    
    def _load_model(self):
        """Load AI model for current tier"""
        try:
            # Show loading message
            self._add_system_message("Loading AI model, please wait...")
            
            def load_in_background():
                model_name = system_config.OLLAMA_DEFAULT_MODEL
                if not model_name:
                    self._add_system_message(
                        "No AI model configured. Please set OLLAMA_DEFAULT_MODEL or run the setup script."
                    )
                    return
                result = model_manager.load_model(model_name)

                if result.get('status') == 'loaded':
                    self._add_system_message("AI model ready! You can start asking questions.")
                else:
                    error = result.get('error', 'Unknown error')
                    self._add_system_message(
                        f"Failed to load AI model: {error}\n"
                        "Some features may not work properly."
                    )
            
            # Load in background thread
            thread = threading.Thread(target=load_in_background, daemon=True)
            thread.start()
            
        except Exception as e:
            logger.error(f"Model loading error: {e}")
            self._add_system_message("AI model loading error. Please restart the application.")
    
    def _show_welcome_message(self):
        """Show personalized welcome message"""
        age = self.profile.age
        if age <= 7:
            age_group = "K-2"
        elif age <= 10:
            age_group = "Elementary"
        elif age <= 13:
            age_group = "Middle School"
        else:
            age_group = "High School"
        
        if age_group == "K-2":
            message = (
                "Hi! I'm Snflwr, your friendly learning helper!\n\n"
                "I can help you with:\n"
                "- Math problems\n"
                "- Science questions\n"
                "- Reading and writing\n"
                "- And lots more!\n\n"
                "Just ask me anything you want to learn about!"
            )
        elif age_group == "Elementary":
            message = (
                "Hello! I'm Snflwr, your AI learning assistant!\n\n"
                "I'm here to help you with:\n"
                "- Math homework and practice\n"
                "- Science experiments and concepts\n"
                "- Technology and coding basics\n"
                "- Engineering projects\n\n"
                "Ask me any question about your studies!"
            )
        elif age_group == "Middle":
            message = (
                "Welcome! I'm Snflwr, your STEM tutor!\n\n"
                "I can assist you with:\n"
                "- Advanced math concepts\n"
                "- Physical and life sciences\n"
                "- Computer science and programming\n"
                "- Engineering principles\n\n"
                "What would you like to learn about today?"
            )
        else:  # High School
            message = (
                "Welcome back! I'm Snflwr, your advanced STEM tutor!\n\n"
                "I'm ready to help with:\n"
                "- AP-level mathematics and sciences\n"
                "- Advanced programming concepts\n"
                "- Engineering design and analysis\n"
                "- College preparation\n\n"
                "What challenging topic can I help you with today?"
            )
        
        self._add_assistant_message(message)
    
    def _send_message(self):
        """Send user message"""
        if self.is_sending:
            return
        
        # Get message
        message = self.input_entry.get().strip()
        
        if not message:
            return
        
        # Clear input
        self.input_entry.delete(0, tk.END)
        
        # Display user message
        self._add_user_message(message)
        
        # Check safety filter
        filter_result = safety_pipeline.check_input(
            message,
            self.profile.age,
            self.profile.profile_id
        )

        if not filter_result.is_safe:
            # Content filtered
            safe_response = safety_pipeline.get_safe_response(filter_result)
            self._add_assistant_message(safe_response)
            
            # Monitor the filtered content
            if self.session_id:
                safety_monitor.monitor_message(
                    self.profile.profile_id,
                    message,
                    message_type='user',
                    session_id=self.session_id
                )

            return
        
        # Disable send button
        self.is_sending = True
        if ctk:
            self.send_button.configure(state=tk.DISABLED, text="Thinking...")
        else:
            self.send_button.config(state=tk.DISABLED, text="Thinking...")
        
        # Generate response in background
        thread = threading.Thread(
            target=self._generate_response,
            args=(message,),
            daemon=True
        )
        thread.start()
    
    def _generate_response(self, message: str):
        """Generate AI response (runs in background thread)"""
        try:
            # Build conversation context
            context_messages = []
            for msg in self.conversation_history[-5:]:  # Last 5 messages
                context_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
            
            # Add current message
            context_messages.append({
                'role': 'user',
                'content': message
            })
            
            # Generate response
            model_name = system_config.OLLAMA_DEFAULT_MODEL
            if not model_name:
                return "No AI model configured. Please run the setup script first."
            success, response, metadata = model_manager.generate(
                model_name=model_name,
                prompt=message,
                options={'temperature': 0.7}
            )
            
            if success:
                # Filter output
                output_filter = safety_pipeline.check_output(
                    response,
                    self.profile.age,
                    self.profile.profile_id
                )
                
                if output_filter.is_safe:
                    # Display response
                    if self.window:
                        self.window.after(0, self._add_assistant_message, response)
                    
                    # Save to conversation history
                    self.conversation_history.append({
                        'role': 'user',
                        'content': message,
                        'timestamp': datetime.now(timezone.utc)
                    })
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': response,
                        'timestamp': datetime.now(timezone.utc)
                    })
                    
                    # Update session stats
                    if self.session_id:
                        session_manager.increment_question_count(self.session_id)
                    
                    # Monitor conversation
                    if self.session_id:
                        safety_monitor.monitor_message(
                            self.profile.profile_id,
                            message,
                            message_type='user',
                            session_id=self.session_id
                        )
                        safety_monitor.monitor_message(
                            self.profile.profile_id,
                            response,
                            message_type='assistant',
                            session_id=self.session_id
                        )
                else:
                    # AI generated unsafe content - use safe alternative
                    safe_response = output_filter.modified_content or safety_pipeline.get_safe_response(output_filter)
                    if self.window:
                        self.window.after(0, self._add_assistant_message, safe_response)
                    
                    logger.warning(f"AI output filtered for profile {self.profile.profile_id}")
            else:
                # Generation failed
                error_response = (
                    "I'm having trouble thinking right now. "
                    "Could you try asking your question again?"
                )
                if self.window:
                    self.window.after(0, self._add_assistant_message, error_response)
                logger.error(f"Response generation failed: {metadata}")
            
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            error_response = (
                "Oops! Something went wrong. "
                "Please try asking your question again."
            )
            if self.window:
                self.window.after(0, self._add_assistant_message, error_response)
        
        finally:
            # Re-enable send button
            self.is_sending = False
            
            def enable_button():
                if self.send_button:
                    if ctk:
                        self.send_button.configure(state=tk.NORMAL, text="Send")
                    else:
                        self.send_button.config(state=tk.NORMAL, text="Send")
            
            if self.window:
                self.window.after(0, enable_button)
    
    def _add_user_message(self, message: str):
        """Add user message to chat"""
        self.chat_display.config(state=tk.NORMAL)
        
        # Timestamp
        timestamp = datetime.now(timezone.utc).strftime("%H:%M")
        
        # Add message
        self.chat_display.insert(tk.END, f"\n{self.profile.name} ({timestamp}):\n", "user")
        self.chat_display.insert(tk.END, f"{message}\n")
        
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)
    
    def _add_assistant_message(self, message: str):
        """Add assistant message to chat"""
        self.chat_display.config(state=tk.NORMAL)
        
        # Timestamp
        timestamp = datetime.now(timezone.utc).strftime("%H:%M")
        
        # Add message
        self.chat_display.insert(tk.END, f"\nSnflwr ({timestamp}):\n", "assistant")
        self.chat_display.insert(tk.END, f"{message}\n")
        
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)
    
    def _add_system_message(self, message: str):
        """Add system message to chat"""
        self.chat_display.config(state=tk.NORMAL)
        
        # Add message
        self.chat_display.insert(tk.END, f"\n{message}\n", "system")
        
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)
    
    def _select_subject(self, subject_id: str):
        """Select subject area"""
        self.current_subject = subject_id
        
        # Update button states
        for sid, btn in self.subject_buttons.items():
            if sid == subject_id:
                if ctk:
                    btn.configure(fg_color="#007AFF")
                else:
                    btn.config(bg="#007AFF", fg="white")
            else:
                if ctk:
                    btn.configure(fg_color="#3B8ED0")
                else:
                    btn.config(bg="#f0f0f0", fg="black")
        
        # Add system message
        subject_names = {
            'general': 'General Studies',
            'math': 'Mathematics',
            'science': 'Science',
            'technology': 'Technology',
            'engineering': 'Engineering'
        }
        
        subject_name = subject_names.get(subject_id, 'General')
        self._add_system_message(f"Switched to {subject_name}. What would you like to learn?")
        
        logger.info(f"Subject changed to: {subject_id}")
    
    def _show_help(self):
        """Show help dialog"""
        help_text = (
            "How to Use snflwr.ai\n\n"
            "1. Type your question in the box at the bottom\n"
            "2. Click 'Send' or press Enter\n"
            "3. Wait for Snflwr to respond\n"
            "4. Continue the conversation!\n\n"
            "Tips:\n"
            "- Ask clear, specific questions\n"
            "- Use the subject buttons to focus on a topic\n"
            "- You can ask follow-up questions\n"
            "- Ask Snflwr to explain things differently if needed\n\n"
            "If you need help from your parents, click the 'Exit' button."
        )
        
        messagebox.showinfo("Help", help_text)
    
    def _on_window_close(self):
        """Handle window close"""
        try:
            # End session
            if self.session_id:
                session_manager.end_session(self.session_id)
            
            # Stop monitoring
            safety_monitor.stop_monitoring(self.profile.profile_id)
            
            # Close window
            self.window.destroy()
            
        except Exception as e:
            logger.error(f"Error closing student interface: {e}")
        
        if self.on_close:
            self.on_close()


# Export public interface
__all__ = ['StudentInterface']
