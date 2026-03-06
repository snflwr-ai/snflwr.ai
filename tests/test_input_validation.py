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

    def test_non_string_value_converted(self):
        """Non-string value should be converted to string"""
        result = sanitize_string(12345)
        assert result == "12345"


# ==============================================================================
# Additional coverage tests
# ==============================================================================


class TestProfileIdNonString:
    """Cover line 81: non-string profile_id"""

    def test_non_string_profile_id(self):
        """Integer profile_id should fail with 'must be a string'"""
        is_valid, error = validate_profile_id(12345)
        assert is_valid is False
        assert "must be a string" in error.lower()


class TestParentIdValidation:
    """Cover lines 103, 106, 109-110 for validate_parent_id"""

    def test_valid_hex_parent_id(self):
        """Valid 32-char hex parent ID should pass"""
        is_valid, error = validate_parent_id("a" * 32)
        assert is_valid is True
        assert error is None

    def test_valid_hyphenated_parent_id(self):
        """Valid hyphenated UUID parent ID should pass"""
        is_valid, error = validate_parent_id("abcdef01-2345-6789-abcd-ef0123456789")
        assert is_valid is True
        assert error is None

    def test_empty_parent_id(self):
        """Empty parent_id should fail with 'required'"""
        is_valid, error = validate_parent_id("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_non_string_parent_id(self):
        """Non-string parent_id should fail with 'must be a string'"""
        is_valid, error = validate_parent_id(12345)
        assert is_valid is False
        assert "must be a string" in error.lower()

    def test_invalid_format_parent_id(self):
        """Invalid format should fail with 'invalid' message"""
        is_valid, error = validate_parent_id("not-a-valid-uuid")
        assert is_valid is False
        assert "invalid" in error.lower()


class TestSessionIdEdgeCases:
    """Cover lines 126, 129 for validate_session_id"""

    def test_empty_session_id(self):
        """Empty session_id should fail with 'required'"""
        is_valid, error = validate_session_id("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_non_string_session_id(self):
        """Non-string session_id should fail with 'must be a string'"""
        is_valid, error = validate_session_id(12345)
        assert is_valid is False
        assert "must be a string" in error.lower()

    def test_valid_hyphenated_session_id(self):
        """Hyphenated UUID session ID should pass"""
        is_valid, error = validate_session_id("abcdef01-2345-6789-abcd-ef0123456789")
        assert is_valid is True
        assert error is None


class TestNameEdgeCases:
    """Cover lines 154, 160 for validate_name"""

    def test_non_string_name(self):
        """Non-string name should fail with 'must be a string'"""
        is_valid, error = validate_name(12345)
        assert is_valid is False
        assert "must be a string" in error.lower()

    def test_whitespace_only_name(self):
        """Whitespace-only name should fail (strips to empty, too short)"""
        is_valid, error = validate_name("   ")
        assert is_valid is False
        assert "at least" in error.lower()


class TestMessageEdgeCases:
    """Cover line 185 for validate_message"""

    def test_non_string_message(self):
        """Non-string message should fail with 'must be a string'"""
        is_valid, error = validate_message(12345)
        assert is_valid is False
        assert "must be a string" in error.lower()


class TestAgeEdgeCases:
    """Cover line 213 for validate_age"""

    def test_non_integer_age(self):
        """String age should fail with 'must be an integer'"""
        is_valid, error = validate_age("10")
        assert is_valid is False
        assert "must be an integer" in error.lower()

    def test_float_age(self):
        """Float age should fail with 'must be an integer'"""
        is_valid, error = validate_age(10.5)
        assert is_valid is False
        assert "must be an integer" in error.lower()


class TestGradeLevelEdgeCases:
    """Cover line 238 for validate_grade_level"""

    def test_non_string_grade_level(self):
        """Non-string grade_level should fail with 'must be a string'"""
        is_valid, error = validate_grade_level(5)
        assert is_valid is False
        assert "must be a string" in error.lower()


class TestModelRoleEdgeCases:
    """Cover lines 257, 260 for validate_model_role"""

    def test_empty_model_role(self):
        """Empty model_role should fail with 'required'"""
        is_valid, error = validate_model_role("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_non_string_model_role(self):
        """Non-string model_role should fail with 'must be a string'"""
        is_valid, error = validate_model_role(123)
        assert is_valid is False
        assert "must be a string" in error.lower()


class TestCreateIdValidator:
    """Cover lines 307-313 for create_id_validator"""

    def test_valid_id_passes(self):
        """Valid hex ID should pass the created validator"""
        from pydantic import BaseModel
        from utils.input_validation import create_id_validator

        class TestModel(BaseModel):
            profile_id: str
            _validate_profile_id = create_id_validator('profile_id')

        obj = TestModel(profile_id="a" * 32)
        assert obj.profile_id == "a" * 32

    def test_empty_id_raises(self):
        """Empty ID should raise ValueError"""
        from pydantic import BaseModel, ValidationError
        from utils.input_validation import create_id_validator

        class TestModel(BaseModel):
            profile_id: str
            _validate_profile_id = create_id_validator('profile_id')

        with pytest.raises(ValidationError, match="required"):
            TestModel(profile_id="")

    def test_invalid_format_id_raises(self):
        """Invalid format ID should raise ValueError"""
        from pydantic import BaseModel, ValidationError
        from utils.input_validation import create_id_validator

        class TestModel(BaseModel):
            profile_id: str
            _validate_profile_id = create_id_validator('profile_id')

        with pytest.raises(ValidationError, match="Invalid"):
            TestModel(profile_id="not-valid")


class TestCreateNameValidator:
    """Cover lines 316-327 for create_name_validator"""

    def test_valid_name_passes(self):
        """Valid name should pass the created validator"""
        from pydantic import BaseModel
        from utils.input_validation import create_name_validator

        class TestModel(BaseModel):
            child_name: str
            _validate_name = create_name_validator('child_name')

        obj = TestModel(child_name="Alice")
        assert obj.child_name == "Alice"

    def test_empty_name_raises(self):
        """Empty name should raise ValueError"""
        from pydantic import BaseModel, ValidationError
        from utils.input_validation import create_name_validator

        class TestModel(BaseModel):
            child_name: str
            _validate_name = create_name_validator('child_name')

        with pytest.raises(ValidationError, match="required"):
            TestModel(child_name="")

    def test_name_too_long_raises(self):
        """Name exceeding max length should raise ValueError"""
        from pydantic import BaseModel, ValidationError
        from utils.input_validation import create_name_validator

        class TestModel(BaseModel):
            child_name: str
            _validate_name = create_name_validator('child_name')

        with pytest.raises(ValidationError, match="characters"):
            TestModel(child_name="A" * 101)

    def test_name_with_invalid_chars_raises(self):
        """Name with invalid characters should raise ValueError"""
        from pydantic import BaseModel, ValidationError
        from utils.input_validation import create_name_validator

        class TestModel(BaseModel):
            child_name: str
            _validate_name = create_name_validator('child_name')

        with pytest.raises(ValidationError, match="invalid characters"):
            TestModel(child_name="Alice<script>")
