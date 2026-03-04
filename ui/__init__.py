"""
snflwr.ai User Interface Module
GUI components for family-friendly interaction
"""

from .launcher import LauncherWindow
from .setup_wizard import SetupWizard
from .student_interface import StudentInterface
from .parent_dashboard import ParentDashboard

__all__ = [
    'LauncherWindow',
    'SetupWizard',
    'StudentInterface',
    'ParentDashboard',
]
