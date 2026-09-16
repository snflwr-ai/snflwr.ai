"""Login must not reveal whether an account exists.

Live 2026-09-16: an unknown email got {"detail": "User not found"} while a real
account with a wrong password got "Invalid username or password", so anyone could
test whether a parent's email was registered. The unknown branch also skipped the
Argon2 verify, so equalizing only the message would still leak through timing.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from argon2 import PasswordHasher


@pytest.fixture
def auth_manager():
    from core.authentication import AuthenticationManager

    return AuthenticationManager(MagicMock())


def _row(ph, password="correct-horse-battery"):
    return [
        {
            "parent_id": "p1",
            "password_hash": ph.hash(password),
            "failed_login_attempts": 0,
            "account_locked_until": None,
            "is_active": 1,
        }
    ]


def test_unknown_and_wrong_password_are_indistinguishable(auth_manager):
    with patch.object(auth_manager.db, "execute_query", return_value=[]):
        unknown = auth_manager.authenticate_parent("nobody@example.org", "guess-1")
    with (
        patch.object(
            auth_manager.db, "execute_query", return_value=_row(auth_manager.ph)
        ),
        patch.object(auth_manager.db, "execute_write", return_value=None),
    ):
        wrong = auth_manager.authenticate_parent("parent@example.org", "guess-1")
    assert unknown == wrong == (False, "Invalid username or password")


def test_unknown_account_still_pays_for_an_argon2_verify(auth_manager):
    real = auth_manager.ph
    spy = MagicMock(wraps=real)
    auth_manager.ph = spy
    with patch.object(auth_manager.db, "execute_query", return_value=[]):
        auth_manager.authenticate_parent("nobody@example.org", "guess-1")
    assert spy.verify.call_count == 1


def test_unknown_account_is_not_measurably_faster(auth_manager):
    """Coarse guard, not a side-channel proof: the miss must cost a real verify
    (tens of ms with default Argon2), not return in microseconds."""
    auth_manager._timing_dummy_hash()  # warm the one-time hash
    with patch.object(auth_manager.db, "execute_query", return_value=[]):
        t = time.perf_counter()
        for _ in range(3):
            auth_manager.authenticate_parent("nobody@example.org", "guess-1")
        miss = (time.perf_counter() - t) / 3
    t = time.perf_counter()
    for _ in range(3):
        try:
            PasswordHasher().verify(auth_manager._timing_dummy_hash(), "guess-1")
        except Exception:
            pass
    one_verify = (time.perf_counter() - t) / 3
    assert miss >= 0.5 * one_verify
