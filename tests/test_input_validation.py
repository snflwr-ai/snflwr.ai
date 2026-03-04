"""
Tests for Input Validation Utilities

Ensures all input validators work correctly for security hardening.
"""

import pytest
from utils.input_validation import (
    validate_profile_id,
    validate_parent_id,
    validate_session_id,
    validate_name,
    validate_message,
    validate_age,
    validate_grade_level,
    validate_model_role,
    sanitize_string,
    MIN_MESSAGE_LENGTH,
    MAX_MESSAGE_LENGTH,
    MIN_AGE,
    MAX_AGE,
)


class TestProfileIdValidation:
    """Test profile ID validation"""

    def test_valid_profile_id(self):
        """Valid 32-character hex ID should pass"""
        is_valid, error = validate_profile_id("a" * 32)
        assert is_valid is True
        assert error is None

    @pytest.mark.parametrize("bad_id,expected_error", [
        ("a" * 31, "Invalid"),
        ("a" * 33, "Invalid"),
        ("A" * 32, "Invalid"),
        ("a" * 30 + "!@", "Invalid"),
        ("", "required"),
        (None, None),  # None should fail, error message varies
    ])
    def test_invalid_profile_ids(self, bad_id, expected_error):
        """Invalid profile IDs should fail validation"""
        is_valid, error = validate_profile_id(bad_id)
        assert is_valid is False
        if expected_error:
            assert expected_error.lower() in error.lower()


class TestSessionIdValidation:
    """Test session ID validation"""

    @pytest.mark.parametrize("session_id", ["a" * 32, "b" * 64])
    def test_valid_session_ids(self, session_id):
        """32 and 64-character hex session IDs should pass"""
        is_valid, error = validate_session_id(session_id)
        assert is_valid is True
        assert error is None

    def test_invalid_session_id_wrong_length(self):
        """Wrong length should fail"""
        is_valid, error = validate_session_id("a" * 50)
        assert is_valid is False


class TestNameValidation:
    """Test name field validation"""

    @pytest.mark.parametrize("name", ["John Doe", "Mary-Jane", "O'Connor"])
    def test_valid_names(self, name):
        """Names with letters, hyphens, and apostrophes should pass"""
        is_valid, error = validate_name(name)
        assert is_valid is True

    def test_empty_name_fails(self):
        """Empty name should fail"""
        is_valid, error = validate_name("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_name_too_long(self):
        """Name over 100 chars should fail"""
        is_valid, error = validate_name("A" * 101)
        assert is_valid is False

    def test_name_with_script_injection(self):
        """Name with HTML/script should fail"""
        is_valid, error = validate_name("John<script>")
        assert is_valid is False
        assert "invalid characters" in error.lower()


class TestMessageValidation:
    """Test message content validation"""

    def test_valid_message(self):
        """Valid message should pass"""
        is_valid, error = validate_message("Hello, how are you?")
        assert is_valid is True

    @pytest.mark.parametrize("bad_msg", ["", "   "])
    def test_empty_or_whitespace_message_fails(self, bad_msg):
        """Empty/whitespace-only messages should fail"""
        is_valid, error = validate_message(bad_msg)
        assert is_valid is False

    def test_message_too_long(self):
        """Message over max length should fail"""
        is_valid, error = validate_message("A" * (MAX_MESSAGE_LENGTH + 1))
        assert is_valid is False

    def test_message_at_max_length(self):
        """Message at exactly max length should pass"""
        is_valid, error = validate_message("A" * MAX_MESSAGE_LENGTH)
        assert is_valid is True


class TestAgeValidation:
    """Test age validation for COPPA compliance"""

    def test_valid_age(self):
        """Valid age should pass"""
        is_valid, error = validate_age(10)
        assert is_valid is True

    def test_age_too_young(self):
        """Age below minimum should fail"""
        is_valid, error = validate_age(MIN_AGE - 1)
        assert is_valid is False
        assert str(MIN_AGE) in error

    def test_age_too_old(self):
        """Age above maximum should fail"""
        is_valid, error = validate_age(MAX_AGE + 1)
        assert is_valid is False

    def test_age_none(self):
        """None age should fail"""
        is_valid, error = validate_age(None)
        assert is_valid is False


class TestGradeLevelValidation:
    """Test grade level validation"""

    def test_valid_grade_levels(self):
        """All valid grade levels should pass"""
        valid_grades = ['pre-k', 'k', '1', '5', '12', 'elementary', 'high', 'college']
        for grade in valid_grades:
            is_valid, error = validate_grade_level(grade)
            assert is_valid is True, f"Grade {grade} should be valid"

    def test_invalid_grade_level(self):
        """Invalid grade level should fail"""
        is_valid, error = validate_grade_level("invalid_grade")
        assert is_valid is False

    def test_empty_grade_level(self):
        """Empty grade level should fail"""
        is_valid, error = validate_grade_level("")
        assert is_valid is False


class TestModelRoleValidation:
    """Test model role validation"""

    def test_valid_model_roles(self):
        """All valid model roles should pass"""
        valid_roles = ['student', 'tutor', 'teacher', 'assistant', 'researcher']
        for role in valid_roles:
            is_valid, error = validate_model_role(role)
            assert is_valid is True, f"Role {role} should be valid"

    def test_invalid_model_role(self):
        """Invalid model role should fail"""
        is_valid, error = validate_model_role("admin")
        assert is_valid is False


class TestSanitizeString:
    """Test string sanitization"""

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace"""
        assert sanitize_string("  hello  ") == "hello"

    def test_truncates_long_string(self):
        """Should truncate to max length"""
        assert len(sanitize_string("A" * 100, max_length=50)) == 50

    def test_removes_null_bytes(self):
        """Should remove null bytes"""
        assert sanitize_string("hello\x00world") == "helloworld"

    @pytest.mark.parametrize("input_val,expected", [("", ""), (None, "")])
    def test_handles_empty_and_none(self, input_val, expected):
        """Should handle empty string and None"""
        assert sanitize_string(input_val) == expected
