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

    def test_prohibited_keywords_defined(self):
        """Test prohibited keywords are defined for all categories"""
        keywords = safety_config.PROHIBITED_KEYWORDS

        required_categories = [
            'violence', 'self_harm', 'sexual', 'drugs',
            'personal_info', 'bullying', 'dangerous_activity'
        ]

        for category in required_categories:
            assert category in keywords
            assert len(keywords[category]) > 0

    def test_retention_policy_summary(self):
        """Test retention policy summary is available"""
        policy = safety_config.get_retention_policy()

        assert 'safety_incidents' in policy
        assert 'audit_logs' in policy
        assert 'sessions' in policy
        assert 'conversations' in policy
        assert 'analytics' in policy
        assert 'compliance' in policy

        # Check compliance section
        compliance = policy['compliance']
        assert compliance['framework'] == 'COPPA/FERPA'
        assert compliance['data_minimization'] is True


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

        assert 'retention_policy' in summary
        assert 'data_volumes' in summary
        assert 'cleanup_enabled' in summary
        assert 'cleanup_schedule' in summary

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
        assert system_config.LOG_LEVEL in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        assert system_config.LOG_MAX_SIZE_MB > 0
        assert system_config.LOG_BACKUP_COUNT > 0

    def test_system_info_available(self):
        """Test system info can be retrieved"""
        info = system_config.get_info()

        assert 'application' in info
        assert 'version' in info
        assert 'platform' in info
        assert 'app_data_dir' in info


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

        assert 'elementary' in filter_levels
        assert 'middle' in filter_levels
        assert 'high' in filter_levels

        # Elementary should have maximum strictness
        elementary = filter_levels['elementary']
        assert elementary['strictness'] == 'maximum'
        assert elementary['block_all_external_links'] is True


# Pytest configuration
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
