"""
Tests for production security configuration validation.

Verifies that validate_production_security() correctly blocks unsafe
configurations in production and production-like environments. These
checks are the last line of defense before enterprise deployment.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from config import _SystemConfig, INSECURE_JWT_DEFAULTS, MIN_JWT_SECRET_LENGTH


# A valid long JWT secret for tests that need config to pass JWT checks
_VALID_JWT = "a" * 64


def _make_config(**overrides):
    """Build a _SystemConfig with sane test defaults, then apply overrides."""
    # We patch _get_jwt_secret to avoid filesystem side effects
    with patch.object(_SystemConfig, '_get_jwt_secret', return_value=_VALID_JWT):
        cfg = _SystemConfig()
    # Apply overrides directly
    for k, v in overrides.items():
        object.__setattr__(cfg, k, v)
    return cfg


# =========================================================================
# JWT Secret Validation
# =========================================================================

class TestJWTSecretValidation:

    @pytest.mark.parametrize("bad_secret", list(INSECURE_JWT_DEFAULTS - {''}))
    def test_insecure_jwt_default_flagged(self, bad_secret):
        """Every known insecure JWT default must produce an error."""
        cfg = _make_config(JWT_SECRET_KEY=bad_secret)
        errors = cfg.validate_production_security()
        assert any("insecure default" in e.lower() or "JWT_SECRET_KEY" in e for e in errors)

    def test_short_jwt_secret_flagged(self):
        """JWT secrets shorter than MIN_JWT_SECRET_LENGTH must be rejected."""
        cfg = _make_config(JWT_SECRET_KEY="short")
        errors = cfg.validate_production_security()
        assert any("too short" in e for e in errors)

    def test_valid_jwt_secret_passes(self):
        """A long, non-default JWT secret should not produce JWT errors."""
        cfg = _make_config(JWT_SECRET_KEY=_VALID_JWT)
        errors = cfg.validate_production_security()
        jwt_errors = [e for e in errors if "JWT_SECRET_KEY" in e]
        assert not jwt_errors


# =========================================================================
# Database Encryption (FERPA)
# =========================================================================

class TestDatabaseEncryptionValidation:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_sqlite_without_encryption_blocked(self):
        """Production + SQLite + no encryption = FERPA violation → error."""
        cfg = _make_config(
            DB_TYPE="sqlite",
            DB_ENCRYPTION_ENABLED=False,
            REDIS_ENABLED=True,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_encryption_without_key_blocked(self):
        """Encryption enabled but no key = error."""
        cfg = _make_config(
            DB_TYPE="sqlite",
            DB_ENCRYPTION_ENABLED=True,
            DB_ENCRYPTION_KEY=None,
            REDIS_ENABLED=True,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()

    @patch.dict(os.environ, {"ENVIRONMENT": "development"})
    def test_dev_mode_allows_unencrypted_sqlite(self):
        """Development mode should not require encryption."""
        cfg = _make_config(
            DB_TYPE="sqlite",
            DB_ENCRYPTION_ENABLED=False,
        )
        errors = cfg.validate_production_security()
        enc_errors = [e for e in errors if "encryption" in e.lower() and "FERPA" in e]
        assert not enc_errors


# =========================================================================
# Redis Required in Production
# =========================================================================

class TestRedisRequirement:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_without_redis_blocked(self):
        """Production without Redis = error (rate limiting needs it)."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            DB_ENCRYPTION_ENABLED=False,
            REDIS_ENABLED=False,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()

    @patch.dict(os.environ, {"ENVIRONMENT": "development"})
    def test_dev_mode_allows_no_redis(self):
        """Development mode should not require Redis."""
        cfg = _make_config(REDIS_ENABLED=False)
        errors = cfg.validate_production_security()
        redis_errors = [e for e in errors if "redis" in e.lower()]
        assert not redis_errors


# =========================================================================
# PostgreSQL SSL Mode
# =========================================================================

class TestPostgresSSLValidation:

    @pytest.mark.parametrize("weak_mode", ["disable", "allow", "prefer"])
    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_weak_ssl_blocked(self, weak_mode):
        """Production PostgreSQL with weak SSL mode = error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_SSLMODE=weak_mode,
            POSTGRES_PASSWORD="strongpassword",
            DB_ENCRYPTION_ENABLED=False,
            REDIS_ENABLED=True,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()

    @pytest.mark.parametrize("strong_mode", ["require", "verify-full"])
    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_strong_ssl_passes(self, strong_mode):
        """Production PostgreSQL with strong SSL mode should not flag SSL errors."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_SSLMODE=strong_mode,
            POSTGRES_PASSWORD="strongpassword",
            DB_ENCRYPTION_ENABLED=False,
            REDIS_ENABLED=True,
        )
        # May still raise for other reasons (ENCRYPT_CONVERSATIONS, etc.)
        # We just check that no SSL-specific error exists
        try:
            errors = cfg.validate_production_security()
        except RuntimeError as e:
            assert "SSLMODE" not in str(e)

    @pytest.mark.parametrize("weak_mode", ["disable", "allow", "prefer"])
    @patch.dict(os.environ, {
        "ENVIRONMENT": "development",
        "INTERNAL_API_KEY": "a" * 64,
    })
    def test_dev_weak_ssl_is_warning_only(self, weak_mode):
        """Dev mode weak SSL should warn, not error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_SSLMODE=weak_mode,
        )
        # Should not raise (dev mode: errors returned but not raised)
        errors = cfg.validate_production_security()
        ssl_errors = [e for e in errors if "SSLMODE" in e]
        assert not ssl_errors  # warnings go to warnings module, not returned


# =========================================================================
# PostgreSQL Password
# =========================================================================

class TestPostgresPassword:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_pg_no_password_blocked(self):
        """Production PostgreSQL with empty password = error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_PASSWORD="",
            POSTGRES_SSLMODE="require",
            REDIS_ENABLED=True,
            DB_ENCRYPTION_ENABLED=False,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()


# =========================================================================
# CORS Wildcards
# =========================================================================

class TestCORSValidation:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_wildcard_cors_blocked(self):
        """Production CORS with wildcards = error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_PASSWORD="pass",
            POSTGRES_SSLMODE="require",
            REDIS_ENABLED=True,
            DB_ENCRYPTION_ENABLED=False,
            CORS_ORIGINS=["*"],
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()


# =========================================================================
# Flower Monitoring Credentials
# =========================================================================

class TestFlowerCredentials:

    def test_flower_weak_password_flagged(self):
        """Flower enabled with weak password = error."""
        cfg = _make_config(FLOWER_ENABLED=True, FLOWER_PASSWORD="admin")
        errors = cfg.validate_production_security()
        assert any("Flower" in e for e in errors)

    def test_flower_no_password_flagged(self):
        """Flower enabled with no password = error."""
        cfg = _make_config(FLOWER_ENABLED=True, FLOWER_PASSWORD="")
        errors = cfg.validate_production_security()
        assert any("Flower" in e for e in errors)

    def test_flower_disabled_no_check(self):
        """Flower disabled should not flag anything."""
        cfg = _make_config(FLOWER_ENABLED=False, FLOWER_PASSWORD="")
        errors = cfg.validate_production_security()
        flower_errors = [e for e in errors if "Flower" in e]
        assert not flower_errors


# =========================================================================
# Internal API Key
# =========================================================================

class TestInternalAPIKey:

    @patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "INTERNAL_API_KEY": "snflwr-internal-dev-key",
    })
    def test_production_default_api_key_blocked(self):
        """Production with default internal API key = error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_PASSWORD="pass",
            POSTGRES_SSLMODE="require",
            REDIS_ENABLED=True,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()

    @patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "INTERNAL_API_KEY": "short",
    })
    def test_production_short_api_key_blocked(self):
        """Production with too-short internal API key = error."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_PASSWORD="pass",
            POSTGRES_SSLMODE="require",
            REDIS_ENABLED=True,
        )
        with pytest.raises(RuntimeError, match="security validation failed"):
            cfg.validate_production_security()


# =========================================================================
# Conversation Encryption (COPPA/FERPA)
# =========================================================================

class TestConversationEncryption:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_production_unencrypted_conversations_blocked(self):
        """Production without ENCRYPT_CONVERSATIONS = COPPA/FERPA violation."""
        cfg = _make_config(
            DB_TYPE="postgresql",
            POSTGRES_PASSWORD="pass",
            POSTGRES_SSLMODE="require",
            REDIS_ENABLED=True,
            DB_ENCRYPTION_ENABLED=False,
        )
        with patch("config.safety_config") as mock_safety:
            mock_safety.ENCRYPT_CONVERSATIONS = False
            with pytest.raises(RuntimeError, match="security validation failed"):
                cfg.validate_production_security()


# =========================================================================
# Production-Like Detection
# =========================================================================

class TestProductionDetection:

    @patch.dict(os.environ, {"ENVIRONMENT": "production"})
    def test_is_production_true(self):
        cfg = _make_config()
        assert cfg.is_production() is True

    @patch.dict(os.environ, {"ENVIRONMENT": "staging"})
    def test_staging_is_production(self):
        cfg = _make_config()
        assert cfg.is_production() is True

    @patch.dict(os.environ, {"ENVIRONMENT": "development"})
    def test_dev_is_not_production(self):
        cfg = _make_config()
        assert cfg.is_production() is False

    def test_postgresql_is_production_like(self):
        cfg = _make_config(DB_TYPE="postgresql")
        assert cfg.is_production_like() is True

    def test_nonlocalhost_base_url_is_production_like(self):
        cfg = _make_config(BASE_URL="https://snflwr.school.edu")
        assert cfg.is_production_like() is True

    def test_smtp_enabled_is_production_like(self):
        cfg = _make_config(SMTP_ENABLED=True)
        assert cfg.is_production_like() is True

    @patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False)
    def test_sqlite_localhost_is_not_production_like(self):
        cfg = _make_config(
            DB_TYPE="sqlite",
            BASE_URL="http://localhost:8000",
            SMTP_ENABLED=False,
        )
        # Remove API_WORKERS from env if present
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_WORKERS", None)
            assert cfg.is_production_like() is False
