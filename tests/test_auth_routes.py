"""
Tests for api/routes/auth.py — Authentication Routes

Coverage targets (lines 46-47, 113-125, 163-170, 184, 191-198, 209-238, 249-271, 285-351, 368-398):
    - Login: success, invalid credentials, AccountLockedError, DB error, rate limiting
    - Register: success, password mismatch, duplicate email, AuthenticationError, DB error
    - Logout: success, logout failure, DB error
    - Validate session: valid session, invalid session, non-owner denied, admin bypass
    - Verify email: success, invalid token, DB error
    - Forgot password: success (token + email sent), email not found, DB error
    - Reset password: success, password mismatch, invalid token, DB error
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.authentication import (
    AuthSession,
    AuthenticationError,
    InvalidCredentialsError,
    AccountLockedError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parent_session():
    return AuthSession(
        user_id="test-parent-id",
        role="parent",
        session_token="tok_parent",
        email="parent@test.com",
        created_at="2024-01-01T00:00:00",
    )


@pytest.fixture
def admin_session():
    return AuthSession(
        user_id="test-admin-id",
        role="admin",
        session_token="tok_admin",
        email="admin@test.com",
        created_at="2024-01-01T00:00:00",
    )


@pytest.fixture
def other_parent_session():
    return AuthSession(
        user_id="other-parent-id",
        role="parent",
        session_token="tok_other",
        email="other@test.com",
        created_at="2024-01-01T00:00:00",
    )


@pytest.fixture
def mock_deps():
    """Patch all external dependencies used by auth routes."""
    with patch("api.routes.auth.auth_manager") as auth_mgr, \
         patch("api.routes.auth.rate_limiter") as rl, \
         patch("api.routes.auth.set_csrf_cookie") as csrf, \
         patch("api.routes.auth.audit_log") as audit, \
         patch("api.routes.auth.email_service") as email_svc, \
         patch("api.routes.auth.get_email_crypto") as email_crypto:
        rl.check_rate_limit.return_value = (True, {"remaining": 4})
        csrf.return_value = "csrf-token-123"
        yield {
            "auth_manager": auth_mgr,
            "rate_limiter": rl,
            "set_csrf_cookie": csrf,
            "audit_log": audit,
            "email_service": email_svc,
            "get_email_crypto": email_crypto,
        }


def _make_rate_limit_info():
    """Return a valid rate limit info dict (simulates the dependency output)."""
    return {"remaining": 4, "reset_time": 0}


def _make_response():
    """Create a mock FastAPI Response for endpoints that set cookies."""
    return MagicMock()


# ============================================================================
# LOGIN
# ============================================================================

class TestLogin:

    def test_login_success(self, mock_deps):
        """Successful login returns session, token, and CSRF token."""
        from api.routes.auth import login, LoginRequest

        session_data = {
            "parent_id": "user-123",
            "session_token": "session-tok-abc",
            "expires_at": "2024-12-31T23:59:59",
        }
        mock_deps["auth_manager"].authenticate_parent.return_value = (True, session_data)

        request = LoginRequest(email="user@example.com", password="GoodP@ss1")
        response = _make_response()

        result = login(request, response, rate_limit_info=_make_rate_limit_info())

        assert result["session"] == session_data
        assert result["token"] == "session-tok-abc"
        assert result["csrf_token"] == "csrf-token-123"
        mock_deps["auth_manager"].authenticate_parent.assert_called_once_with(
            "user@example.com", "GoodP@ss1"
        )

    def test_login_invalid_credentials_from_result(self, mock_deps):
        """When authenticate_parent returns (False, error_msg), 401 is raised."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.return_value = (
            False,
            "Invalid email or password",
        )

        request = LoginRequest(email="bad@example.com", password="wrong")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 401
        assert "Invalid email or password" in exc.value.detail

    def test_login_account_locked(self, mock_deps):
        """AccountLockedError maps to 401 (line 113-115)."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.side_effect = AccountLockedError(
            "Account locked after 5 failed attempts"
        )

        request = LoginRequest(email="locked@example.com", password="pass")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 401
        assert "locked" in exc.value.detail.lower()

    def test_login_invalid_credentials_exception(self, mock_deps):
        """InvalidCredentialsError maps to 401 (line 116-117)."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.side_effect = (
            InvalidCredentialsError("Bad credentials")
        )

        request = LoginRequest(email="user@example.com", password="wrong")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 401

    def test_login_authentication_error(self, mock_deps):
        """AuthenticationError maps to 401 (line 118-119)."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.side_effect = (
            AuthenticationError("Auth system error")
        )

        request = LoginRequest(email="user@example.com", password="pass")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 401
        assert "Auth system error" in exc.value.detail

    def test_login_db_error(self, mock_deps):
        """Database error maps to 503 (line 120-122)."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.side_effect = sqlite3.Error(
            "connection lost"
        )

        request = LoginRequest(email="user@example.com", password="pass")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 503
        assert "Service temporarily unavailable" in exc.value.detail

    def test_login_unexpected_error(self, mock_deps):
        """Unexpected exception maps to 500 (line 123-125)."""
        from api.routes.auth import login, LoginRequest

        mock_deps["auth_manager"].authenticate_parent.side_effect = RuntimeError(
            "something broke"
        )

        request = LoginRequest(email="user@example.com", password="pass")
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            login(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 500
        assert "Internal server error" in exc.value.detail


# ============================================================================
# RATE LIMITING
# ============================================================================

class TestRateLimiting:

    def test_rate_limit_exceeded(self, mock_deps):
        """Rate limit exceeded raises 429 (lines 46-47)."""
        from api.routes.auth import check_auth_rate_limit

        mock_deps["rate_limiter"].check_rate_limit.return_value = (
            False,
            {"retry_after": 42},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        with pytest.raises(HTTPException) as exc:
            check_auth_rate_limit(mock_request)
        assert exc.value.status_code == 429
        assert "42" in exc.value.detail

    def test_rate_limit_allowed(self, mock_deps):
        """Rate limit not exceeded returns info dict."""
        from api.routes.auth import check_auth_rate_limit

        mock_deps["rate_limiter"].check_rate_limit.return_value = (
            True,
            {"remaining": 3},
        )

        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.1"

        result = check_auth_rate_limit(mock_request)
        assert result == {"remaining": 3}

    def test_rate_limit_unknown_client(self, mock_deps):
        """Missing client info defaults to 'unknown' IP."""
        from api.routes.auth import check_auth_rate_limit

        mock_deps["rate_limiter"].check_rate_limit.return_value = (
            True,
            {"remaining": 5},
        )

        mock_request = MagicMock()
        mock_request.client = None

        result = check_auth_rate_limit(mock_request)
        mock_deps["rate_limiter"].check_rate_limit.assert_called_once_with(
            identifier="unknown",
            max_requests=5,
            window_seconds=60,
            limit_type="auth",
        )
        assert result == {"remaining": 5}


# ============================================================================
# REGISTER
# ============================================================================

class TestRegister:

    def test_register_success(self, mock_deps):
        """Successful registration returns user_id and CSRF token."""
        from api.routes.auth import register, RegisterRequest

        mock_deps["auth_manager"].create_parent_account.return_value = (
            True,
            "new-user-id-abc",
        )

        request = RegisterRequest(
            email="new@example.com",
            password="Str0ngP@ss!",
            verify_password="Str0ngP@ss!",
        )
        response = _make_response()

        result = register(request, response, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"
        assert result["user_id"] == "new-user-id-abc"
        assert result["csrf_token"] == "csrf-token-123"
        mock_deps["auth_manager"].create_parent_account.assert_called_once_with(
            username="new@example.com",
            password="Str0ngP@ss!",
            email="new@example.com",
        )

    def test_register_password_mismatch(self, mock_deps):
        """Mismatched passwords raise 400."""
        from api.routes.auth import register, RegisterRequest

        request = RegisterRequest(
            email="new@example.com",
            password="Pass1!",
            verify_password="Different2!",
        )
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            register(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Passwords do not match" in exc.value.detail

    def test_register_duplicate_email(self, mock_deps):
        """Duplicate email returns (False, error_msg) -> 400."""
        from api.routes.auth import register, RegisterRequest

        mock_deps["auth_manager"].create_parent_account.return_value = (
            False,
            "Email already registered",
        )

        request = RegisterRequest(
            email="exists@example.com",
            password="Str0ngP@ss!",
            verify_password="Str0ngP@ss!",
        )
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            register(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Email already registered" in exc.value.detail

    def test_register_authentication_error(self, mock_deps):
        """AuthenticationError during registration maps to 400 (line 163-164)."""
        from api.routes.auth import register, RegisterRequest

        mock_deps["auth_manager"].create_parent_account.side_effect = (
            AuthenticationError("Weak password")
        )

        request = RegisterRequest(
            email="new@example.com",
            password="weak",
            verify_password="weak",
        )
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            register(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Weak password" in exc.value.detail

    def test_register_db_error(self, mock_deps):
        """DB error during registration maps to 503 (line 165-167)."""
        from api.routes.auth import register, RegisterRequest

        mock_deps["auth_manager"].create_parent_account.side_effect = sqlite3.Error(
            "disk full"
        )

        request = RegisterRequest(
            email="new@example.com",
            password="Str0ngP@ss!",
            verify_password="Str0ngP@ss!",
        )
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            register(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 503

    def test_register_unexpected_error(self, mock_deps):
        """Unexpected error during registration maps to 500 (line 168-170)."""
        from api.routes.auth import register, RegisterRequest

        mock_deps["auth_manager"].create_parent_account.side_effect = RuntimeError(
            "boom"
        )

        request = RegisterRequest(
            email="new@example.com",
            password="Str0ngP@ss!",
            verify_password="Str0ngP@ss!",
        )
        response = _make_response()

        with pytest.raises(HTTPException) as exc:
            register(request, response, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 500


# ============================================================================
# LOGOUT
# ============================================================================

class TestLogout:

    def test_logout_success(self, parent_session, mock_deps):
        """Successful logout returns status success and logs audit event."""
        from api.routes.auth import logout

        mock_deps["auth_manager"].logout.return_value = True

        result = logout(session=parent_session)

        assert result["status"] == "success"
        mock_deps["auth_manager"].logout.assert_called_once_with("tok_parent")
        mock_deps["audit_log"].assert_called_once_with(
            "logout", "session", "tok_parent", parent_session
        )

    def test_logout_failure(self, parent_session, mock_deps):
        """When auth_manager.logout returns False, 400 is raised (line 184)."""
        from api.routes.auth import logout

        mock_deps["auth_manager"].logout.return_value = False

        with pytest.raises(HTTPException) as exc:
            logout(session=parent_session)
        assert exc.value.status_code == 400
        assert "Logout failed" in exc.value.detail

    def test_logout_db_error(self, parent_session, mock_deps):
        """DB error during logout maps to 503 (line 193-195)."""
        from api.routes.auth import logout

        mock_deps["auth_manager"].logout.side_effect = sqlite3.Error("db locked")

        with pytest.raises(HTTPException) as exc:
            logout(session=parent_session)
        assert exc.value.status_code == 503

    def test_logout_unexpected_error(self, parent_session, mock_deps):
        """Unexpected error during logout maps to 500 (line 196-198)."""
        from api.routes.auth import logout

        mock_deps["auth_manager"].logout.side_effect = RuntimeError("unexpected")

        with pytest.raises(HTTPException) as exc:
            logout(session=parent_session)
        assert exc.value.status_code == 500


# ============================================================================
# VALIDATE SESSION
# ============================================================================

class TestValidateSession:

    def test_validate_own_session(self, parent_session, mock_deps):
        """Owner validating own session succeeds (line 209-227)."""
        from api.routes.auth import validate_session

        validated_session = AuthSession(
            user_id="test-parent-id",
            role="parent",
            session_token="some-session-tok",
            email="parent@test.com",
        )
        mock_deps["auth_manager"].validate_session.return_value = (
            True,
            validated_session,
        )

        result = validate_session("some-session-tok", current_session=parent_session)

        assert result["valid"] is True
        assert result["session"]["session_id"] == "some-session-tok"
        assert result["session"]["role"] == "parent"

    def test_validate_invalid_session(self, parent_session, mock_deps):
        """Invalid session returns 401 (line 212-213)."""
        from api.routes.auth import validate_session

        mock_deps["auth_manager"].validate_session.return_value = (False, None)

        with pytest.raises(HTTPException) as exc:
            validate_session("bad-session-id", current_session=parent_session)
        assert exc.value.status_code == 401
        assert "Invalid or expired session" in exc.value.detail

    def test_validate_non_owner_denied(self, parent_session, mock_deps):
        """Non-owner, non-admin cannot validate another user's session (line 216-217)."""
        from api.routes.auth import validate_session

        other_user_session = AuthSession(
            user_id="different-user-id",
            role="parent",
            session_token="other-tok",
            email="other@test.com",
        )
        mock_deps["auth_manager"].validate_session.return_value = (
            True,
            other_user_session,
        )

        with pytest.raises(HTTPException) as exc:
            validate_session("other-tok", current_session=parent_session)
        assert exc.value.status_code == 403
        assert "Not authorized" in exc.value.detail

    def test_validate_admin_bypass(self, admin_session, mock_deps):
        """Admin can validate any user's session (line 216)."""
        from api.routes.auth import validate_session

        other_user_session = AuthSession(
            user_id="someone-else",
            role="parent",
            session_token="their-tok",
            email="them@test.com",
        )
        mock_deps["auth_manager"].validate_session.return_value = (
            True,
            other_user_session,
        )

        result = validate_session("their-tok", current_session=admin_session)

        assert result["valid"] is True
        assert result["session"]["session_id"] == "their-tok"
        assert result["session"]["role"] == "parent"

    def test_validate_authentication_error(self, parent_session, mock_deps):
        """AuthenticationError during validation maps to 401 (line 231-232)."""
        from api.routes.auth import validate_session

        mock_deps["auth_manager"].validate_session.side_effect = (
            AuthenticationError("Session corrupt")
        )

        with pytest.raises(HTTPException) as exc:
            validate_session("bad-tok", current_session=parent_session)
        assert exc.value.status_code == 401

    def test_validate_db_error(self, parent_session, mock_deps):
        """DB error during validation maps to 503 (line 233-235)."""
        from api.routes.auth import validate_session

        mock_deps["auth_manager"].validate_session.side_effect = sqlite3.Error(
            "timeout"
        )

        with pytest.raises(HTTPException) as exc:
            validate_session("tok", current_session=parent_session)
        assert exc.value.status_code == 503

    def test_validate_unexpected_error(self, parent_session, mock_deps):
        """Unexpected error during validation maps to 500 (line 236-238)."""
        from api.routes.auth import validate_session

        mock_deps["auth_manager"].validate_session.side_effect = RuntimeError("oops")

        with pytest.raises(HTTPException) as exc:
            validate_session("tok", current_session=parent_session)
        assert exc.value.status_code == 500

    def test_validate_session_expires_at_attribute(self, parent_session, mock_deps):
        """Session with expires_at attribute returns it in the response."""
        from api.routes.auth import validate_session

        validated_session = AuthSession(
            user_id="test-parent-id",
            role="parent",
            session_token="tok-with-expiry",
            email="parent@test.com",
        )
        # Simulate an expires_at attribute on the session
        validated_session.expires_at = "2025-12-31T23:59:59"

        mock_deps["auth_manager"].validate_session.return_value = (
            True,
            validated_session,
        )

        result = validate_session("tok-with-expiry", current_session=parent_session)
        assert result["session"]["expires_at"] == "2025-12-31T23:59:59"


# ============================================================================
# VERIFY EMAIL
# ============================================================================

class TestVerifyEmail:

    def test_verify_email_success(self, mock_deps):
        """Successful email verification (line 249-260)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.return_value = (
            True,
            "user-id-123",
            None,
        )

        request = VerifyEmailRequest(token="valid-verification-token")
        result = verify_email(request)

        assert result["status"] == "success"
        assert "verified successfully" in result["message"]
        mock_deps["auth_manager"].verify_email_token.assert_called_once_with(
            "valid-verification-token"
        )

    def test_verify_email_invalid_token(self, mock_deps):
        """Invalid token raises 400 (line 252-253)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.return_value = (
            False,
            None,
            "Token expired",
        )

        request = VerifyEmailRequest(token="expired-token")

        with pytest.raises(HTTPException) as exc:
            verify_email(request)
        assert exc.value.status_code == 400
        assert "Token expired" in exc.value.detail

    def test_verify_email_invalid_token_no_error_message(self, mock_deps):
        """Invalid token with no error message uses default (line 253)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.return_value = (
            False,
            None,
            None,
        )

        request = VerifyEmailRequest(token="bad-token")

        with pytest.raises(HTTPException) as exc:
            verify_email(request)
        assert exc.value.status_code == 400
        assert "Invalid or expired verification token" in exc.value.detail

    def test_verify_email_authentication_error(self, mock_deps):
        """AuthenticationError during email verification maps to 400 (line 264-265)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.side_effect = (
            AuthenticationError("Token tampered")
        )

        request = VerifyEmailRequest(token="tampered-token")

        with pytest.raises(HTTPException) as exc:
            verify_email(request)
        assert exc.value.status_code == 400
        assert "Token tampered" in exc.value.detail

    def test_verify_email_db_error(self, mock_deps):
        """DB error during email verification maps to 503 (line 266-268)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.side_effect = sqlite3.Error(
            "db error"
        )

        request = VerifyEmailRequest(token="tok")

        with pytest.raises(HTTPException) as exc:
            verify_email(request)
        assert exc.value.status_code == 503

    def test_verify_email_unexpected_error(self, mock_deps):
        """Unexpected error during email verification maps to 500 (line 269-271)."""
        from api.routes.auth import verify_email, VerifyEmailRequest

        mock_deps["auth_manager"].verify_email_token.side_effect = RuntimeError(
            "crash"
        )

        request = VerifyEmailRequest(token="tok")

        with pytest.raises(HTTPException) as exc:
            verify_email(request)
        assert exc.value.status_code == 500


# ============================================================================
# FORGOT PASSWORD
# ============================================================================

class TestForgotPassword:

    def test_forgot_password_success_email_sent(self, mock_deps):
        """Token generated, user found, email sent successfully (line 285-337)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok-abc",
            None,
        )

        # Mock email crypto for hashing
        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        # Mock DB lookup returning user
        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [
                {"parent_id": "user-123", "name": "Test User"}
            ]

            mock_deps["email_service"].send_password_reset_email.return_value = (
                True,
                None,
            )

            request = ForgotPasswordRequest(email="user@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"
        assert "password reset link" in result["message"]
        mock_deps["email_service"].send_password_reset_email.assert_called_once_with(
            user_id="user-123",
            user_email="user@example.com",
            user_name="Test User",
            reset_token="reset-tok-abc",
        )

    def test_forgot_password_success_tuple_row(self, mock_deps):
        """DB returns tuple rows instead of dicts (line 311-313)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok-abc",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [("user-456", "Tuple User")]

            mock_deps["email_service"].send_password_reset_email.return_value = (
                True,
                None,
            )

            request = ForgotPasswordRequest(email="tuple@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"
        mock_deps["email_service"].send_password_reset_email.assert_called_once_with(
            user_id="user-456",
            user_email="tuple@example.com",
            user_name="Tuple User",
            reset_token="reset-tok-abc",
        )

    def test_forgot_password_email_not_registered(self, mock_deps):
        """Email not found: token generation fails, still returns success (line 289-291)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            False,
            None,
            "Email not found",
        )

        request = ForgotPasswordRequest(email="nonexistent@example.com")
        result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # Security: always returns success even when email not found
        assert result["status"] == "success"
        assert "password reset link" in result["message"]

    def test_forgot_password_user_not_found_in_db(self, mock_deps):
        """Token generated but user not found in accounts table (line 305-320)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            # DB returns empty result
            mock_db.execute_read.return_value = []

            request = ForgotPasswordRequest(email="ghost@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"
        # Email should NOT have been sent since user wasn't found
        mock_deps["email_service"].send_password_reset_email.assert_not_called()

    def test_forgot_password_user_row_no_parent_id(self, mock_deps):
        """User row found but parent_id is None (line 318-320)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [{"parent_id": None, "name": "Ghost"}]

            request = ForgotPasswordRequest(email="noid@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # Returns early with just "message" (no "status") when user_id is None
        assert "registered" in result["message"]
        mock_deps["email_service"].send_password_reset_email.assert_not_called()

    def test_forgot_password_email_send_failure(self, mock_deps):
        """Email sending fails but endpoint still returns success (line 330-331)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [
                {"parent_id": "user-789", "name": "User"}
            ]

            mock_deps["email_service"].send_password_reset_email.return_value = (
                False,
                "SMTP error",
            )

            request = ForgotPasswordRequest(email="user@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # Still returns success for security
        assert result["status"] == "success"

    def test_forgot_password_db_error(self, mock_deps):
        """DB error during password reset still returns success (line 341-347)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.side_effect = (
            sqlite3.Error("db unavailable")
        )

        request = ForgotPasswordRequest(email="user@example.com")
        result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # Security: returns success even on DB error
        assert result["status"] == "success"
        assert "password reset link" in result["message"]

    def test_forgot_password_unexpected_error(self, mock_deps):
        """Unexpected error during password reset still returns success (line 348-354)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.side_effect = (
            RuntimeError("unexpected crash")
        )

        request = ForgotPasswordRequest(email="user@example.com")
        result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # Security: returns success even on unexpected error
        assert result["status"] == "success"
        assert "password reset link" in result["message"]

    def test_forgot_password_user_name_none_uses_default(self, mock_deps):
        """When user name is None, 'User' is used as default (line 327)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            mock_db.execute_read.return_value = [
                {"parent_id": "user-999", "name": None}
            ]

            mock_deps["email_service"].send_password_reset_email.return_value = (
                True,
                None,
            )

            request = ForgotPasswordRequest(email="noname@example.com")
            forgot_password(request, rate_limit_info=_make_rate_limit_info())

        mock_deps["email_service"].send_password_reset_email.assert_called_once_with(
            user_id="user-999",
            user_email="noname@example.com",
            user_name="User",
            reset_token="reset-tok",
        )

    def test_forgot_password_short_tuple_row(self, mock_deps):
        """Row is a tuple but with fewer than 2 elements (line 314-316).

        When the row has < 2 elements, user_id and user_name are set to None,
        and the function returns early (line 320) with just a 'message' key.
        """
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            True,
            "reset-tok",
            None,
        )

        mock_crypto = MagicMock()
        mock_crypto.hash_email.return_value = "hashed-email"
        mock_deps["get_email_crypto"].return_value = mock_crypto

        with patch("storage.database.db_manager") as mock_db:
            # Row is a non-dict, non-subscriptable short tuple
            mock_db.execute_read.return_value = [("only-one-element",)]

            request = ForgotPasswordRequest(email="short@example.com")
            result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        # user_id is None so early return with just "message" (no "status")
        assert "registered" in result["message"]
        mock_deps["email_service"].send_password_reset_email.assert_not_called()

    def test_forgot_password_token_generation_fails_no_error(self, mock_deps):
        """Token generation fails with no error message (line 289 branch)."""
        from api.routes.auth import forgot_password, ForgotPasswordRequest

        mock_deps["auth_manager"].generate_password_reset_token.return_value = (
            False,
            None,
            None,
        )

        request = ForgotPasswordRequest(email="user@example.com")
        result = forgot_password(request, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"


# ============================================================================
# RESET PASSWORD
# ============================================================================

class TestResetPassword:

    def test_reset_password_success(self, mock_deps):
        """Successful password reset (line 368-387)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.return_value = (
            True,
            None,
        )

        request = ResetPasswordRequest(
            token="valid-reset-tok",
            new_password="NewStr0ng!Pass",
            verify_password="NewStr0ng!Pass",
        )

        result = reset_password(request, rate_limit_info=_make_rate_limit_info())

        assert result["status"] == "success"
        assert "reset successfully" in result["message"]
        mock_deps["auth_manager"].reset_password_with_token.assert_called_once_with(
            token="valid-reset-tok",
            new_password="NewStr0ng!Pass",
        )

    def test_reset_password_mismatch(self, mock_deps):
        """Mismatched passwords raise 400 (line 370-371)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        request = ResetPasswordRequest(
            token="any-tok",
            new_password="Pass1!",
            verify_password="Different2!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Passwords do not match" in exc.value.detail

    def test_reset_password_invalid_token(self, mock_deps):
        """Invalid or expired token raises 400 (line 379-380)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.return_value = (
            False,
            "Token expired",
        )

        request = ResetPasswordRequest(
            token="expired-tok",
            new_password="NewP@ss1!",
            verify_password="NewP@ss1!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Token expired" in exc.value.detail

    def test_reset_password_invalid_token_no_error(self, mock_deps):
        """Invalid token with no error message uses default (line 380)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.return_value = (
            False,
            None,
        )

        request = ResetPasswordRequest(
            token="bad-tok",
            new_password="NewP@ss1!",
            verify_password="NewP@ss1!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Invalid or expired reset token" in exc.value.detail

    def test_reset_password_authentication_error(self, mock_deps):
        """AuthenticationError during reset maps to 400 (line 391-392)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.side_effect = (
            AuthenticationError("Token invalid")
        )

        request = ResetPasswordRequest(
            token="tampered-tok",
            new_password="NewP@ss1!",
            verify_password="NewP@ss1!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 400
        assert "Token invalid" in exc.value.detail

    def test_reset_password_db_error(self, mock_deps):
        """DB error during reset maps to 503 (line 393-395)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.side_effect = (
            sqlite3.Error("db error")
        )

        request = ResetPasswordRequest(
            token="tok",
            new_password="NewP@ss1!",
            verify_password="NewP@ss1!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 503

    def test_reset_password_unexpected_error(self, mock_deps):
        """Unexpected error during reset maps to 500 (line 396-398)."""
        from api.routes.auth import reset_password, ResetPasswordRequest

        mock_deps["auth_manager"].reset_password_with_token.side_effect = RuntimeError(
            "kaboom"
        )

        request = ResetPasswordRequest(
            token="tok",
            new_password="NewP@ss1!",
            verify_password="NewP@ss1!",
        )

        with pytest.raises(HTTPException) as exc:
            reset_password(request, rate_limit_info=_make_rate_limit_info())
        assert exc.value.status_code == 500
