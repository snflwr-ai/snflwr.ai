#!/usr/bin/env python
"""
Environment Configuration Validator
Validates all required environment variables before server startup

Usage:
    python scripts/validate_env.py
    python scripts/validate_env.py --env production

Returns exit code 0 if valid, 1 if invalid
"""

import os
import sys
import argparse
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config


def print_header(text):
    """Print section header"""
    print(f"\n{'=' * 70}")
    print(text)
    print(f"{'=' * 70}\n")


def print_success(text):
    print(f"  [OK] {text}")


def print_warning(text):
    print(f"  [WARN] {text}")


def print_error(text):
    print(f"  [ERROR] {text}")


def validate_jwt_secret():
    """Validate JWT secret key"""
    issues = []

    _jwt_len = len(system_config.JWT_SECRET_KEY)
    _jwt_is_default = (system_config.JWT_SECRET_KEY == 'change-this-secret-key-in-production')

    if _jwt_is_default:
        issues.append("JWT_SECRET_KEY is using default value - CRITICAL SECURITY RISK!")

    if _jwt_len < 32:
        issues.append(f"JWT_SECRET_KEY is too short ({_jwt_len} chars, minimum 32)")

    return issues


def validate_smtp_config():
    """Validate SMTP configuration"""
    issues = []
    warnings = []

    if not system_config.SMTP_ENABLED:
        warnings.append("SMTP is disabled - parent email notifications will not work")
        return issues, warnings

    if not system_config.SMTP_HOST:
        issues.append("SMTP_HOST is not set")

    if not system_config.SMTP_PORT:
        issues.append("SMTP_PORT is not set")

    if not system_config.SMTP_FROM_EMAIL:
        issues.append("SMTP_FROM_EMAIL is not set")

    # Check for default/example values
    _smtp_email = system_config.SMTP_FROM_EMAIL.lower()
    _smtp_domain = _smtp_email.split('@', 1)[1] if '@' in _smtp_email else _smtp_email
    if _smtp_domain == 'example.com':
        warnings.append("SMTP_FROM_EMAIL contains 'example.com' - update with a real email address")

    _smtp_pw_is_example = system_config.SMTP_PASSWORD.startswith('SG.YOUR')
    _smtp_pw_is_set = bool(system_config.SMTP_PASSWORD)
    _smtp_user_is_set = bool(system_config.SMTP_USERNAME)

    if _smtp_pw_is_example:
        issues.append("SMTP_PASSWORD is using example value - update with real SendGrid API key")

    if not _smtp_user_is_set or not _smtp_pw_is_set:
        warnings.append("SMTP credentials not set - email sending may fail")

    return issues, warnings


def validate_encryption_key():
    """Validate encryption key"""
    issues = []

    try:
        encryption_key = os.getenv('ENCRYPTION_KEY', '')

        if not encryption_key:
            issues.append("ENCRYPTION_KEY is not set - parent emails cannot be encrypted")
        elif encryption_key.startswith('CHANGE_THIS'):
            issues.append("ENCRYPTION_KEY is using default value")
        elif len(encryption_key) < 40:
            issues.append(f"ENCRYPTION_KEY is too short ({len(encryption_key)} chars, should be 44 for Fernet)")

    except Exception as e:
        issues.append(f"Failed to validate ENCRYPTION_KEY: {e}")

    return issues


def validate_cors_origins():
    """Validate CORS configuration"""
    issues = []
    warnings = []

    origins = system_config.CORS_ORIGINS

    if not origins:
        warnings.append("CORS_ORIGINS is empty - API will not accept requests")

    for origin in origins:
        _parsed = urlparse(origin)
        _netloc = _parsed.netloc or _parsed.path
        if _netloc == 'localhost' or _netloc.startswith('localhost:'):
            warnings.append(f"CORS_ORIGINS contains localhost: {origin} - OK for dev, not for production")
        elif _netloc == 'yourdomain.com' or _netloc.endswith('.yourdomain.com'):
            issues.append(f"CORS_ORIGINS contains example domain: {origin}")

    return issues, warnings


def validate_database_config():
    """Validate database configuration"""
    issues = []
    warnings = []

    if system_config.DATABASE_TYPE == 'sqlite':
        warnings.append("Using SQLite - consider PostgreSQL for production at scale")

        # Check if database file exists
        if not system_config.DB_PATH.exists():
            warnings.append(f"Database file does not exist: {system_config.DB_PATH}")
            warnings.append("Run: python database/init_db.py")

    return issues, warnings


def validate_api_config():
    """Validate API server configuration"""
    issues = []
    warnings = []

    if system_config.API_RELOAD:
        warnings.append("API_RELOAD is enabled - should be false in production")

    if system_config.API_HOST == '0.0.0.0':
        warnings.append("API_HOST is 0.0.0.0 (all interfaces) - ensure firewall is configured")

    if system_config.LOG_LEVEL == 'DEBUG':
        warnings.append("LOG_LEVEL is DEBUG - consider INFO or WARNING for production")

    return issues, warnings


def test_smtp_connection():
    """Test SMTP connection if enabled"""
    if not system_config.SMTP_ENABLED:
        return []

    issues = []

    try:
        from core.email_service import email_service

        print(f"  Testing SMTP connection to {system_config.SMTP_HOST}:{system_config.SMTP_PORT}...")
        success, error = email_service.test_connection()

        if not success:
            issues.append(f"SMTP connection test failed: {error}")

    except Exception as e:
        issues.append(f"Failed to test SMTP connection: {e}")

    return issues


def main():
    """Main validation function"""
    parser = argparse.ArgumentParser(description='Validate snflwr.ai environment configuration')
    parser.add_argument('--env', choices=['development', 'staging', 'production'], help='Environment to validate')
    parser.add_argument('--test-smtp', action='store_true', help='Test SMTP connection')
    args = parser.parse_args()

    print_header("snflwr.ai - Environment Configuration Validator")

    environment = os.getenv('ENVIRONMENT', 'development')
    is_production = environment.lower() == 'production'

    print(f"Environment: {environment}")
    print(f"Config File: {system_config.APP_DATA_DIR}")
    print(f"Database: {system_config.DB_PATH}")

    all_issues = []
    all_warnings = []

    # 1. JWT Secret
    print_header("1. JWT Authentication")
    issues = validate_jwt_secret()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    else:
        _jwt_key_len = len(system_config.JWT_SECRET_KEY)
        print_success(f"JWT_SECRET_KEY is set ({_jwt_key_len} characters)")

    # 2. SMTP Configuration
    print_header("2. Email Notifications (SMTP)")
    issues, warnings = validate_smtp_config()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    if warnings:
        all_warnings.extend(warnings)
        for warning in warnings:
            print_warning(warning)
    if not issues and not warnings:
        print_success(f"SMTP configured: {system_config.SMTP_HOST}:{system_config.SMTP_PORT}")

    # Test SMTP connection if requested
    if args.test_smtp and system_config.SMTP_ENABLED:
        smtp_issues = test_smtp_connection()
        if smtp_issues:
            all_issues.extend(smtp_issues)
            for issue in smtp_issues:
                print_error(issue)
        else:
            print_success("SMTP connection test passed")

    # 3. Encryption
    print_header("3. Data Encryption")
    issues = validate_encryption_key()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    else:
        print_success("ENCRYPTION_KEY is set")

    # 4. CORS
    print_header("4. CORS Configuration")
    issues, warnings = validate_cors_origins()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    if warnings:
        all_warnings.extend(warnings)
        for warning in warnings:
            print_warning(warning)
    if not issues:
        print_success(f"CORS origins: {', '.join(system_config.CORS_ORIGINS)}")

    # 5. Database
    print_header("5. Database Configuration")
    issues, warnings = validate_database_config()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    if warnings:
        all_warnings.extend(warnings)
        for warning in warnings:
            print_warning(warning)
    if not issues:
        print_success(f"Database type: {system_config.DATABASE_TYPE}")

    # 6. API Server
    print_header("6. API Server Configuration")
    issues, warnings = validate_api_config()
    if issues:
        all_issues.extend(issues)
        for issue in issues:
            print_error(issue)
    if warnings:
        all_warnings.extend(warnings)
        for warning in warnings:
            print_warning(warning)
    if not issues:
        print_success(f"API server: {system_config.API_HOST}:{system_config.API_PORT}")

    # Summary
    print_header("Validation Summary")

    if all_issues:
        print(f"\n  [FAIL] VALIDATION FAILED")
        print(f"\n  Critical Issues: {len(all_issues)}")
        for issue in all_issues:
            print(f"    - {issue}")

        if is_production:
            print(f"\n  Server will NOT start in production with these issues!")

        print(f"\n  Warnings: {len(all_warnings)}")
        for warning in all_warnings:
            print(f"    - {warning}")

        return 1

    elif all_warnings:
        print(f"\n  [WARN] VALIDATION PASSED WITH WARNINGS")
        print(f"\n  Warnings: {len(all_warnings)}")
        for warning in all_warnings:
            print(f"    - {warning}")

        if is_production:
            print(f"\n  Review warnings before production deployment")

        return 0

    else:
        print(f"\n  [OK] VALIDATION PASSED")
        print(f"\n  All configuration checks passed!")
        print(f"  System is ready for {'production ' if is_production else ''}deployment")

        return 0


if __name__ == '__main__':
    sys.exit(main())
