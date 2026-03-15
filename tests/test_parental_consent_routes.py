"""
Tests for api/routes/parental_consent.py — COPPA Consent Flow

Compliance-critical paths tested:
    - Parent ownership verification (only parent of child can request consent)
    - Under-13 age gate (consent not required for 13+)
    - Token generation, storage, and email dispatch
    - Token verification with expiry and signature checks
    - Consent revocation (parent ownership enforced)
    - Consent status (parent/admin access only)
"""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Skip if email-validator not installed (required by pydantic EmailStr)
pytest.importorskip("email_validator", reason="email-validator not installed")

from core.authentication import AuthSession


@pytest.fixture
def parent_session():
    return AuthSession(
        user_id="parent123",
        role="parent",
        session_token="tok_abc",
        email="parent@test.com",
    )


@pytest.fixture
def admin_session():
    return AuthSession(
        user_id="admin1",
        role="admin",
        session_token="tok_admin",
        email="admin@test.com",
    )


@pytest.fixture
def other_parent_session():
    return AuthSession(
        user_id="other_parent",
        role="parent",
        session_token="tok_other",
        email="other@test.com",
    )


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_auth_manager(mock_db):
    with patch("api.routes.parental_consent.auth_manager") as m:
        m.db = mock_db
        yield m


@pytest.fixture
def mock_email():
    with patch("api.routes.parental_consent.email_service") as m:
        m.send_parental_consent_request = AsyncMock(return_value=True)
        yield m


@pytest.fixture
def mock_audit():
    with patch("api.routes.parental_consent.audit_log") as m:
        yield m


# --------------------------------------------------------------------------
# request_parental_consent
# --------------------------------------------------------------------------

class TestRequestParentalConsent:

    @pytest.mark.asyncio
    async def test_parent_can_request_consent_for_own_child(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],  # profile lookup
            [{'username': 'ParentName'}],  # parent name lookup
        ]
        mock_db.execute_write.return_value = None

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()
        request.base_url = "http://localhost/"

        result = await request_parental_consent(request, req_data, parent_session)

        assert result["status"] == "success"
        mock_email.send_parental_consent_request.assert_called_once()
        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrong_parent_denied(
        self, other_parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.return_value = [
            {'parent_id': 'parent123', 'age': 8}
        ]

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="other@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, other_parent_session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_age_13_plus_rejected(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.return_value = [
            {'parent_id': 'parent123', 'age': 14}
        ]

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=14,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, parent_session)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_profile_not_found(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.return_value = []

        req_data = ConsentRequest(
            profile_id="missing",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, parent_session)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_email_failure_returns_500(
        self, parent_session, mock_auth_manager, mock_email, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],
            [{'username': 'ParentName'}],
        ]
        mock_db.execute_write.return_value = None
        mock_email.send_parental_consent_request = AsyncMock(return_value=False)

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()
        request.base_url = "http://localhost/"

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, parent_session)
        assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# verify_parental_consent
# --------------------------------------------------------------------------

class TestVerifyParentalConsent:

    @pytest.mark.asyncio
    async def test_valid_token_grants_consent(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        token = "valid-token-abc"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

        mock_db.execute_query.side_effect = [
            [{  # Token lookup
                'user_id': 'parent123',
                'expires_at': future,
                'is_valid': 1,
            }],
            [{'parent_id': 'parent123'}],  # Profile ownership check
        ]
        mock_db.execute_write.return_value = None

        verification = ConsentVerification(
            token=token,
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"user-agent": "test-browser"}

        result = await verify_parental_consent(verification, "prof1", request)
        assert result["status"] == "success"
        assert result["profile_id"] == "prof1"

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        mock_db.execute_query.return_value = []  # Token not found

        verification = ConsentVerification(
            token="bad-token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mock_db.execute_query.return_value = [{
            'user_id': 'parent123',
            'expires_at': past,
            'is_valid': 1,
        }]

        verification = ConsentVerification(
            token="expired-token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 400
        assert "expired" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_already_used_token_rejected(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.return_value = [{
            'user_id': 'parent123',
            'expires_at': future,
            'is_valid': 0,  # Already used
        }]

        verification = ConsentVerification(
            token="used-token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_terms_not_accepted_rejected(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.return_value = [{
            'user_id': 'parent123',
            'expires_at': future,
            'is_valid': 1,
        }]

        verification = ConsentVerification(
            token="valid-token",
            electronic_signature="Jane Doe",
            accept_terms=False,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 400
        assert "terms" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_empty_signature_rejected(self, mock_auth_manager, mock_db):
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.return_value = [{
            'user_id': 'parent123',
            'expires_at': future,
            'is_valid': 1,
        }]

        verification = ConsentVerification(
            token="valid-token",
            electronic_signature=" ",  # Whitespace only
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 400
        assert "signature" in exc.value.detail.lower()


# --------------------------------------------------------------------------
# revoke_parental_consent
# --------------------------------------------------------------------------

class TestRevokeConsent:

    @pytest.mark.asyncio
    async def test_parent_can_revoke_own_child(
        self, parent_session, mock_auth_manager, mock_audit, mock_db
    ):
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]
        mock_db.execute_write.return_value = None

        revocation = ConsentRevocation(
            profile_id="prof1",
            reason="Testing revocation",
        )

        result = await revoke_parental_consent(revocation, parent_session)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_wrong_parent_cannot_revoke(
        self, other_parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]

        revocation = ConsentRevocation(profile_id="prof1")

        with pytest.raises(HTTPException) as exc:
            await revoke_parental_consent(revocation, other_parent_session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_revoke_missing_profile(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        mock_db.execute_query.return_value = []

        revocation = ConsentRevocation(profile_id="missing")

        with pytest.raises(HTTPException) as exc:
            await revoke_parental_consent(revocation, parent_session)
        assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# get_consent_status
# --------------------------------------------------------------------------

class TestGetConsentStatus:

    @pytest.mark.asyncio
    async def test_parent_can_view_own_child(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123'}],  # profile ownership
            [{  # consent status from age_manager.get_consent_status
                'parental_consent_given': 1,
                'parental_consent_date': '2024-01-01',
                'parental_consent_method': 'email',
                'coppa_verified': 1,
                'age': 10,
                'birthdate': '2014-01-01',
            }],
            [{  # consent history
                'consent_id': 'c1',
                'consent_type': 'initial',
                'consent_method': 'email_verification',
                'consent_date': '2024-01-01',
                'electronic_signature': 'Jane Doe',
                'is_active': 1,
            }],
        ]

        result = await get_consent_status("prof1", parent_session)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_admin_can_view_any_child(
        self, admin_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123'}],
            [{
                'parental_consent_given': 1,
                'parental_consent_date': '2024-01-01',
                'parental_consent_method': 'email',
                'coppa_verified': 1,
                'age': 10,
                'birthdate': '2014-01-01',
            }],
            [],  # empty consent history
        ]

        result = await get_consent_status("prof1", admin_session)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_wrong_parent_denied(
        self, other_parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]

        with pytest.raises(HTTPException) as exc:
            await get_consent_status("prof1", other_parent_session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_consent_status_not_found(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.return_value = []

        with pytest.raises(HTTPException) as exc:
            await get_consent_status("missing", parent_session)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_consent_status_error_in_status(
        self, parent_session, mock_auth_manager, mock_db
    ):
        """When age_manager.get_consent_status returns an error dict, 404 is raised (line 444)."""
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123'}],  # profile ownership
            [{'error': 'No consent record found'}],  # age_manager returns error
        ]

        with patch("api.routes.parental_consent.AgeVerificationManager") as mock_avm:
            mock_avm_instance = MagicMock()
            mock_avm_instance.get_consent_status.return_value = {
                "error": "No consent record found"
            }
            mock_avm.return_value = mock_avm_instance

            with pytest.raises(HTTPException) as exc:
                await get_consent_status("prof1", parent_session)
            assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# Error handler coverage (DB_ERRORS, AgeVerificationError, generic Exception)
# --------------------------------------------------------------------------

class TestRequestConsentErrorHandlers:
    """Cover error handler branches in request_parental_consent (lines 187-195)."""

    @pytest.mark.asyncio
    async def test_age_verification_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest
        from core.age_verification import AgeVerificationError

        mock_db.execute_query.return_value = [
            {'parent_id': 'parent123', 'age': 8}
        ]

        with patch(
            "api.routes.parental_consent.generate_consent_verification_token",
            side_effect=AgeVerificationError("age check failed"),
        ):
            req_data = ConsentRequest(
                profile_id="prof1",
                parent_email="parent@test.com",
                child_name="Tommy",
                child_age=8,
            )
            request = MagicMock()

            with pytest.raises(HTTPException) as exc:
                await request_parental_consent(request, req_data, parent_session)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_db_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        # First query raises a DB error
        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.OperationalError("db locked")

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, parent_session)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = RuntimeError("unexpected boom")

        req_data = ConsentRequest(
            profile_id="prof1",
            parent_email="parent@test.com",
            child_name="Tommy",
            child_age=8,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await request_parental_consent(request, req_data, parent_session)
        assert exc.value.status_code == 500


class TestRequestConsentParentNameLookup:
    """Cover parent name lookup paths (lines 147-156).

    The source does ``from storage.database import db_manager`` at line 140,
    so we must patch ``storage.database.db_manager`` (not ``mock_db`` which
    backs ``auth_manager.db``).
    """

    @pytest.mark.asyncio
    async def test_parent_name_lookup_db_error(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """DB error during parent name lookup is caught, falls back to user_id (lines 153-154)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        import sqlite3

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],  # profile lookup (auth_manager.db)
        ]
        mock_db.execute_write.return_value = None

        mock_dbm = MagicMock()
        mock_dbm.execute_query.side_effect = sqlite3.OperationalError("db locked")

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_parent_name_lookup_no_rows(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """When username lookup returns empty rows, falls back to user_id (line 146)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],  # profile lookup (auth_manager.db)
        ]
        mock_db.execute_write.return_value = None

        # Mock db_manager (the object imported at line 140) to return empty rows
        mock_dbm = MagicMock()
        mock_dbm.execute_query.return_value = []

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_parent_name_lookup_username_found(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """Username found in rows → parent_name set (lines 147-150)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],  # profile lookup (auth_manager.db)
        ]
        mock_db.execute_write.return_value = None

        mock_dbm = MagicMock()
        mock_dbm.execute_query.return_value = [{"username": "JaneDoe"}]

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"
                # Verify parent_name was set from db lookup
                call_kwargs = mock_email.send_parental_consent_request.call_args
                assert call_kwargs[1]["parent_name"] == "JaneDoe" or call_kwargs.kwargs["parent_name"] == "JaneDoe"

    @pytest.mark.asyncio
    async def test_parent_name_lookup_key_error(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """Rows exist but missing 'username' key → KeyError caught (line 151)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],
        ]
        mock_db.execute_write.return_value = None

        mock_dbm = MagicMock()
        mock_dbm.execute_query.return_value = [{"wrong_column": "value"}]

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_parent_name_lookup_username_none(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """Username is None → falls back to user_id (line 149 falsy check)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],
        ]
        mock_db.execute_write.return_value = None

        mock_dbm = MagicMock()
        mock_dbm.execute_query.return_value = [{"username": None}]

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_parent_name_lookup_generic_exception(
        self, parent_session, mock_auth_manager, mock_email, mock_audit, mock_db
    ):
        """Generic exception during parent name lookup is caught (lines 155-156)."""
        from api.routes.parental_consent import request_parental_consent, ConsentRequest

        mock_db.execute_query.side_effect = [
            [{'parent_id': 'parent123', 'age': 8}],
        ]
        mock_db.execute_write.return_value = None

        mock_dbm = MagicMock()
        mock_dbm.execute_query.side_effect = RuntimeError("something weird")

        with patch("api.routes.parental_consent.generate_consent_verification_token", return_value=("token123", "hash123")):
            with patch("storage.database.db_manager", mock_dbm):
                req_data = ConsentRequest(
                    profile_id="prof1",
                    parent_email="parent@test.com",
                    child_name="Tommy",
                    child_age=8,
                )
                request = MagicMock()
                request.base_url = "http://localhost/"

                result = await request_parental_consent(request, req_data, parent_session)
                assert result["status"] == "success"


class TestVerifyConsentErrorHandlers:
    """Cover error handler branches in verify_parental_consent (lines 266, 273-279, 331-339)."""

    @pytest.mark.asyncio
    async def test_profile_not_found_in_verify(self, mock_auth_manager, mock_db):
        """Profile not found during ownership check raises 404 (line 266)."""
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.side_effect = [
            [{'user_id': 'parent123', 'expires_at': future, 'is_valid': 1}],
            [],  # profile not found
        ]

        verification = ConsentVerification(
            token="valid-token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "missing_prof", request)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_token_user_does_not_own_profile(self, mock_auth_manager, mock_db):
        """Token user != profile parent raises 403 (lines 273-279)."""
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.side_effect = [
            [{'user_id': 'parent123', 'expires_at': future, 'is_valid': 1}],
            [{'parent_id': 'different_parent'}],  # different parent owns profile
        ]

        verification = ConsentVerification(
            token="valid-token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_verify_db_error(self, mock_auth_manager, mock_db):
        """DB error during verify raises 503 (lines 334-336)."""
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.OperationalError("db locked")

        verification = ConsentVerification(
            token="token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_verify_unexpected_error(self, mock_auth_manager, mock_db):
        """Unexpected error during verify raises 500 (lines 337-339)."""
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification

        mock_db.execute_query.side_effect = RuntimeError("unexpected")

        verification = ConsentVerification(
            token="token",
            electronic_signature="Jane Doe",
            accept_terms=True,
        )
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await verify_parental_consent(verification, "prof1", request)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_verify_age_verification_error(self, mock_auth_manager, mock_db):
        """AgeVerificationError during verify raises 400 (lines 331-333)."""
        from api.routes.parental_consent import verify_parental_consent, ConsentVerification
        from core.age_verification import AgeVerificationError

        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_db.execute_query.side_effect = [
            [{'user_id': 'parent123', 'expires_at': future, 'is_valid': 1}],
            [{'parent_id': 'parent123'}],
        ]

        with patch(
            "api.routes.parental_consent.AgeVerificationManager"
        ) as mock_avm:
            mock_avm_instance = MagicMock()
            mock_avm_instance.log_parental_consent.side_effect = AgeVerificationError("failed")
            mock_avm.return_value = mock_avm_instance

            verification = ConsentVerification(
                token="valid-token",
                electronic_signature="Jane Doe",
                accept_terms=True,
            )
            request = MagicMock()
            request.client.host = "127.0.0.1"
            request.headers = {"user-agent": "test"}

            with pytest.raises(HTTPException) as exc:
                await verify_parental_consent(verification, "prof1", request)
            assert exc.value.status_code == 400


class TestRevokeConsentErrorHandlers:
    """Cover error handler branches in revoke_parental_consent (lines 383, 400-408)."""

    @pytest.mark.asyncio
    async def test_revoke_fails_returns_500(
        self, parent_session, mock_auth_manager, mock_db
    ):
        """When revoke_parental_consent returns False, 500 is raised (line 383)."""
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]

        with patch(
            "api.routes.parental_consent.AgeVerificationManager"
        ) as mock_avm:
            mock_avm_instance = MagicMock()
            mock_avm_instance.revoke_parental_consent.return_value = False
            mock_avm.return_value = mock_avm_instance

            revocation = ConsentRevocation(profile_id="prof1")

            with pytest.raises(HTTPException) as exc:
                await revoke_parental_consent(revocation, parent_session)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_revoke_age_verification_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        """AgeVerificationError during revoke raises 400 (lines 400-402)."""
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation
        from core.age_verification import AgeVerificationError

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]

        with patch(
            "api.routes.parental_consent.AgeVerificationManager"
        ) as mock_avm:
            mock_avm_instance = MagicMock()
            mock_avm_instance.revoke_parental_consent.side_effect = AgeVerificationError("failed")
            mock_avm.return_value = mock_avm_instance

            revocation = ConsentRevocation(profile_id="prof1")

            with pytest.raises(HTTPException) as exc:
                await revoke_parental_consent(revocation, parent_session)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_revoke_db_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        """DB error during revoke raises 503 (lines 403-405)."""
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.OperationalError("db locked")

        revocation = ConsentRevocation(profile_id="prof1")

        with pytest.raises(HTTPException) as exc:
            await revoke_parental_consent(revocation, parent_session)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_revoke_unexpected_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        """Unexpected error during revoke raises 500 (lines 406-408)."""
        from api.routes.parental_consent import revoke_parental_consent, ConsentRevocation

        mock_db.execute_query.side_effect = RuntimeError("unexpected")

        revocation = ConsentRevocation(profile_id="prof1")

        with pytest.raises(HTTPException) as exc:
            await revoke_parental_consent(revocation, parent_session)
        assert exc.value.status_code == 500


class TestGetConsentStatusErrorHandlers:
    """Cover error handler branches in get_consent_status (lines 489-497)."""

    @pytest.mark.asyncio
    async def test_status_age_verification_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status
        from core.age_verification import AgeVerificationError

        mock_db.execute_query.return_value = [{'parent_id': 'parent123'}]

        with patch(
            "api.routes.parental_consent.AgeVerificationManager"
        ) as mock_avm:
            mock_avm_instance = MagicMock()
            mock_avm_instance.get_consent_status.side_effect = AgeVerificationError("failed")
            mock_avm.return_value = mock_avm_instance

            with pytest.raises(HTTPException) as exc:
                await get_consent_status("prof1", parent_session)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_status_db_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.OperationalError("db locked")

        with pytest.raises(HTTPException) as exc:
            await get_consent_status("prof1", parent_session)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_status_unexpected_error(
        self, parent_session, mock_auth_manager, mock_db
    ):
        from api.routes.parental_consent import get_consent_status

        mock_db.execute_query.side_effect = RuntimeError("unexpected")

        with pytest.raises(HTTPException) as exc:
            await get_consent_status("prof1", parent_session)
        assert exc.value.status_code == 500
