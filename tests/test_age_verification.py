"""
Tests for core/age_verification.py — COPPA Compliance

Covers:
    - calculate_age_from_birthdate: correct age calculation, birthday edge cases
    - validate_birthdate: K-12 range validation, future dates, invalid formats
    - check_coppa_compliance: under-13 consent requirement, 13+ no consent needed
    - generate_consent_verification_token / verify_consent_token: token round-trip
    - AgeVerificationManager: verify_age_from_birthdate, log_parental_consent,
      update_profile_consent_status, revoke_parental_consent, get_consent_status
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.age_verification import (
    AgeVerificationError,
    AgeVerificationManager,
    AgeVerificationResult,
    COPPA_AGE_THRESHOLD,
    ParentalConsentRequired,
    calculate_age_from_birthdate,
    check_coppa_compliance,
    generate_consent_verification_token,
    validate_birthdate,
    verify_consent_token,
)


# ==========================================================================
# calculate_age_from_birthdate
# ==========================================================================

class TestCalculateAge:
    """Age calculation from birthdate."""

    def test_exact_age(self):
        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()
        assert calculate_age_from_birthdate(birthdate) == 10

    def test_birthday_not_yet_this_year(self):
        """If birthday hasn't occurred yet this year, age is one less."""
        today = date.today()
        # Pick a date that's tomorrow (or next month) in the current year
        future_birthday = today + timedelta(days=30)
        birthdate = future_birthday.replace(year=future_birthday.year - 10).isoformat()
        assert calculate_age_from_birthdate(birthdate) == 9

    def test_birthday_already_passed(self):
        today = date.today()
        past_birthday = today - timedelta(days=30)
        birthdate = past_birthday.replace(year=past_birthday.year - 10).isoformat()
        assert calculate_age_from_birthdate(birthdate) == 10

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid birthdate format"):
            calculate_age_from_birthdate("not-a-date")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            calculate_age_from_birthdate("")


# ==========================================================================
# validate_birthdate
# ==========================================================================

class TestValidateBirthdate:
    """Birthdate validation for K-12 range."""

    def test_valid_birthdate(self):
        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()
        is_valid, error = validate_birthdate(birthdate)
        assert is_valid is True
        assert error is None

    def test_too_young(self):
        today = date.today()
        birthdate = today.replace(year=today.year - 3).isoformat()
        is_valid, error = validate_birthdate(birthdate, min_age=5)
        assert is_valid is False
        assert "at least 5" in error

    def test_too_old(self):
        today = date.today()
        birthdate = today.replace(year=today.year - 25).isoformat()
        is_valid, error = validate_birthdate(birthdate, max_age=18)
        assert is_valid is False
        assert "18 or younger" in error

    def test_future_birthdate(self):
        """A birthdate far in the future is rejected (age < min_age)."""
        future = (date.today() + timedelta(days=365)).isoformat()
        is_valid, error = validate_birthdate(future)
        assert is_valid is False
        assert error is not None

    def test_invalid_format(self):
        is_valid, error = validate_birthdate("99/99/9999")
        assert is_valid is False

    def test_custom_range(self):
        today = date.today()
        birthdate = today.replace(year=today.year - 7).isoformat()
        is_valid, _ = validate_birthdate(birthdate, min_age=6, max_age=14)
        assert is_valid is True


# ==========================================================================
# check_coppa_compliance
# ==========================================================================

class TestCoppaCompliance:
    """COPPA compliance checks."""

    def test_under_13_no_consent_not_compliant(self):
        result = check_coppa_compliance(age=10, has_parental_consent=False)
        assert result.is_under_13 is True
        assert result.requires_parental_consent is True
        assert result.is_compliant is False
        assert result.error_message is not None

    def test_under_13_with_consent_compliant(self):
        result = check_coppa_compliance(age=10, has_parental_consent=True)
        assert result.is_under_13 is True
        assert result.requires_parental_consent is True
        assert result.is_compliant is True
        assert result.error_message is None

    def test_age_13_no_consent_compliant(self):
        result = check_coppa_compliance(age=13, has_parental_consent=False)
        assert result.is_under_13 is False
        assert result.requires_parental_consent is False
        assert result.is_compliant is True

    def test_age_17_no_consent_compliant(self):
        result = check_coppa_compliance(age=17, has_parental_consent=False)
        assert result.is_compliant is True

    def test_threshold_boundary(self):
        """Exactly 12 should require consent, 13 should not."""
        r12 = check_coppa_compliance(age=12, has_parental_consent=False)
        r13 = check_coppa_compliance(age=13, has_parental_consent=False)
        assert r12.is_compliant is False
        assert r13.is_compliant is True

    def test_result_has_verification_date(self):
        result = check_coppa_compliance(age=10, has_parental_consent=True)
        assert result.verification_date is not None
        # Should be a valid ISO timestamp
        datetime.fromisoformat(result.verification_date)

    def test_coppa_age_threshold_constant(self):
        assert COPPA_AGE_THRESHOLD == 13


# ==========================================================================
# Consent Tokens
# ==========================================================================

class TestConsentTokens:
    """Consent verification token generation and verification."""

    def test_round_trip(self):
        token, token_hash = generate_consent_verification_token("parent1", "profile1")
        assert verify_consent_token(token, token_hash, "parent1", "profile1") is True

    def test_wrong_token_fails(self):
        _, token_hash = generate_consent_verification_token("parent1", "profile1")
        assert verify_consent_token("wrong-token", token_hash, "parent1", "profile1") is False

    def test_tokens_are_unique(self):
        t1, h1 = generate_consent_verification_token("p1", "pr1")
        t2, h2 = generate_consent_verification_token("p1", "pr1")
        assert t1 != t2
        assert h1 != h2

    def test_token_length(self):
        token, _ = generate_consent_verification_token("p", "pr")
        # token_urlsafe(32) produces ~43 characters
        assert len(token) >= 32

    def test_hash_is_hex(self):
        _, token_hash = generate_consent_verification_token("p", "pr")
        # SHA-256 hex digest is 64 characters
        assert len(token_hash) == 64
        int(token_hash, 16)  # Should not raise


# ==========================================================================
# AgeVerificationManager
# ==========================================================================

class TestAgeVerificationManager:
    """AgeVerificationManager with mocked database."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def manager(self, mock_db):
        return AgeVerificationManager(mock_db)

    def test_verify_valid_birthdate(self, manager):
        today = date.today()
        birthdate = today.replace(year=today.year - 10).isoformat()
        result = manager.verify_age_from_birthdate(birthdate, has_parental_consent=True)
        assert result.is_compliant is True
        assert result.age == 10

    def test_verify_invalid_birthdate_raises(self, manager):
        with pytest.raises(AgeVerificationError):
            manager.verify_age_from_birthdate("not-a-date")

    def test_verify_too_young_raises(self, manager):
        today = date.today()
        birthdate = today.replace(year=today.year - 3).isoformat()
        with pytest.raises(AgeVerificationError, match="at least 5"):
            manager.verify_age_from_birthdate(birthdate)

    def test_verify_under_13_no_consent_not_compliant(self, manager):
        today = date.today()
        birthdate = today.replace(year=today.year - 8).isoformat()
        result = manager.verify_age_from_birthdate(birthdate, has_parental_consent=False)
        assert result.is_compliant is False

    def test_log_parental_consent(self, manager, mock_db):
        consent_id = manager.log_parental_consent(
            profile_id="prof1",
            parent_id="par1",
            consent_method="email_verification",
            ip_address="127.0.0.1",
            user_agent="test-browser",
        )
        assert consent_id is not None
        mock_db.execute_write.assert_called_once()
        args = mock_db.execute_write.call_args
        assert "INSERT INTO parental_consent_log" in args[0][0]

    def test_log_parental_consent_db_error(self, manager, mock_db):
        import sqlite3
        mock_db.execute_write.side_effect = sqlite3.Error("db fail")
        with pytest.raises(sqlite3.Error):
            manager.log_parental_consent("prof1", "par1", "email")

    def test_update_profile_consent_status(self, manager, mock_db):
        result = manager.update_profile_consent_status(
            profile_id="prof1",
            consent_given=True,
            consent_date=datetime.now(timezone.utc).isoformat(),
            consent_method="email_verification",
        )
        assert result is True
        mock_db.execute_write.assert_called_once()

    def test_update_profile_consent_db_error(self, manager, mock_db):
        import sqlite3
        mock_db.execute_write.side_effect = sqlite3.Error("db fail")
        result = manager.update_profile_consent_status(
            "prof1", False, "2024-01-01", "manual"
        )
        assert result is False

    def test_revoke_parental_consent(self, manager, mock_db):
        result = manager.revoke_parental_consent("prof1", "par1", reason="Testing")
        assert result is True
        # Three writes: deactivate prior consent + insert revocation log + update profile
        assert mock_db.execute_write.call_count == 3

    def test_revoke_consent_db_error(self, manager, mock_db):
        import sqlite3
        mock_db.execute_write.side_effect = sqlite3.Error("db fail")
        result = manager.revoke_parental_consent("prof1", "par1")
        assert result is False

    def test_get_consent_status_found(self, manager, mock_db):
        mock_db.execute_query.return_value = [{
            'parental_consent_given': 1,
            'parental_consent_date': '2024-01-01T00:00:00',
            'parental_consent_method': 'email',
            'coppa_verified': 1,
            'age': 10,
            'birthdate': '2014-01-01',
        }]
        status = manager.get_consent_status("prof1")
        assert status['consent_given'] is True
        assert status['coppa_verified'] is True
        assert status['requires_consent'] is True

    def test_get_consent_status_not_found(self, manager, mock_db):
        mock_db.execute_query.return_value = []
        status = manager.get_consent_status("missing")
        assert status == {"error": "Profile not found"}

    def test_get_consent_status_tuple_row(self, manager, mock_db):
        """Test tuple-style row access (non-dict)."""
        mock_db.execute_query.return_value = [(1, '2024-01-01', 'email', 1, 10, '2014-01-01')]
        status = manager.get_consent_status("prof1")
        assert status['consent_given'] is True

    def test_get_consent_status_db_error(self, manager, mock_db):
        import sqlite3
        mock_db.execute_query.side_effect = sqlite3.Error("db fail")
        status = manager.get_consent_status("prof1")
        assert "error" in status

    def test_get_consent_status_age_13_plus_no_consent_required(self, manager, mock_db):
        mock_db.execute_query.return_value = [{
            'parental_consent_given': 0,
            'parental_consent_date': None,
            'parental_consent_method': None,
            'coppa_verified': 0,
            'age': 15,
            'birthdate': '2009-01-01',
        }]
        status = manager.get_consent_status("prof1")
        assert status['requires_consent'] is False


# ==========================================================================
# AgeVerificationResult dataclass
# ==========================================================================

class TestAgeVerificationResult:
    """AgeVerificationResult dataclass."""

    def test_fields(self):
        result = AgeVerificationResult(
            age=10,
            is_under_13=True,
            requires_parental_consent=True,
            has_parental_consent=False,
            is_compliant=False,
            verification_date="2024-01-01T00:00:00",
            error_message="test error",
        )
        assert result.age == 10
        assert result.error_message == "test error"

    def test_default_error_message(self):
        result = AgeVerificationResult(
            age=14,
            is_under_13=False,
            requires_parental_consent=False,
            has_parental_consent=False,
            is_compliant=True,
            verification_date="2024-01-01T00:00:00",
        )
        assert result.error_message is None


class TestExceptions:
    """Custom exception classes."""

    def test_age_verification_error(self):
        with pytest.raises(AgeVerificationError):
            raise AgeVerificationError("test")

    def test_parental_consent_required(self):
        with pytest.raises(ParentalConsentRequired):
            raise ParentalConsentRequired("consent needed")
