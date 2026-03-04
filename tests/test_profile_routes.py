"""
Tests for api/routes/profiles.py — COPPA Profile CRUD

Compliance-critical paths tested:
    - COPPA age gate: under-13 without consent blocked (403)
    - COPPA age gate: under-13 with consent allowed
    - 13+ creation without consent allowed
    - Parent authorization: can only create for self
    - Admin bypass: admins can create for anyone
    - Age/birthdate validation
    - Profile deactivation (soft delete, never hard)
    - Data export (COPPA/FERPA right to portability)
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.authentication import AuthSession
from core.profile_manager import ChildProfile


@pytest.fixture
def parent_session():
    return AuthSession(
        user_id="a" * 32,
        role="parent",
        session_token="tok_abc",
        email="parent@test.com",
    )


@pytest.fixture
def admin_session():
    return AuthSession(
        user_id="b" * 32,
        role="admin",
        session_token="tok_admin",
        email="admin@test.com",
    )


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_deps(mock_db):
    """Patch all external dependencies for profile routes."""
    with patch("api.routes.profiles.auth_manager") as auth_mgr, \
         patch("api.routes.profiles.audit_log") as audit, \
         patch("api.routes.profiles.rate_limiter") as rl:
        auth_mgr.db = mock_db
        rl.check_rate_limit.return_value = (True, {"remaining": 19})
        yield {"auth_manager": auth_mgr, "db": mock_db, "audit": audit}


def _make_profile(**overrides):
    defaults = dict(
        profile_id="prof1",
        parent_id="a" * 32,
        name="Tommy",
        age=10,
        grade="5th",
        is_active=True,
        total_sessions=0,
        total_questions=0,
    )
    defaults.update(overrides)
    return ChildProfile(**defaults)


# --------------------------------------------------------------------------
# create_profile — COPPA Age Gate
# --------------------------------------------------------------------------

class TestCreateProfileCoppaAgeGate:

    def test_under_13_without_consent_blocked(self, parent_session, mock_deps, mock_db):
        """COPPA: Under-13 child without parental consent must be rejected."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()

        # No consent in DB
        mock_db.execute_query.return_value = []

        request = CreateProfileRequest(
            parent_id="a" * 32,
            name="Tommy",
            birthdate=birthdate,
            grade_level="5th",
        )

        with pytest.raises(HTTPException) as exc:
            create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 403
        assert "parental_consent_required" in str(exc.value.detail)

    def test_under_13_with_consent_allowed(self, parent_session, mock_deps, mock_db):
        """COPPA: Under-13 child WITH parental consent must be allowed."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()

        # Consent exists in DB
        mock_db.execute_query.side_effect = [
            [{'is_active': 1}],  # consent check
        ]
        mock_db.execute_write.return_value = None

        profile = _make_profile()
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Tommy",
                birthdate=birthdate,
                grade_level="5th",
            )

            result = create_profile(request, parent_session, rate_limit_info={})
            assert result["age_verification"]["coppa_compliant"] is True
            assert result["age_verification"]["has_parental_consent"] is True

    def test_age_13_plus_no_consent_required(self, parent_session, mock_deps, mock_db):
        """COPPA: 13+ child does not need parental consent."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()

        # No consent query needed for 13+
        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None

        profile = _make_profile(age=14)
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Tommy",
                birthdate=birthdate,
                grade_level="9th",
            )

            result = create_profile(request, parent_session, rate_limit_info={})
            assert result["age_verification"]["is_under_13"] is False
            assert result["age_verification"]["coppa_compliant"] is True


# --------------------------------------------------------------------------
# create_profile — Authorization
# --------------------------------------------------------------------------

class TestCreateProfileAuthorization:

    def test_parent_cannot_create_for_other_parent(self, parent_session, mock_deps):
        """Parent A cannot create a profile under Parent B."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        request = CreateProfileRequest(
            parent_id="c" * 32,  # Different parent
            name="Tommy",
            age=14,
            grade_level="9th",
        )

        with pytest.raises(HTTPException) as exc:
            create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 403

    def test_admin_can_create_for_any_parent(self, admin_session, mock_deps, mock_db):
        """Admin should be able to create profiles for any parent."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None

        profile = _make_profile(parent_id="c" * 32, age=14)
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="c" * 32,
                name="Tommy",
                age=14,
                grade_level="9th",
            )

            result = create_profile(request, admin_session, rate_limit_info={})
            assert result["profile_id"] == "prof1"


# --------------------------------------------------------------------------
# create_profile — Input Validation
# --------------------------------------------------------------------------

class TestCreateProfileValidation:

    def test_no_age_or_birthdate_rejected(self, parent_session, mock_deps):
        """Either age or birthdate must be provided."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        request = CreateProfileRequest(
            parent_id="a" * 32,
            name="Tommy",
            grade_level="5th",
            # No age, no birthdate
        )

        with pytest.raises(HTTPException) as exc:
            create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_invalid_birthdate_rejected(self, parent_session, mock_deps):
        """Invalid birthdate format should be rejected."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        request = CreateProfileRequest(
            parent_id="a" * 32,
            name="Tommy",
            birthdate="not-a-date",
            grade_level="5th",
        )

        with pytest.raises(HTTPException) as exc:
            create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_db_error_returns_error(self, parent_session, mock_deps, mock_db):
        """Database errors on profile INSERT should raise an error."""
        import sqlite3
        from api.routes.profiles import create_profile, CreateProfileRequest

        # Reads succeed (parent check, consent check), write fails
        mock_db.execute_query.return_value = []
        mock_db.execute_write.side_effect = sqlite3.OperationalError("db fail")

        request = CreateProfileRequest(
            parent_id="a" * 32,
            name="Tommy",
            age=14,
            grade_level="9th",
        )

        with pytest.raises(HTTPException) as exc:
            create_profile(request, parent_session, rate_limit_info={})
        # Should be an error status code (400 from ProfileError or 503 from DB_ERRORS)
        assert exc.value.status_code >= 400


# --------------------------------------------------------------------------
# deactivate_profile (soft delete, not hard delete)
# --------------------------------------------------------------------------

class TestDeactivateProfile:

    def test_deactivate_calls_soft_delete(self, parent_session, mock_deps):
        """Deactivation must be a soft delete, not hard delete."""
        from api.routes.profiles import deactivate_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.return_value = True

            result = deactivate_profile("prof1", parent_session)
            assert result["status"] == "success"
            PM.return_value.deactivate_profile.assert_called_once_with("prof1")
            # Verify hard delete was NOT called
            PM.return_value.delete_profile_permanently.assert_not_called()


# --------------------------------------------------------------------------
# export_profile_data — COPPA/FERPA Right to Data Portability
# --------------------------------------------------------------------------

class TestExportProfileData:

    def test_export_includes_all_required_data(self, parent_session, mock_deps, mock_db):
        """Export must include profile, conversations, incidents, stats."""
        from api.routes.profiles import export_profile_data

        profile = _make_profile()
        with patch("api.routes.profiles.ProfileManager") as PM, \
             patch("api.routes.profiles.conversation_store") as cs, \
             patch("api.routes.profiles.incident_logger") as il:

            PM.return_value.get_profile.return_value = profile
            cs.get_profile_conversations.return_value = []
            il.get_profile_incidents.return_value = []

            response = export_profile_data("prof1", parent_session)
            data = response.body.decode()

            import json
            export = json.loads(data)
            assert "profile" in export
            assert "conversations" in export
            assert "safety_incidents" in export
            assert "usage_statistics" in export
            assert "export_metadata" in export
            assert export["export_metadata"]["compliance"]["coppa_compliant"] is True
            assert export["export_metadata"]["compliance"]["ferpa_compliant"] is True

    def test_export_not_found_returns_404(self, parent_session, mock_deps):
        """Non-existent profile returns 404."""
        from api.routes.profiles import export_profile_data

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = None

            with pytest.raises(HTTPException) as exc:
                export_profile_data("missing", parent_session)
            assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# get_profile — success and error paths
# --------------------------------------------------------------------------

class TestGetProfile:

    def test_get_profile_success(self, parent_session, mock_deps):
        """get_profile returns profile dict on success."""
        from api.routes.profiles import get_profile

        profile = _make_profile()
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = profile
            result = get_profile("prof1", parent_session)
        assert result["profile_id"] == "prof1"
        assert result["name"] == "Tommy"

    def test_get_profile_not_found_returns_404(self, parent_session, mock_deps):
        """get_profile raises 404 when profile_manager returns None."""
        from api.routes.profiles import get_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = None
            with pytest.raises(HTTPException) as exc:
                get_profile("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_get_profile_profile_not_found_error(self, parent_session, mock_deps):
        """get_profile handles ProfileNotFoundError -> 404."""
        from api.routes.profiles import get_profile
        from core.profile_manager import ProfileNotFoundError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = ProfileNotFoundError("gone")
            with pytest.raises(HTTPException) as exc:
                get_profile("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_get_profile_db_error_returns_503(self, parent_session, mock_deps):
        """get_profile translates DB errors to 503."""
        import sqlite3
        from api.routes.profiles import get_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = sqlite3.OperationalError("disk I/O error")
            with pytest.raises(HTTPException) as exc:
                get_profile("prof1", parent_session)
        assert exc.value.status_code == 503

    def test_get_profile_unexpected_error_returns_500(self, parent_session, mock_deps):
        """get_profile translates unexpected exceptions to 500."""
        from api.routes.profiles import get_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = RuntimeError("boom")
            with pytest.raises(HTTPException) as exc:
                get_profile("prof1", parent_session)
        assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# get_profiles_for_parent — listing and filtering
# --------------------------------------------------------------------------

class TestGetProfilesForParent:

    def test_returns_active_profiles_by_default(self, parent_session, mock_deps):
        """Default call filters out inactive profiles."""
        from api.routes.profiles import get_profiles_for_parent

        active = _make_profile(profile_id="p1", is_active=True)
        inactive = _make_profile(profile_id="p2", is_active=False)

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profiles_by_parent.return_value = [active, inactive]
            result = get_profiles_for_parent("a" * 32, include_inactive=False, session=parent_session)

        assert result["count"] == 1
        assert result["profiles"][0]["profile_id"] == "p1"

    def test_include_inactive_returns_all(self, parent_session, mock_deps):
        """include_inactive=True returns both active and inactive profiles."""
        from api.routes.profiles import get_profiles_for_parent

        active = _make_profile(profile_id="p1", is_active=True)
        inactive = _make_profile(profile_id="p2", is_active=False)

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profiles_by_parent.return_value = [active, inactive]
            result = get_profiles_for_parent("a" * 32, include_inactive=True, session=parent_session)

        assert result["count"] == 2

    def test_empty_list_returns_zero_count(self, parent_session, mock_deps):
        """No profiles returns empty list with count 0."""
        from api.routes.profiles import get_profiles_for_parent

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profiles_by_parent.return_value = []
            result = get_profiles_for_parent("a" * 32, include_inactive=False, session=parent_session)

        assert result["count"] == 0
        assert result["profiles"] == []

    def test_db_error_returns_503(self, parent_session, mock_deps):
        """DB errors in get_profiles_for_parent produce 503."""
        import sqlite3
        from api.routes.profiles import get_profiles_for_parent

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profiles_by_parent.side_effect = sqlite3.Error("db fail")
            with pytest.raises(HTTPException) as exc:
                get_profiles_for_parent("a" * 32, include_inactive=False, session=parent_session)
        assert exc.value.status_code == 503

    def test_unexpected_error_returns_500(self, parent_session, mock_deps):
        """Unexpected errors in get_profiles_for_parent produce 500."""
        from api.routes.profiles import get_profiles_for_parent

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profiles_by_parent.side_effect = RuntimeError("unexpected")
            with pytest.raises(HTTPException) as exc:
                get_profiles_for_parent("a" * 32, include_inactive=False, session=parent_session)
        assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# update_profile — success and error paths
# --------------------------------------------------------------------------

class TestUpdateProfile:

    def test_update_profile_success(self, parent_session, mock_deps):
        """Successful update returns updated profile dict."""
        from api.routes.profiles import update_profile, UpdateProfileRequest

        updated_profile = _make_profile(name="Timothy")
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.return_value = True
            PM.return_value.get_profile.return_value = updated_profile

            request = UpdateProfileRequest(name="Timothy")
            result = update_profile("prof1", request, parent_session, rate_limit_info={})

        assert result["name"] == "Timothy"

    def test_update_profile_with_grade_level(self, parent_session, mock_deps):
        """Update with grade_level field is forwarded correctly."""
        from api.routes.profiles import update_profile, UpdateProfileRequest

        updated_profile = _make_profile(grade="6th")
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.return_value = True
            PM.return_value.get_profile.return_value = updated_profile

            request = UpdateProfileRequest(grade_level="6th")
            result = update_profile("prof1", request, parent_session, rate_limit_info={})

        PM.return_value.update_profile.assert_called_once_with(
            profile_id="prof1", grade_level="6th"
        )

    def test_update_profile_not_found_error(self, parent_session, mock_deps):
        """ProfileNotFoundError during update -> 404."""
        from api.routes.profiles import update_profile, UpdateProfileRequest
        from core.profile_manager import ProfileNotFoundError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = ProfileNotFoundError("not found")
            request = UpdateProfileRequest(name="X")
            with pytest.raises(HTTPException) as exc:
                update_profile("ghost", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 404

    def test_update_profile_validation_error(self, parent_session, mock_deps):
        """ProfileValidationError during update -> 400."""
        from api.routes.profiles import update_profile, UpdateProfileRequest
        from core.profile_manager import ProfileValidationError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = ProfileValidationError("bad age")
            request = UpdateProfileRequest(age=3)
            with pytest.raises(HTTPException) as exc:
                update_profile("prof1", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_update_profile_permission_denied(self, parent_session, mock_deps):
        """PermissionDeniedError during update -> 403."""
        from api.routes.profiles import update_profile, UpdateProfileRequest
        from core.profile_manager import PermissionDeniedError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = PermissionDeniedError("denied")
            request = UpdateProfileRequest(name="Hacker")
            with pytest.raises(HTTPException) as exc:
                update_profile("prof1", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 403

    def test_update_profile_db_error_returns_503(self, parent_session, mock_deps):
        """DB error during update -> 503."""
        import sqlite3
        from api.routes.profiles import update_profile, UpdateProfileRequest

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = sqlite3.OperationalError("disk error")
            request = UpdateProfileRequest(name="Alice")
            with pytest.raises(HTTPException) as exc:
                update_profile("prof1", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 503

    def test_update_profile_generic_profile_error(self, parent_session, mock_deps):
        """Generic ProfileError during update -> 400."""
        from api.routes.profiles import update_profile, UpdateProfileRequest
        from core.profile_manager import ProfileError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = ProfileError("generic fail")
            request = UpdateProfileRequest(name="Bob")
            with pytest.raises(HTTPException) as exc:
                update_profile("prof1", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_update_profile_unexpected_error_returns_500(self, parent_session, mock_deps):
        """Unexpected error during update -> 500."""
        from api.routes.profiles import update_profile, UpdateProfileRequest

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.side_effect = RuntimeError("surprise")
            request = UpdateProfileRequest(name="Charlie")
            with pytest.raises(HTTPException) as exc:
                update_profile("prof1", request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 500

    def test_update_no_fields_still_returns_profile(self, parent_session, mock_deps):
        """Update with no changed fields still returns the current profile."""
        from api.routes.profiles import update_profile, UpdateProfileRequest

        profile = _make_profile()
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.update_profile.return_value = True
            PM.return_value.get_profile.return_value = profile

            request = UpdateProfileRequest()  # all fields None
            result = update_profile("prof1", request, parent_session, rate_limit_info={})

        assert result["profile_id"] == "prof1"
        # update_profile called with only profile_id (no extra kwargs)
        PM.return_value.update_profile.assert_called_once_with(profile_id="prof1")


# --------------------------------------------------------------------------
# deactivate_profile — error paths
# --------------------------------------------------------------------------

class TestDeactivateProfileErrors:

    def test_deactivate_not_found_returns_404(self, parent_session, mock_deps):
        """ProfileNotFoundError during deactivation -> 404."""
        from api.routes.profiles import deactivate_profile
        from core.profile_manager import ProfileNotFoundError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.side_effect = ProfileNotFoundError("gone")
            with pytest.raises(HTTPException) as exc:
                deactivate_profile("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_deactivate_permission_denied_returns_403(self, parent_session, mock_deps):
        """PermissionDeniedError during deactivation -> 403."""
        from api.routes.profiles import deactivate_profile
        from core.profile_manager import PermissionDeniedError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.side_effect = PermissionDeniedError("no access")
            with pytest.raises(HTTPException) as exc:
                deactivate_profile("prof1", parent_session)
        assert exc.value.status_code == 403

    def test_deactivate_profile_error_returns_400(self, parent_session, mock_deps):
        """Generic ProfileError during deactivation -> 400."""
        from api.routes.profiles import deactivate_profile
        from core.profile_manager import ProfileError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.side_effect = ProfileError("general fail")
            with pytest.raises(HTTPException) as exc:
                deactivate_profile("prof1", parent_session)
        assert exc.value.status_code == 400

    def test_deactivate_db_error_returns_503(self, parent_session, mock_deps):
        """DB error during deactivation -> 503."""
        import sqlite3
        from api.routes.profiles import deactivate_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.side_effect = sqlite3.OperationalError("io error")
            with pytest.raises(HTTPException) as exc:
                deactivate_profile("prof1", parent_session)
        assert exc.value.status_code == 503

    def test_deactivate_unexpected_error_returns_500(self, parent_session, mock_deps):
        """Unexpected errors during deactivation -> 500."""
        from api.routes.profiles import deactivate_profile

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.deactivate_profile.side_effect = RuntimeError("kaboom")
            with pytest.raises(HTTPException) as exc:
                deactivate_profile("prof1", parent_session)
        assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# get_profile_statistics
# --------------------------------------------------------------------------

class TestGetProfileStatistics:

    def test_get_stats_success(self, parent_session, mock_deps):
        """get_profile_statistics returns expected keys."""
        from api.routes.profiles import get_profile_statistics

        profile = _make_profile(total_sessions=5, total_questions=20)
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = profile
            result = get_profile_statistics("prof1", parent_session)

        assert result["profile_id"] == "prof1"
        assert result["total_sessions"] == 5
        assert result["total_questions"] == 20
        assert result["is_active"] is True

    def test_get_stats_not_found_returns_404(self, parent_session, mock_deps):
        """Returns 404 when profile is None."""
        from api.routes.profiles import get_profile_statistics

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.return_value = None
            with pytest.raises(HTTPException) as exc:
                get_profile_statistics("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_get_stats_profile_not_found_error(self, parent_session, mock_deps):
        """ProfileNotFoundError -> 404."""
        from api.routes.profiles import get_profile_statistics
        from core.profile_manager import ProfileNotFoundError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = ProfileNotFoundError("gone")
            with pytest.raises(HTTPException) as exc:
                get_profile_statistics("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_get_stats_db_error_returns_503(self, parent_session, mock_deps):
        """DB error in stats -> 503."""
        import sqlite3
        from api.routes.profiles import get_profile_statistics

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = sqlite3.Error("disk fail")
            with pytest.raises(HTTPException) as exc:
                get_profile_statistics("prof1", parent_session)
        assert exc.value.status_code == 503

    def test_get_stats_unexpected_error_returns_500(self, parent_session, mock_deps):
        """Unexpected error in stats -> 500."""
        from api.routes.profiles import get_profile_statistics

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = RuntimeError("oops")
            with pytest.raises(HTTPException) as exc:
                get_profile_statistics("prof1", parent_session)
        assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# export_profile_data — additional error paths
# --------------------------------------------------------------------------

class TestExportProfileDataErrors:

    def test_export_db_error_returns_503(self, parent_session, mock_deps):
        """DB error during export -> 503."""
        import sqlite3
        from api.routes.profiles import export_profile_data

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = sqlite3.OperationalError("io fail")
            with pytest.raises(HTTPException) as exc:
                export_profile_data("prof1", parent_session)
        assert exc.value.status_code == 503

    def test_export_unexpected_error_returns_500(self, parent_session, mock_deps):
        """Unexpected error during export -> 500."""
        from api.routes.profiles import export_profile_data

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = RuntimeError("boom")
            with pytest.raises(HTTPException) as exc:
                export_profile_data("prof1", parent_session)
        assert exc.value.status_code == 500

    def test_export_profile_not_found_error(self, parent_session, mock_deps):
        """ProfileNotFoundError during export -> 404."""
        from api.routes.profiles import export_profile_data
        from core.profile_manager import ProfileNotFoundError

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.get_profile.side_effect = ProfileNotFoundError("missing")
            with pytest.raises(HTTPException) as exc:
                export_profile_data("ghost", parent_session)
        assert exc.value.status_code == 404

    def test_export_includes_conversations_with_messages(self, parent_session, mock_deps, mock_db):
        """Export correctly serialises conversations that contain messages."""
        import json
        from api.routes.profiles import export_profile_data

        profile = _make_profile()

        # Build a minimal fake conversation with messages
        fake_conv = MagicMock()
        fake_conv.conversation_id = "conv-001"
        fake_conv.subject_area = "math"
        fake_conv.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        fake_conv.updated_at = datetime(2025, 1, 2, tzinfo=timezone.utc)
        fake_conv.message_count = 1

        fake_msg = MagicMock()
        fake_msg.to_dict.return_value = {"role": "user", "content": "hi"}

        with patch("api.routes.profiles.ProfileManager") as PM, \
             patch("api.routes.profiles.conversation_store") as cs, \
             patch("api.routes.profiles.incident_logger") as il:

            PM.return_value.get_profile.return_value = profile
            cs.get_profile_conversations.return_value = [fake_conv]
            cs.get_conversation_messages.return_value = [fake_msg]
            il.get_profile_incidents.return_value = []

            response = export_profile_data("prof1", parent_session)
            export = json.loads(response.body.decode())

        assert export["total_conversations"] == 1
        assert export["conversations"][0]["conversation_id"] == "conv-001"
        assert len(export["conversations"][0]["messages"]) == 1


# --------------------------------------------------------------------------
# create_profile — additional paths
# --------------------------------------------------------------------------

class TestCreateProfileAdditionalPaths:

    def test_create_profile_with_age_only(self, parent_session, mock_deps, mock_db):
        """Creating a 13+ profile using age (no birthdate) succeeds."""
        from api.routes.profiles import create_profile, CreateProfileRequest

        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None

        profile = _make_profile(age=15)
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Teenager",
                age=15,
                grade_level="10th",
            )
            result = create_profile(request, parent_session, rate_limit_info={})

        assert result["age_verification"]["is_under_13"] is False

    def test_create_profile_failed_none_profile_returns_400(self, parent_session, mock_deps, mock_db):
        """If profile_manager.create_profile returns None, raise 400."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = None

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Orphan",
                birthdate=birthdate,
                grade_level="9th",
            )
            with pytest.raises(HTTPException) as exc:
                create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_create_profile_profile_validation_error(self, parent_session, mock_deps, mock_db):
        """ProfileValidationError from create_profile -> 400."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from core.profile_manager import ProfileValidationError
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.side_effect = ProfileValidationError("name too short")

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Valid Name",
                birthdate=birthdate,
                grade_level="9th",
            )
            with pytest.raises(HTTPException) as exc:
                create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_create_profile_permission_denied_error(self, parent_session, mock_deps, mock_db):
        """PermissionDeniedError from create_profile -> 403."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from core.profile_manager import PermissionDeniedError
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.side_effect = PermissionDeniedError("denied")

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Valid Name",
                birthdate=birthdate,
                grade_level="9th",
            )
            with pytest.raises(HTTPException) as exc:
                create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 403

    def test_create_profile_generic_profile_error(self, parent_session, mock_deps, mock_db):
        """Generic ProfileError from create_profile -> 400."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from core.profile_manager import ProfileError
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.side_effect = ProfileError("generic error")

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Valid Name",
                birthdate=birthdate,
                grade_level="9th",
            )
            with pytest.raises(HTTPException) as exc:
                create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 400

    def test_create_profile_unexpected_error_returns_500(self, parent_session, mock_deps, mock_db):
        """Unexpected errors during create_profile -> 500."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []

        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.side_effect = RuntimeError("weird failure")

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Valid Name",
                birthdate=birthdate,
                grade_level="9th",
            )
            with pytest.raises(HTTPException) as exc:
                create_profile(request, parent_session, rate_limit_info={})
        assert exc.value.status_code == 500

    def test_create_profile_birthdate_stores_age_verification_data(self, parent_session, mock_deps, mock_db):
        """When birthdate is supplied, the birthdate UPDATE is executed after INSERT."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 14).isoformat()
        mock_db.execute_query.return_value = []
        mock_db.execute_write.return_value = None

        profile = _make_profile(age=14)
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Birthdated",
                birthdate=birthdate,
                grade_level="9th",
            )
            create_profile(request, parent_session, rate_limit_info={})

        # execute_write should have been called for the age_verification UPDATE
        mock_db.execute_write.assert_called()

    def test_create_profile_rate_limit_exceeded_returns_429(self, parent_session, mock_deps):
        """Rate limit check raises 429 when limit exceeded."""
        from api.routes.profiles import check_profile_rate_limit
        from unittest.mock import MagicMock

        # Patch rate_limiter to return not-allowed
        with patch("api.routes.profiles.rate_limiter") as rl:
            rl.check_rate_limit.return_value = (False, {"retry_after": 30})
            request_mock = MagicMock()
            request_mock.client.host = "1.2.3.4"
            with pytest.raises(HTTPException) as exc:
                check_profile_rate_limit(request_mock)
        assert exc.value.status_code == 429

    def test_consent_check_uses_row_tuple_format(self, parent_session, mock_deps, mock_db):
        """Consent row returned as tuple (not dict) is handled correctly."""
        from api.routes.profiles import create_profile, CreateProfileRequest
        from datetime import date

        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()

        # Return consent as tuple (index 0 = is_active)
        mock_db.execute_query.return_value = [(1,)]  # tuple format
        mock_db.execute_write.return_value = None

        profile = _make_profile()
        with patch("api.routes.profiles.ProfileManager") as PM:
            PM.return_value.create_profile.return_value = profile

            request = CreateProfileRequest(
                parent_id="a" * 32,
                name="Tuplechild",
                birthdate=birthdate,
                grade_level="5th",
            )
            result = create_profile(request, parent_session, rate_limit_info={})

        assert result["age_verification"]["has_parental_consent"] is True


# --------------------------------------------------------------------------
# CreateProfileRequest / UpdateProfileRequest — Pydantic validation
# --------------------------------------------------------------------------

class TestProfileRequestValidation:

    def test_create_request_invalid_grade_level(self):
        """Invalid grade_level is rejected by Pydantic validator."""
        import pydantic
        from api.routes.profiles import CreateProfileRequest

        with pytest.raises(pydantic.ValidationError):
            CreateProfileRequest(
                parent_id="a" * 32,
                name="Tommy",
                age=10,
                grade_level="invalid_grade",
            )

    def test_create_request_invalid_model_role(self):
        """Invalid model_role is rejected by Pydantic validator."""
        import pydantic
        from api.routes.profiles import CreateProfileRequest

        with pytest.raises(pydantic.ValidationError):
            CreateProfileRequest(
                parent_id="a" * 32,
                name="Tommy",
                age=10,
                grade_level="5th",
                model_role="superteacher",
            )

    def test_create_request_name_too_long(self):
        """Name above MAX_NAME_LENGTH is rejected."""
        import pydantic
        from api.routes.profiles import CreateProfileRequest
        from utils.input_validation import MAX_NAME_LENGTH

        with pytest.raises(pydantic.ValidationError):
            CreateProfileRequest(
                parent_id="a" * 32,
                name="X" * (MAX_NAME_LENGTH + 1),  # too long
                age=10,
                grade_level="5th",
            )

    def test_update_request_invalid_grade_level(self):
        """UpdateProfileRequest rejects invalid grade_level."""
        import pydantic
        from api.routes.profiles import UpdateProfileRequest

        with pytest.raises(pydantic.ValidationError):
            UpdateProfileRequest(grade_level="invalid_grade")

    def test_update_request_invalid_model_role(self):
        """UpdateProfileRequest rejects invalid model_role."""
        import pydantic
        from api.routes.profiles import UpdateProfileRequest

        with pytest.raises(pydantic.ValidationError):
            UpdateProfileRequest(model_role="wizard")

    def test_update_request_all_none_is_valid(self):
        """UpdateProfileRequest with all None fields is valid."""
        from api.routes.profiles import UpdateProfileRequest

        req = UpdateProfileRequest()
        assert req.name is None
        assert req.age is None
        assert req.grade_level is None
        assert req.model_role is None


# --------------------------------------------------------------------------
# check_profile_rate_limit — no client host path
# --------------------------------------------------------------------------

class TestCheckProfileRateLimit:

    def test_no_client_uses_unknown_identifier(self):
        """When request.client is None, identifier defaults to 'unknown'."""
        from api.routes.profiles import check_profile_rate_limit
        from unittest.mock import MagicMock

        with patch("api.routes.profiles.rate_limiter") as rl:
            rl.check_rate_limit.return_value = (True, {"remaining": 19})
            request_mock = MagicMock()
            request_mock.client = None  # no client
            result = check_profile_rate_limit(request_mock)

        rl.check_rate_limit.assert_called_once()
        call_kwargs = rl.check_rate_limit.call_args
        assert call_kwargs[1]["identifier"] == "unknown" or call_kwargs[0][0] == "unknown"

    def test_rate_limit_info_non_dict_uses_default_retry_after(self):
        """When info is not a dict, retry_after defaults to 60."""
        from api.routes.profiles import check_profile_rate_limit
        from unittest.mock import MagicMock

        with patch("api.routes.profiles.rate_limiter") as rl:
            # info is a plain string, not a dict
            rl.check_rate_limit.return_value = (False, "exceeded")
            request_mock = MagicMock()
            request_mock.client.host = "9.9.9.9"
            with pytest.raises(HTTPException) as exc:
                check_profile_rate_limit(request_mock)
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "60"
