"""
UI Smoke Tests — verify tkinter UI logic without a display

Tests the setup wizard step flow, validation, skip-child-profile,
parent dashboard tab switching, and launcher detection routing.
All tkinter widgets are mocked since no display server is available.
"""

import sys
import types
from unittest.mock import MagicMock, patch, call
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# Mock tkinter before any UI module is imported
# ---------------------------------------------------------------------------

def _build_tkinter_mock():
    """Create a tkinter mock that satisfies all UI module imports."""
    tk = types.ModuleType('tkinter')

    # Constants used by UI code (extracted from all ui/*.py files)
    for const in (
        'X', 'Y', 'BOTH', 'LEFT', 'RIGHT', 'TOP', 'BOTTOM',
        'W', 'E', 'N', 'S', 'B', 'DISABLED', 'NORMAL', 'FLAT',
        'WORD', 'END', 'INSERT', 'CENTER', 'NW', 'NSEW',
        'RIDGE', 'VERTICAL',
    ):
        setattr(tk, const, const)

    # Widget classes
    for widget in ('Tk', 'Toplevel', 'Frame', 'Label', 'Button', 'Entry',
                    'Spinbox', 'Canvas', 'Scrollbar', 'Text', 'StringVar',
                    'IntVar', 'BooleanVar', 'DoubleVar'):
        setattr(tk, widget, MagicMock())

    return tk


# Only inject mocks if tkinter is not natively available
if 'tkinter' not in sys.modules:
    _tk_mock = _build_tkinter_mock()
    sys.modules['tkinter'] = _tk_mock
    sys.modules['tkinter.messagebox'] = MagicMock()
    sys.modules['tkinter.scrolledtext'] = MagicMock()
    sys.modules['tkinter.ttk'] = MagicMock()
    sys.modules['tkinter.simpledialog'] = MagicMock()

# Ensure customtkinter is not found (use plain tkinter branch)
sys.modules.setdefault('customtkinter', None)


# ---------------------------------------------------------------------------
# Now import UI modules (they will get the mocked tkinter)
# ---------------------------------------------------------------------------

from ui.setup_wizard import SetupWizard
from ui.parent_dashboard import ParentDashboard
from ui.launcher import LauncherWindow


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def mock_root():
    """Fake Tk root window."""
    root = MagicMock()
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    return root


@pytest.fixture
def wizard(mock_root):
    """SetupWizard with mocked tkinter, ready for step-flow tests."""
    wiz = SetupWizard(
        parent_window=mock_root,
        cdrom_path=Path("/mnt/cdrom"),
        usb_path=Path("/mnt/usb"),
    )
    # Simulate show() having created the window + UI widgets
    wiz.window = MagicMock()
    wiz.window.winfo_screenwidth.return_value = 1920
    wiz.window.winfo_screenheight.return_value = 1080
    wiz.step_label = MagicMock()
    wiz.content_frame = MagicMock()
    wiz.content_frame.winfo_children.return_value = []
    wiz.back_button = MagicMock()
    wiz.next_button = MagicMock()
    return wiz


@pytest.fixture
def dashboard(mock_root):
    """ParentDashboard with mocked dependencies."""
    session_data = {
        'parent_id': 'parent_001',
        'session_token': 'tok_abc',
        'expires_at': '2026-02-22T00:00:00',
        'username': 'testparent',
    }
    with patch('ui.parent_dashboard.ProfileManager') as MockPM, \
         patch('ui.parent_dashboard.auth_manager'), \
         patch('ui.parent_dashboard.incident_logger'), \
         patch('ui.parent_dashboard.db_manager'):
        MockPM.return_value.get_profiles_by_parent.return_value = []
        dash = ParentDashboard(mock_root, session_data)
        dash.window = MagicMock()
        dash.window.winfo_screenwidth.return_value = 1920
        dash.window.winfo_screenheight.return_value = 1080
        dash.content_frame = MagicMock()
        dash.content_frame.winfo_children.return_value = []
        dash.tab_buttons = {}
        yield dash


# ===========================================================================
# Setup Wizard — Step Flow
# ===========================================================================

class TestSetupWizardStepFlow:
    """Test wizard navigates through steps correctly."""

    def test_initial_step_is_zero(self, wizard):
        assert wizard.current_step == 0

    def test_total_steps_is_four(self, wizard):
        assert wizard.total_steps == 4

    def test_show_step_updates_current_step(self, wizard):
        wizard._show_step(2)
        assert wizard.current_step == 2

    def test_step_label_updated_on_navigation(self, wizard):
        wizard._show_step(1)
        wizard.step_label.config.assert_called_with(text="Step 2 of 4")

    def test_back_button_disabled_on_step_zero(self, wizard):
        wizard._show_step(0)
        wizard.back_button.config.assert_called_with(state='DISABLED')

    def test_back_button_enabled_on_later_steps(self, wizard):
        wizard._show_step(1)
        wizard.back_button.config.assert_called_with(state='NORMAL')

    def test_next_button_hidden_on_completion_step(self, wizard):
        wizard._show_step(3)
        wizard.next_button.pack_forget.assert_called()

    def test_content_frame_cleared_on_step_change(self, wizard):
        wizard._show_step(1)
        wizard.content_frame.winfo_children.assert_called()

    def test_on_back_decrements_step(self, wizard):
        wizard._show_step(2)
        wizard.child_name_entry = MagicMock()
        wizard.child_name_entry.get.return_value = ""
        wizard._on_back()
        assert wizard.current_step == 1

    def test_on_back_does_nothing_on_step_zero(self, wizard):
        wizard._show_step(0)
        wizard._on_back()
        assert wizard.current_step == 0


# ===========================================================================
# Setup Wizard — Validation
# ===========================================================================

class TestSetupWizardValidation:
    """Test form validation on each step."""

    def _setup_parent_form(self, wizard, username="testuser",
                           password="SecurePass1!", confirm="SecurePass1!",
                           email=""):
        wizard.current_step = 1
        wizard.username_entry = MagicMock()
        wizard.username_entry.get.return_value = username
        wizard.password_entry = MagicMock()
        wizard.password_entry.get.return_value = password
        wizard.confirm_entry = MagicMock()
        wizard.confirm_entry.get.return_value = confirm
        wizard.email_entry = MagicMock()
        wizard.email_entry.get.return_value = email

    @patch('ui.setup_wizard.messagebox')
    def test_valid_parent_account(self, mock_mb, wizard):
        self._setup_parent_form(wizard)
        assert wizard._validate_current_step() is True

    @patch('ui.setup_wizard.messagebox')
    def test_empty_username_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, username="")
        assert wizard._validate_current_step() is False
        mock_mb.showwarning.assert_called()

    @patch('ui.setup_wizard.messagebox')
    def test_short_username_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, username="ab")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_short_password_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, password="Short1", confirm="Short1")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_password_mismatch_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, password="SecurePass1", confirm="Different1")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_password_missing_uppercase_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, password="alllower1", confirm="alllower1")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_password_missing_lowercase_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, password="ALLUPPER1", confirm="ALLUPPER1")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_password_missing_digit_rejected(self, mock_mb, wizard):
        self._setup_parent_form(wizard, password="NoDigitHere", confirm="NoDigitHere")
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_child_profile_required_when_not_skipping(self, mock_mb, wizard):
        wizard.current_step = 2
        wizard.child_profiles = []
        wizard.child_name_entry = MagicMock()
        wizard.child_name_entry.get.return_value = ""
        assert wizard._validate_current_step() is False

    @patch('ui.setup_wizard.messagebox')
    def test_child_profile_accepted_when_profiles_exist(self, mock_mb, wizard):
        wizard.current_step = 2
        wizard.child_profiles = [{"name": "Emma", "age": 10, "grade": "5th"}]
        wizard.child_name_entry = MagicMock()
        wizard.child_name_entry.get.return_value = ""
        assert wizard._validate_current_step() is True

    @patch('ui.setup_wizard.messagebox')
    def test_child_profile_accepted_when_form_has_name(self, mock_mb, wizard):
        wizard.current_step = 2
        wizard.child_profiles = []
        wizard.child_name_entry = MagicMock()
        wizard.child_name_entry.get.return_value = "Emma"
        assert wizard._validate_current_step() is True

    def test_welcome_step_always_valid(self, wizard):
        wizard.current_step = 0
        assert wizard._validate_current_step() is True


# ===========================================================================
# Setup Wizard — Skip Child Profile
# ===========================================================================

class TestSkipChildProfile:
    """Test the skip-child-profile flow."""

    def test_skip_flag_defaults_to_false(self, wizard):
        assert wizard.skip_child_profile is False

    def test_skip_sets_flag(self, wizard):
        wizard._skip_child_profile()
        assert wizard.skip_child_profile is True

    def test_skip_clears_child_profiles(self, wizard):
        wizard.child_profiles = [{"name": "Emma", "age": 10, "grade": "5th"}]
        wizard._skip_child_profile()
        assert wizard.child_profiles == []

    def test_skip_jumps_to_completion_step(self, wizard):
        wizard._skip_child_profile()
        assert wizard.current_step == 3

    def test_next_on_step2_resets_skip_flag(self, wizard):
        """If user goes back to step 2 and has profiles, skip is cleared."""
        wizard.skip_child_profile = True
        wizard.current_step = 2
        wizard.child_profiles = [{"name": "Emma", "age": 10, "grade": "5th"}]
        wizard.child_name_entry = MagicMock()
        wizard.child_name_entry.get.return_value = ""
        wizard._on_next()
        assert wizard.skip_child_profile is False

    @patch('ui.setup_wizard.auth_manager')
    def test_create_account_skips_child_profile(self, mock_auth, wizard):
        """When skip flag is set, only parent account is created."""
        wizard.skip_child_profile = True
        wizard.parent_username = "testuser"
        wizard.parent_password = "SecurePass1"
        wizard.parent_email = ""
        wizard.progress_label = MagicMock()

        mock_auth.create_parent_account.return_value = (True, "parent_001")

        wizard._create_account()

        mock_auth.create_parent_account.assert_called_once()
        mock_auth.authenticate_parent.assert_not_called()

    @patch('ui.setup_wizard.ProfileManager')
    @patch('ui.setup_wizard.auth_manager')
    def test_create_account_creates_child_when_not_skipped(
        self, mock_auth, mock_pm_cls, wizard
    ):
        """When skip flag is not set, both parent and child are created."""
        wizard.skip_child_profile = False
        wizard.parent_username = "testuser"
        wizard.parent_password = "SecurePass1!"
        wizard.parent_email = ""
        wizard.child_profiles = [{"name": "Emma", "age": 10, "grade": "5th"}]
        wizard.progress_label = MagicMock()

        mock_auth.create_parent_account.return_value = (True, "parent_001")
        mock_auth.authenticate_parent.return_value = (True, {"parent_id": "parent_001", "session_token": "tok"})
        mock_pm_cls.return_value.create_profile.return_value = MagicMock(profile_id="child_001")

        with patch.object(wizard, '_get_owui_admin_token', return_value=""):
            with patch.object(wizard, '_create_owui_student_account', return_value=None):
                wizard._create_account()

        mock_auth.create_parent_account.assert_called_once()
        mock_auth.authenticate_parent.assert_called_once()
        mock_pm_cls.return_value.create_profile.assert_called_once_with(
            parent_id="parent_001", name="Emma", age=10, grade="5th"
        )


# ===========================================================================
# Setup Wizard — Save/Restore Data
# ===========================================================================

class TestSetupWizardDataPersistence:
    """Test that form data is saved when navigating between steps."""

    def test_save_parent_data(self, wizard):
        wizard.current_step = 1
        wizard.username_entry = MagicMock()
        wizard.username_entry.get.return_value = "myuser"
        wizard.password_entry = MagicMock()
        wizard.password_entry.get.return_value = "MyPass123"
        wizard.email_entry = MagicMock()
        wizard.email_entry.get.return_value = "me@test.com"

        wizard._save_current_step_data()

        assert wizard.parent_username == "myuser"
        assert wizard.parent_password == "MyPass123"
        assert wizard.parent_email == "me@test.com"

    def test_save_child_data(self, wizard):
        """Step 2 save is a no-op — profiles are added via _add_child_to_list."""
        wizard.current_step = 2
        wizard._save_current_step_data()
        # No error raised; profiles list unchanged
        assert wizard.child_profiles == []

    def test_save_on_unrelated_step_is_noop(self, wizard):
        wizard.current_step = 0
        wizard.parent_username = "original"
        wizard._save_current_step_data()
        assert wizard.parent_username == "original"


# ===========================================================================
# Setup Wizard — Cancel Flow
# ===========================================================================

class TestSetupWizardCancel:
    """Test cancel/close behavior."""

    @patch('ui.setup_wizard.messagebox')
    def test_cancel_on_step_zero_skips_confirmation(self, mock_mb, wizard):
        wizard.current_step = 0
        callback = MagicMock()
        wizard.on_complete = callback

        wizard._on_cancel()

        mock_mb.askyesno.assert_not_called()
        callback.assert_called_once_with(False)

    @patch('ui.setup_wizard.messagebox')
    def test_cancel_on_later_step_shows_confirmation(self, mock_mb, wizard):
        wizard.current_step = 2
        mock_mb.askyesno.return_value = True
        callback = MagicMock()
        wizard.on_complete = callback

        wizard._on_cancel()

        mock_mb.askyesno.assert_called_once()
        callback.assert_called_once_with(False)

    @patch('ui.setup_wizard.messagebox')
    def test_cancel_denied_keeps_wizard_open(self, mock_mb, wizard):
        wizard.current_step = 1
        mock_mb.askyesno.return_value = False
        callback = MagicMock()
        wizard.on_complete = callback

        wizard._on_cancel()

        callback.assert_not_called()


# ===========================================================================
# Parent Dashboard — Tab Navigation
# ===========================================================================

class TestDashboardTabs:
    """Test dashboard tab switching."""

    def test_session_data_preserved(self, dashboard):
        assert dashboard.session_data['username'] == 'testparent'
        assert dashboard.session_data['parent_id'] == 'parent_001'

    def test_show_tab_updates_current(self, dashboard):
        dashboard._show_tab("profiles")
        assert dashboard.current_tab == "profiles"

    def test_show_tab_clears_content(self, dashboard):
        dashboard._show_tab("overview")
        dashboard.content_frame.winfo_children.assert_called()

    @pytest.mark.parametrize("tab", ["overview", "profiles", "safety", "analytics", "settings"])
    def test_all_tabs_render_without_error(self, dashboard, tab):
        """Each tab renders without raising."""
        dashboard._show_tab(tab)
        assert dashboard.current_tab == tab


# ===========================================================================
# Launcher — Initialization and State
# ===========================================================================

class TestLauncherInit:
    """Test launcher initializes with correct defaults."""

    def test_partitions_not_detected(self):
        launcher = LauncherWindow()
        assert launcher.partitions_detected is False

    def test_no_existing_account(self):
        launcher = LauncherWindow()
        assert launcher.has_existing_account is False

    def test_paths_are_none(self):
        launcher = LauncherWindow()
        assert launcher.cdrom_path is None
        assert launcher.data_path is None

    def test_window_dimensions(self):
        launcher = LauncherWindow()
        assert launcher.window_width == 800
        assert launcher.window_height == 600

    def test_login_form_widgets_initially_none(self):
        launcher = LauncherWindow()
        assert launcher.login_frame is None
        assert launcher.username_entry is None
        assert launcher.password_entry is None
        assert launcher.login_error_label is None
        assert launcher.sign_in_button is None

    def test_session_data_initially_none(self):
        launcher = LauncherWindow()
        assert launcher.session_data is None


# ===========================================================================
# Launcher — Inline Login Flow
# ===========================================================================

class TestLauncherLoginFlow:
    """Test inline login form lifecycle on the launcher."""

    @pytest.fixture
    def launcher(self):
        lw = LauncherWindow()
        lw.root = MagicMock()
        lw.status_label = MagicMock()
        lw.status_label.master = MagicMock()
        lw.progress_label = MagicMock()
        lw.action_button = MagicMock()
        return lw

    def test_show_login_ready_hides_action_button(self, launcher):
        """_show_login_ready hides the generic action button."""
        # _show_login_ready schedules via root.after — call the callback directly
        launcher.root.after = lambda _, fn: fn()
        launcher._show_login_ready()
        launcher.action_button.pack_forget.assert_called()

    def test_build_login_form_creates_widgets(self, launcher):
        """_build_login_form populates login widget attributes."""
        launcher._build_login_form()
        assert launcher.login_frame is not None
        assert launcher.username_entry is not None
        assert launcher.password_entry is not None
        assert launcher.login_error_label is not None
        assert launcher.sign_in_button is not None

    def test_build_login_form_is_idempotent(self, launcher):
        """Calling _build_login_form twice doesn't create duplicate frames."""
        launcher._build_login_form()
        first_frame = launcher.login_frame
        launcher._build_login_form()
        assert launcher.login_frame is first_frame

    def test_hide_login_form_clears_widgets(self, launcher):
        """_hide_login_form tears down all login widgets."""
        launcher._build_login_form()
        launcher._hide_login_form()
        assert launcher.login_frame is None
        assert launcher.username_entry is None
        assert launcher.password_entry is None
        assert launcher.login_error_label is None
        assert launcher.sign_in_button is None

    def test_hide_login_form_noop_when_no_form(self, launcher):
        """_hide_login_form does nothing if login form was never built."""
        launcher._hide_login_form()  # should not raise
        assert launcher.login_frame is None

    def _setup_login_mocks(self, launcher, username="admin", password="SecurePass1"):
        """Set up distinct mock entries for login form (avoids shared-mock issue)."""
        launcher.login_frame = MagicMock()
        launcher.username_entry = MagicMock()
        launcher.username_entry.get.return_value = username
        launcher.password_entry = MagicMock()
        launcher.password_entry.get.return_value = password
        launcher.login_error_label = MagicMock()
        launcher.sign_in_button = MagicMock()

    @patch('storage.database.db_manager')
    @patch('ui.launcher.auth_manager')
    def test_successful_login_stores_session_and_transitions(
        self, mock_auth, mock_db, launcher
    ):
        """Successful auth stores session_data and shows Start Snflwr."""
        mock_db.execute_query.return_value = [{'count': 1}]
        self._setup_login_mocks(launcher, "admin", "SecurePass1")

        mock_auth.authenticate_parent.return_value = (
            True, {"parent_id": "p1", "session_token": "tok"}
        )

        launcher._attempt_login()

        assert launcher.session_data == {
            "parent_id": "p1", "session_token": "tok", "username": "admin"
        }
        # Login form should be torn down
        assert launcher.login_frame is None
        # Action button should be re-shown as "Start Snflwr"
        launcher.action_button.config.assert_called_with(
            text="Start Snflwr"
        )

    @patch('storage.database.db_manager')
    @patch('ui.launcher.auth_manager')
    def test_failed_login_shows_error_inline(self, mock_auth, mock_db, launcher):
        """Failed auth shows error in the inline form, not a popup."""
        mock_db.execute_query.return_value = [{'count': 1}]
        self._setup_login_mocks(launcher, "admin", "wrong")

        mock_auth.authenticate_parent.return_value = (
            False, "Invalid username or password"
        )

        launcher._attempt_login()

        assert launcher.session_data is None
        launcher.login_error_label.config.assert_called_with(
            text="Invalid username or password"
        )
        # Password field should be cleared
        launcher.password_entry.delete.assert_called_with(0, 'END')

    @patch('storage.database.db_manager')
    @patch('ui.launcher.auth_manager')
    def test_empty_fields_rejected(self, mock_auth, mock_db, launcher):
        """Empty username/password shows inline error, no auth call."""
        mock_db.execute_query.return_value = [{'count': 1}]
        self._setup_login_mocks(launcher, "", "")

        launcher._attempt_login()

        mock_auth.authenticate_parent.assert_not_called()
        launcher.login_error_label.config.assert_called_with(
            text="Please enter both username and password."
        )

    @patch('storage.database.db_manager')
    @patch('ui.launcher.auth_manager')
    def test_session_data_enriched_with_username(self, mock_auth, mock_db, launcher):
        """Successful auth enriches session_data with username for dashboard."""
        mock_db.execute_query.return_value = [{'count': 1}]
        self._setup_login_mocks(launcher, "myadmin", "SecurePass1")

        mock_auth.authenticate_parent.return_value = (
            True, {"parent_id": "p1", "session_token": "tok"}
        )

        launcher._attempt_login()

        assert launcher.session_data['username'] == "myadmin"
        assert launcher.session_data['parent_id'] == "p1"

    def test_show_authenticated_ready_packs_start_button(self, launcher):
        """After auth, the action button shows 'Start Snflwr'."""
        launcher._build_login_form()
        launcher.session_data = {"parent_id": "p1"}
        launcher._show_authenticated_ready()
        launcher.action_button.config.assert_called_with(
            text="Start Snflwr"
        )
        launcher.action_button.pack.assert_called()


# ===========================================================================
# Dashboard — Query Scoping
# ===========================================================================

class TestDashboardQueryScoping:
    """Verify dashboard queries are scoped to the logged-in parent's children."""

    @pytest.fixture
    def scoped_dashboard(self, mock_root):
        session_data = {
            'parent_id': 'parent_001',
            'session_token': 'tok_abc',
            'expires_at': '2026-02-22T00:00:00',
            'username': 'testparent',
        }
        with patch('ui.parent_dashboard.ProfileManager') as MockPM, \
             patch('ui.parent_dashboard.auth_manager'), \
             patch('ui.parent_dashboard.incident_logger'), \
             patch('ui.parent_dashboard.db_manager') as mock_db:
            MockPM.return_value.get_profiles_by_parent.return_value = []
            dash = ParentDashboard(mock_root, session_data)
            dash.window = MagicMock()
            dash.content_frame = MagicMock()
            dash.content_frame.winfo_children.return_value = []
            dash.tab_buttons = {}
            # Give the dashboard some child profiles
            prof_a = MagicMock(profile_id="child_a")
            prof_b = MagicMock(profile_id="child_b")
            dash.profiles = [prof_a, prof_b]
            yield dash, mock_db

    def test_get_profile_ids_returns_children_only(self, scoped_dashboard):
        dash, _ = scoped_dashboard
        assert dash._get_profile_ids() == ["child_a", "child_b"]

    def test_recent_sessions_query_includes_profile_filter(self, scoped_dashboard):
        """_get_recent_sessions passes profile_ids into the WHERE clause."""
        dash, mock_db = scoped_dashboard
        mock_db.execute_query.return_value = []
        dash._get_recent_sessions(5)
        assert mock_db.execute_query.call_args is not None, "execute_query was not called"
        sql = mock_db.execute_query.call_args[0][0]
        assert "profile_id IN" in sql

    def test_active_profiles_today_includes_profile_filter(self, scoped_dashboard):
        dash, mock_db = scoped_dashboard
        mock_db.execute_query.return_value = [{'count': 1}]
        dash._get_active_profiles_today()
        assert mock_db.execute_query.call_args is not None, "execute_query was not called"
        sql = mock_db.execute_query.call_args[0][0]
        assert "profile_id IN" in sql

    def test_total_sessions_today_includes_profile_filter(self, scoped_dashboard):
        dash, mock_db = scoped_dashboard
        mock_db.execute_query.return_value = [{'count': 2}]
        dash._get_total_sessions_today()
        assert mock_db.execute_query.call_args is not None, "execute_query was not called"
        sql = mock_db.execute_query.call_args[0][0]
        assert "profile_id IN" in sql

    def test_pending_incidents_includes_profile_filter(self, scoped_dashboard):
        dash, mock_db = scoped_dashboard
        mock_db.execute_query.return_value = [{'count': 0}]
        dash._get_pending_incidents()
        assert mock_db.execute_query.call_args is not None, "execute_query was not called"
        sql = mock_db.execute_query.call_args[0][0]
        assert "profile_id IN" in sql

    def test_no_profiles_returns_empty(self, scoped_dashboard):
        """If parent has no children, queries return zero without hitting DB."""
        dash, mock_db = scoped_dashboard
        dash.profiles = []
        assert dash._get_recent_sessions(5) == []
        assert dash._get_active_profiles_today() == 0
        assert dash._get_total_sessions_today() == 0
        assert dash._get_pending_incidents() == 0
        mock_db.execute_query.assert_not_called()

    def test_paginated_sessions_uses_offset(self, scoped_dashboard):
        """_get_paginated_sessions passes LIMIT and OFFSET params."""
        dash, mock_db = scoped_dashboard
        mock_db.execute_query.return_value = []
        dash._get_paginated_sessions(20, 40)
        sql = mock_db.execute_query.call_args[0][0]
        assert "LIMIT" in sql
        assert "OFFSET" in sql
        params = mock_db.execute_query.call_args[0][1]
        # last two params are limit and offset
        assert params[-2:] == (20, 40)

    def test_paginated_sessions_no_profiles(self, scoped_dashboard):
        """Pagination returns empty without DB call when no profiles."""
        dash, mock_db = scoped_dashboard
        dash.profiles = []
        assert dash._get_paginated_sessions(20, 0) == []
        mock_db.execute_query.assert_not_called()


# ===========================================================================
# PostgreSQL Placeholder Translation
# ===========================================================================

class TestPlaceholderTranslation:
    """Verify PostgreSQL adapter translates ? placeholders correctly."""

    @staticmethod
    def _translate(query):
        """Import and call the static translator."""
        try:
            from storage.db_adapters import PostgreSQLAdapter
            return PostgreSQLAdapter._translate_placeholders(query)
        except ImportError:
            pytest.skip("psycopg2 not installed")

    def test_simple_replacement(self):
        assert self._translate("SELECT * FROM t WHERE id = ?") == \
            "SELECT * FROM t WHERE id = %s"

    def test_multiple_placeholders(self):
        assert self._translate("WHERE a = ? AND b = ?") == \
            "WHERE a = %s AND b = %s"

    def test_preserves_question_mark_in_string_literal(self):
        sql = "WHERE name LIKE '%?%' AND id = ?"
        assert self._translate(sql) == "WHERE name LIKE '%?%' AND id = %s"

    def test_no_placeholders(self):
        sql = "SELECT 1"
        assert self._translate(sql) == "SELECT 1"

    def test_adjacent_quoted_and_unquoted(self):
        sql = "WHERE a = '?' AND b = ?"
        assert self._translate(sql) == "WHERE a = '?' AND b = %s"


# ===========================================================================
# Setup Wizard — Orphaned Session Cleanup
# ===========================================================================

class TestWizardSessionCleanup:
    """Verify setup wizard invalidates the verification session token."""

    @patch('ui.setup_wizard.auth_manager')
    @patch('ui.setup_wizard.messagebox')
    def test_wizard_logs_out_verification_token(
        self, mock_msgbox, mock_auth, mock_root
    ):
        """_create_account logs out the session used for credential verification."""
        from ui.setup_wizard import SetupWizard

        with patch('ui.setup_wizard.ProfileManager') as MockPM:
            wiz = SetupWizard(mock_root, Path("/mnt/cdrom"), Path("/mnt/usb"))
            wiz.window = MagicMock()
            wiz.progress_bar = MagicMock()
            wiz.progress_label = MagicMock()

            # Stage: account already created, now creating child profile
            wiz.parent_username = "admin"
            wiz.parent_password = "Pass1234!"
            wiz.parent_email = ""
            wiz.child_profiles = [{"name": "Kiddo", "age": 10, "grade": "5th"}]
            wiz.skip_child_profile = False

            # Mock account creation as already done (jump to child profile path)
            mock_auth.create_parent_account.return_value = (
                True, "parent_001"
            )
            mock_auth.authenticate_parent.return_value = (
                True, {"parent_id": "parent_001", "session_token": "tok_wizard"}
            )
            MockPM.return_value.create_profile.return_value = MagicMock(profile_id="child_001")

            with patch.object(wiz, '_get_owui_admin_token', return_value=""):
                with patch.object(wiz, '_create_owui_student_account', return_value=None):
                    wiz._create_account()

            # The wizard should log out the verification token
            mock_auth.logout.assert_called_once_with("tok_wizard")
