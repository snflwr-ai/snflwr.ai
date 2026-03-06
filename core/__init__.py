"""Minimal `core` package stubs for running tests in CI/dev container."""

from .authentication import AuthenticationManager
from .profile_manager import ProfileManager
from .session_manager import SessionManager

__all__ = [
    "AuthenticationManager",
    "ProfileManager",
    "SessionManager",
]
