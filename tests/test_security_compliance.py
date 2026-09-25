# tests/test_security_compliance.py
"""
Security Compliance Test Suite
Tests for encryption, data retention, and COPPA compliance
"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Import modules to test
from config import system_config, safety_config
from utils.data_retention import DataRetentionManager

# NOTE: Encryption tests live in test_encryption.py


class TestConfigurationCompliance:
    """Test COPPA/FERPA compliance configuration"""

    def test_retention_periods_defined(self):
        """Test all retention periods are properly defined"""
        assert safety_config.SAFETY_LOG_RETENTION_DAYS > 0
        assert safety_config.AUDIT_LOG_RETENTION_DAYS > 0
        assert safety_config.SESSION_RETENTION_DAYS > 0
        assert safety_config.CONVERSATION_RETENTION_DAYS > 0
        assert safety_config.ANALYTICS_RETENTION_DAYS > 0

    def test_coppa_compliance_settings(self):
        """Test COPPA compliance settings are enabled"""
        assert safety_config.REQUIRE_PARENT_CONSENT is True
        assert safety_config.AGE_VERIFICATION_REQUIRED is True
        assert safety_config.SHARE_DATA_WITH_THIRD_PARTIES is False
        assert safety_config.ALLOW_DATA_EXPORT is True
        assert safety_config.ALLOW_DATA_DELETION is True

    def test_encryption_enabled(self):
        """Test encryption is enabled for sensitive data"""
        assert safety_config.ENCRYPT_INCIDENT_LOGS is True
        assert safety_config.ENCRYPT_PERSONAL_DATA is True

    def test_audit_logging_enabled(self):
        """Test audit logging is enabled"""
        assert safety_config.ENABLE_AUDIT_LOGGING is True
        assert safety_config.AUDIT_LOG_ALL_ACCESS is True
        assert safety_config.AUDIT_LOG_MODIFICATIONS is True
        assert safety_config.AUDIT_LOG_DELETIONS is True

    def test_parent_controls_enabled(self):
        """Test parent controls are properly configured"""
        assert safety_config.PARENT_FULL_CONVERSATION_ACCESS is True
        assert safety_config.PARENT_CAN_DELETE_CONVERSATIONS is True
        assert safety_config.PARENT_CAN_EXPORT_DATA is True

    def test_security_thresholds(self):
        """Test security alert thresholds are configured"""
        assert safety_config.ALERT_THRESHOLD_CRITICAL >= 1
        assert safety_config.ALERT_THRESHOLD_MAJOR >= 1
        assert safety_config.ALERT_THRESHOLD_MINOR >= 1

    def test_every_required_category_has_a_BLOCKING_MECHANISM(self):
        """Every required category must be COVERED — by a keyword list or a frame.

        ⚠️ This asserted `len(keywords[category]) > 0` until 2026-09-25, i.e.
        that every category has a non-empty keyword LIST. That is not the
        property that matters; the property is that every category has a
        working blocking MECHANISM.

        The difference stopped being academic when `bullying`'s bare keywords
        (`bully`, `bullying`, `harass`, `threat`) were found blocking the child
        they exist to protect — measured live: "someone at school keeps
        bullying me and i dont know what to do" was refused as BULLYING/MAJOR
        and answered "Let's keep our conversation positive and respectful."

        Intent needs a FRAME (subject/modal x verb family), which a
        word-boundary keyword list cannot express, so that category's list is
        now deliberately EMPTY and its coverage lives in the pattern matcher's
        frame table. Under the old assertion that correct fix read as a
        compliance failure — the test was pinning the defect in place, which is
        `tests-written-from-observed-behaviour-pin-the-defect`.

        ⚠️ The fix is NOT a placeholder keyword to make this green. That would
        restore the harm to satisfy a test.
        """
        import inspect

        from safety.pipeline import pattern_matcher

        keywords = safety_config.PROHIBITED_KEYWORDS
        frame_src = inspect.getsource(
            pattern_matcher._PatternMatcher._build_danger_phrases
        )

        required_categories = [
            "violence",
            "self_harm",
            "sexual",
            "drugs",
            "personal_info",
            "bullying",
            "dangerous_activity",
        ]
        # category -> the Category enum member name used in the frame table
        frame_names = {
            "violence": "VIOLENCE",
            "self_harm": "SELF_HARM",
            "sexual": "SEXUAL",
            "drugs": "DRUGS",
            "personal_info": "PII",
            "bullying": "BULLYING",
            "dangerous_activity": "WEAPONS",
        }

        for category in required_categories:
            assert category in keywords, f"{category} missing from the config"
            by_list = len(keywords[category]) > 0
            by_frame = f"Category.{frame_names[category]}" in frame_src
            assert by_list or by_frame, (
                f"{category!r} has NO blocking mechanism: its keyword list is "
                f"empty and no frame in the danger table carries "
                f"Category.{frame_names[category]}"
            )

    def test_bullying_coverage_is_the_FRAME_and_not_a_keyword_list(self):
        """Pin the mechanism for the one category where a list is WRONG.

        Emptying the list must never be able to mean "no coverage", and putting
        keywords back must never be able to look like a fix. Bare words block
        the child reporting it; a phrase list missed 0 of 13 intent phrasings
        just outside it (measured by peer review).
        """
        import inspect

        from safety.pipeline import pattern_matcher

        assert safety_config.PROHIBITED_KEYWORDS["bullying"] == [], (
            "the bullying keyword list is populated again — bare words block "
            "the child reporting bullying, and a phrase list misses the intent "
            "phrasings around it"
        )
        frame_src = inspect.getsource(
            pattern_matcher._PatternMatcher._build_danger_phrases
        )
        assert "Category.BULLYING" in frame_src, (
            "the bullying list is empty AND no frame covers it — the category "
            "has no blocking mechanism at all"
        )

    def test_retention_policy_summary(self):
        """Test retention policy summary is available"""
        policy = safety_config.get_retention_policy()

        assert "safety_incidents" in policy
        assert "audit_logs" in policy
        assert "sessions" in policy
        assert "conversations" in policy
        assert "analytics" in policy
        assert "compliance" in policy

        # Check compliance section
        compliance = policy["compliance"]
        assert compliance["framework"] == "COPPA/FERPA"
        assert compliance["data_minimization"] is True


class TestDataRetention:
    """Test data retention functionality"""

    def test_data_retention_manager_initialization(self):
        """Test data retention manager initializes"""
        manager = DataRetentionManager()
        assert manager.db is not None
        assert manager.running is False

    def test_retention_summary_available(self):
        """Test retention summary can be retrieved"""
        manager = DataRetentionManager()
        summary = manager.get_retention_summary()

        assert "retention_policy" in summary
        assert "data_volumes" in summary
        assert "cleanup_enabled" in summary
        assert "cleanup_schedule" in summary

    def test_cleanup_configuration(self):
        """Test cleanup is properly configured"""
        assert safety_config.DATA_CLEANUP_ENABLED is True
        assert 0 <= safety_config.DATA_CLEANUP_HOUR <= 23


class TestSystemConfiguration:
    """Test system configuration"""

    def test_app_data_directory_exists(self):
        """Test app data directory is properly configured"""
        assert system_config.APP_DATA_DIR is not None
        assert isinstance(system_config.APP_DATA_DIR, Path)

    def test_database_configuration(self):
        """Test database configuration"""
        assert system_config.DB_PATH is not None
        assert system_config.DB_TIMEOUT > 0

    def test_logging_configuration(self):
        """Test logging is properly configured"""
        assert system_config.LOG_DIR is not None
        assert system_config.LOG_LEVEL in [
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        ]
        assert system_config.LOG_MAX_SIZE_MB > 0
        assert system_config.LOG_BACKUP_COUNT > 0

    def test_system_info_available(self):
        """Test system info can be retrieved"""
        info = system_config.get_info()

        assert "application" in info
        assert "version" in info
        assert "platform" in info
        assert "app_data_dir" in info


class TestSecurityFeatures:
    """Test security features"""

    def test_session_security_configured(self):
        """Test session security settings"""
        assert safety_config.SESSION_TIMEOUT_MINUTES > 0
        assert safety_config.MAX_FAILED_LOGIN_ATTEMPTS > 0
        assert safety_config.ACCOUNT_LOCKOUT_DURATION_MINUTES > 0

    def test_password_requirements(self):
        """Test password requirements are strong"""
        assert safety_config.PASSWORD_MIN_LENGTH >= 8
        assert safety_config.PASSWORD_REQUIRE_UPPERCASE is True
        assert safety_config.PASSWORD_REQUIRE_LOWERCASE is True
        assert safety_config.PASSWORD_REQUIRE_NUMBERS is True

    def test_grade_based_filtering_configured(self):
        """Test grade-based filtering is configured"""
        filter_levels = safety_config.FILTER_LEVELS

        assert "elementary" in filter_levels
        assert "middle" in filter_levels
        assert "high" in filter_levels

        # Elementary should have maximum strictness
        elementary = filter_levels["elementary"]
        assert elementary["strictness"] == "maximum"
        assert elementary["block_all_external_links"] is True


# Pytest configuration
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
