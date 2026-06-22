import os
import secrets
import warnings
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Load .env files if present (production first, then dev fallback).
# .env.production takes priority — it's what setup_production.py generates.
try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).parent
    _env_production = _project_root / ".env.production"
    _env_default = _project_root / ".env"
    if _env_production.exists():
        load_dotenv(_env_production)
    if _env_default.exists():
        load_dotenv(_env_default, override=False)  # won't override production values
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

# ---------------------------------------------------------------------------
# Hardware-aware defaults: detect server resources and compute sane defaults
# for workers / pool sizes. Every value is overridable via its env var.
# ---------------------------------------------------------------------------
from resource_detection import get_resource_profile as _get_resource_profile

_resources = _get_resource_profile()


# =============================================================================
# Production Security Constants
# =============================================================================

# Known insecure default values that MUST NOT be used in production
INSECURE_JWT_DEFAULTS = {
    "change-this-secret-key-in-production",
    "secret",
    "supersecret",
    "jwt-secret",
    "dev-secret",
    "test-secret",
    "",
}

# Minimum JWT secret length for security
MIN_JWT_SECRET_LENGTH = 32


@dataclass
class _SystemConfig:
    # Use SNFLWR_DATA_DIR if set, otherwise ./data (relative to working directory)
    # Note: Do NOT use APPDATA - on Windows it points to C:\Users\...\AppData\Roaming
    APP_DATA_DIR: Path = Path(os.getenv("SNFLWR_DATA_DIR", "./data")).resolve()
    DB_PATH: Path = APP_DATA_DIR / "snflwr.db"
    DB_TIMEOUT: int = 5
    DB_CHECK_SAME_THREAD: bool = False
    # Database selection: 'sqlite' or 'postgresql'
    # Accepts DB_TYPE or DATABASE_TYPE env var (DB_TYPE takes priority)
    DB_TYPE: str = os.getenv("DB_TYPE", os.getenv("DATABASE_TYPE", "sqlite"))
    DATABASE_TYPE: str = DB_TYPE

    # Database Encryption (SQLCipher for SQLite)
    # Default-on: pilots running without explicit opt-out would otherwise
    # store child PII in plaintext (audit C4 / FERPA). To opt out for
    # legitimate dev workflows without SQLCipher installed, set
    # DB_ENCRYPTION_ENABLED=false explicitly.
    DB_ENCRYPTION_ENABLED: bool = (
        os.getenv("DB_ENCRYPTION_ENABLED", "true").lower() == "true"
    )
    DB_ENCRYPTION_KEY: Optional[str] = field(
        default_factory=lambda: _SystemConfig._get_db_encryption_key()
    )
    DB_KDF_ITERATIONS: int = int(os.getenv("DB_KDF_ITERATIONS") or "256000")

    # PostgreSQL Configuration (production deployments)
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT") or "5432")
    # Accepts POSTGRES_DATABASE or POSTGRES_DB env var (POSTGRES_DATABASE takes priority)
    POSTGRES_DB: str = os.getenv(
        "POSTGRES_DATABASE", os.getenv("POSTGRES_DB", "snflwr_db")
    )
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "snflwr")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
    POSTGRES_SSLMODE: str = os.getenv("POSTGRES_SSLMODE", "prefer")
    POSTGRES_MIN_CONNECTIONS: int = _resources.postgres_min_connections
    POSTGRES_MAX_CONNECTIONS: int = _resources.postgres_max_connections

    LOG_DIR: Path = APP_DATA_DIR / "logs"
    LOG_FORMAT: str = "%(levelname)s:%(name)s:%(message)s"
    LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
    LOG_LEVEL: str = "INFO"
    LOG_MAX_SIZE_MB: int = 10
    LOG_BACKUP_COUNT: int = 5

    # Accepts OLLAMA_BASE_URL or OLLAMA_HOST env var (OLLAMA_BASE_URL takes priority)
    OLLAMA_HOST: str = os.getenv(
        "OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")
    )
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "300"))
    OLLAMA_MAX_RETRIES: int = 3
    OLLAMA_RETRY_DELAY: int = 2
    # When snflwr-api acts as an Ollama proxy, this is the real Ollama
    # backend to forward requests to.  Defaults to OLLAMA_HOST.
    OLLAMA_PROXY_TARGET: str = os.getenv(
        "OLLAMA_PROXY_TARGET",
        os.getenv(
            "OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")
        ),
    )
    # HTTP request timeout for RequestTimeoutMiddleware — must exceed OLLAMA_TIMEOUT
    # so the middleware never kills a request while Ollama is still generating.
    REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "360"))
    # Set by install.py / setup based on hardware detection — no hardcoded fallback
    OLLAMA_DEFAULT_MODEL: str = os.getenv("OLLAMA_DEFAULT_MODEL", "")

    APPLICATION_NAME: str = "snflwr.ai"
    VERSION: str = os.getenv("SNFLWR_VERSION", "1.0.0")
    PLATFORM: str = "linux"

    # --- Licensing / billing ---
    LICENSE_SERVER_URL: str = os.getenv("LICENSE_SERVER_URL", "")
    LICENSE_PUBLIC_KEY_PATH: str = os.getenv(
        "LICENSE_PUBLIC_KEY_PATH", "./config/license_public_key.pem")
    LICENSE_REFRESH_INTERVAL_SECONDS: int = int(
        os.getenv("LICENSE_REFRESH_INTERVAL_SECONDS", str(14 * 86400)))
    # Off by default: enforcement is flipped on only when billing goes live
    # (entity + legal + license server). Shipping this code must NOT instantly
    # gate existing deployments. See billing spec §11 (phased rollout).
    LICENSE_ENFORCED: bool = os.getenv("LICENSE_ENFORCED", "false").lower() == "true"
    LS_CHECKOUT_URL: str = os.getenv("LS_CHECKOUT_URL", "")
    LS_CUSTOMER_PORTAL_URL: str = os.getenv("LS_CUSTOMER_PORTAL_URL", "")

    # Deployment mode: 'auto' (try USB then local), 'usb', 'local', 'thin_client'
    DEPLOY_MODE: str = os.getenv("SNFLWR_DEPLOY_MODE", "auto")
    # Management server URL for thin client deployments
    MANAGEMENT_SERVER_URL: str = os.getenv("MANAGEMENT_SERVER_URL", "")

    # API runtime configuration
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT") or "39150")
    API_RELOAD: bool = os.getenv("API_RELOAD", "False").lower() in ("1", "true", "yes")
    API_WORKERS: int = _resources.api_workers
    ENABLE_SAFETY_MONITORING: bool = os.getenv(
        "ENABLE_SAFETY_MONITORING", "True"
    ).lower() in ("1", "true", "yes")
    # When the ML safety classifier is unavailable, fail closed (block tutoring)
    # for ALL ages, not just under-13 (which always fails closed). Recommended
    # for production once the safety model is reliably provisioned. Default off
    # so a deployment without the model still serves teens via deterministic
    # filtering. See security finding F2.
    SAFETY_CLASSIFIER_REQUIRED: bool = os.getenv(
        "SAFETY_CLASSIFIER_REQUIRED", "false"
    ).lower() in ("1", "true", "yes")

    # Email Configuration (SMTP for parent alerts)
    SMTP_ENABLED: bool = os.getenv("SMTP_ENABLED", "false").lower() == "true"
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT") or "587")
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "noreply@snflwr.ai")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "snflwr.ai Safety Monitor")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    # Admin email for audit failure alerts
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")

    # JWT Authentication
    # CRITICAL: Must be set to a secure random value in production
    JWT_SECRET_KEY: str = field(default_factory=lambda: _SystemConfig._get_jwt_secret())
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # Redis Configuration (REQUIRED for production rate limiting)
    # Default false for development; production validation enforces Redis is enabled
    REDIS_ENABLED: bool = os.getenv("REDIS_ENABLED", "false").lower() == "true"
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT") or "6379")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_DB: int = int(os.getenv("REDIS_DB") or "0")

    # CORS Configuration
    CORS_ORIGINS: List[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://localhost:5173,http://localhost:8080",
        ).split(",")
    )

    # Flower Configuration (Celery monitoring UI)
    FLOWER_ENABLED: bool = field(
        default_factory=lambda: os.getenv("FLOWER_ENABLED", "false").lower() == "true"
    )
    FLOWER_PORT: int = field(
        default_factory=lambda: int(os.getenv("FLOWER_PORT") or "5555")
    )
    FLOWER_USER: str = field(default_factory=lambda: os.getenv("FLOWER_USER", "admin"))
    FLOWER_PASSWORD: str = field(
        default_factory=lambda: os.getenv("FLOWER_PASSWORD", "")
    )

    # Base URL for the application
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:39150")

    # Open WebUI URL (for admin auth bridge — proxy login through Open WebUI)
    OPEN_WEBUI_URL: str = os.getenv(
        "OPEN_WEBUI_URL",
        f"http://localhost:{os.getenv('WEBUI_PORT', '3000')}",
    )

    @property
    def REDIS_URL(self) -> str:
        """Build Redis URL from components"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @staticmethod
    def _get_jwt_secret() -> str:
        """
        Get JWT secret from environment or generate secure default for development.

        In production, JWT_SECRET_KEY environment variable MUST be set.
        """
        env_secret = os.getenv("JWT_SECRET_KEY")

        if env_secret:
            # Validate the provided secret
            if env_secret in INSECURE_JWT_DEFAULTS:
                raise RuntimeError(
                    "CRITICAL SECURITY ERROR: JWT_SECRET_KEY is set to a known insecure value.\n"
                    "Generate a secure secret with: python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            if len(env_secret) < MIN_JWT_SECRET_LENGTH:
                raise RuntimeError(
                    f"CRITICAL SECURITY ERROR: JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters.\n"
                    "Generate a secure secret with: python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            return env_secret

        # Check if we're in a production-like environment
        environment = os.getenv("ENVIRONMENT", "development").lower()
        is_production = environment in ("production", "prod", "staging")

        if is_production:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set for production.\n"
                "Run the setup script to configure everything automatically:\n"
                "    python scripts/setup_production.py"
            )

        # Development: generate and persist so sessions survive restarts
        env_path = Path(__file__).parent / ".env"

        # Check if .env already has a JWT secret (not loaded into process env)
        try:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith(
                        "JWT_SECRET_KEY="
                    ) and not stripped.startswith("#"):
                        existing = stripped.split("=", 1)[1].strip()
                        if (
                            existing
                            and existing not in INSECURE_JWT_DEFAULTS
                            and len(existing) >= MIN_JWT_SECRET_LENGTH
                        ):
                            return existing
        except OSError:
            pass

        # No valid key found in .env — generate and persist
        generated_secret = secrets.token_hex(32)
        try:
            fd = os.open(str(env_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(f"\n# Auto-generated JWT secret for session persistence\n")
                f.write(f"JWT_SECRET_KEY={generated_secret}\n")
            warnings.warn(
                "JWT_SECRET_KEY not set - generated and saved to .env.", RuntimeWarning
            )
        except OSError:
            warnings.warn(
                "JWT_SECRET_KEY not set - using ephemeral secret. "
                "Sessions will be invalidated on restart. Set JWT_SECRET_KEY for persistence.",
                RuntimeWarning,
            )
        return generated_secret

    @staticmethod
    def _get_db_encryption_key() -> Optional[str]:
        """Return DB_ENCRYPTION_KEY from env, or auto-generate+persist in dev.

        Mirrors the JWT_SECRET_KEY pattern: in any production-like env, an
        unset key is a hard fail at validate_production_security() time. In
        development we auto-generate and persist to .env so the database
        survives container restarts without surprise data loss.
        """
        env_key = os.getenv("DB_ENCRYPTION_KEY")
        if env_key:
            return env_key

        environment = os.getenv("ENVIRONMENT", "development").lower()
        if environment in ("production", "prod", "staging"):
            # Don't auto-generate in prod — the validator will surface a
            # clear error and the operator should set this explicitly with
            # offline backup of the key.
            return None

        env_path = Path(__file__).parent / ".env"

        # Re-read .env directly (it may not have been loaded into os.environ
        # yet if the dotenv shim ran before this key was added).
        try:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith("DB_ENCRYPTION_KEY=") and \
                            not stripped.startswith("#"):
                        existing = stripped.split("=", 1)[1].strip()
                        if existing and len(existing) >= 32:
                            return existing
        except OSError:
            pass

        generated = secrets.token_urlsafe(32)
        try:
            fd = os.open(str(env_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write("\n# Auto-generated DB encryption key (dev only)\n")
                f.write(f"DB_ENCRYPTION_KEY={generated}\n")
            warnings.warn(
                "DB_ENCRYPTION_KEY not set — generated and saved to .env. "
                "BACK THIS UP: losing it means losing all encrypted data.",
                RuntimeWarning,
            )
        except OSError:
            warnings.warn(
                "DB_ENCRYPTION_KEY not set — using ephemeral key for this "
                "process. Encrypted data will be unreadable after restart.",
                RuntimeWarning,
            )
        return generated

    def is_production(self) -> bool:
        """Check if running in production environment"""
        environment = os.getenv("ENVIRONMENT", "development").lower()
        return environment in ("production", "prod", "staging")

    def is_production_like(self) -> bool:
        """Check if configuration suggests production-like deployment"""
        # Note: API_WORKERS is now auto-detected (always >= 2), so we check
        # whether the admin *explicitly* set it via env var instead.
        explicit_workers = os.getenv("API_WORKERS") is not None
        return (
            self.DB_TYPE == "postgresql"
            or "localhost" not in self.BASE_URL
            or self.SMTP_ENABLED
            or explicit_workers
        )

    def validate_production_security(self) -> List[str]:
        """
        Validate security configuration for production deployment.

        Returns:
            List of error messages (empty if all checks pass)

        Raises:
            RuntimeError: If critical security issues are found in production
        """
        errors = []
        warnings_list = []

        is_prod = self.is_production()
        is_prod_like = self.is_production_like()

        # =================================================================
        # CRITICAL: JWT Secret Validation
        # =================================================================
        if self.JWT_SECRET_KEY in INSECURE_JWT_DEFAULTS:
            errors.append(
                "JWT_SECRET_KEY is using an insecure default value. "
                "Set JWT_SECRET_KEY environment variable."
            )

        if len(self.JWT_SECRET_KEY) < MIN_JWT_SECRET_LENGTH:
            errors.append(
                f"JWT_SECRET_KEY is too short ({len(self.JWT_SECRET_KEY)} chars). "
                f"Minimum length: {MIN_JWT_SECRET_LENGTH} characters."
            )

        # =================================================================
        # CRITICAL: Database Encryption for FERPA Compliance
        # =================================================================
        if is_prod or is_prod_like:
            if self.DB_TYPE == "sqlite" and not self.DB_ENCRYPTION_ENABLED:
                errors.append(
                    "Database encryption is REQUIRED for production (FERPA compliance). "
                    "Set DB_ENCRYPTION_ENABLED=true and DB_ENCRYPTION_KEY."
                )

            if self.DB_ENCRYPTION_ENABLED and not self.DB_ENCRYPTION_KEY:
                errors.append(
                    "DB_ENCRYPTION_KEY must be set when encryption is enabled. "
                    "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
                )

        # =================================================================
        # CRITICAL: Redis Required for Rate Limiting
        # =================================================================
        if is_prod and not self.REDIS_ENABLED:
            errors.append(
                "Redis is REQUIRED in production for rate limiting and session management. "
                "Set REDIS_ENABLED=true and configure Redis connection."
            )

        # =================================================================
        # HIGH: PostgreSQL Password
        # =================================================================
        if self.DB_TYPE == "postgresql" and not self.POSTGRES_PASSWORD:
            if is_prod:
                errors.append("PostgreSQL password is not set for production database.")
            else:
                warnings_list.append("PostgreSQL password is empty.")

        # =================================================================
        # CRITICAL: PostgreSQL SSL Mode
        # =================================================================
        if self.DB_TYPE == "postgresql" and self.POSTGRES_SSLMODE in (
            "disable",
            "allow",
            "prefer",
        ):
            if is_prod:
                errors.append(
                    f"POSTGRES_SSLMODE is '{self.POSTGRES_SSLMODE}'. "
                    "Production REQUIRES 'require' or 'verify-full' for encrypted connections. "
                    "Set POSTGRES_SSLMODE=require (or verify-full with a CA cert)."
                )
            else:
                warnings_list.append(
                    f"POSTGRES_SSLMODE is '{self.POSTGRES_SSLMODE}'. "
                    "Production should use 'require' or 'verify-full' for encrypted connections."
                )

        # =================================================================
        # HIGH: CSRF Cookie Security
        # =================================================================
        csrf_cookie_secure = os.getenv("CSRF_COOKIE_SECURE", "false").lower() == "true"
        if not csrf_cookie_secure:
            msg = (
                "CSRF_COOKIE_SECURE is not enabled. Set CSRF_COOKIE_SECURE=true "
                "for production to prevent CSRF cookie transmission over HTTP."
            )
            if is_prod:
                errors.append(msg)
            else:
                warnings_list.append(msg)

        # =================================================================
        # HIGH: Conversation Encryption (COPPA/FERPA Compliance)
        # =================================================================
        if is_prod or is_prod_like:
            if not safety_config.ENCRYPT_CONVERSATIONS:
                errors.append(
                    "ENCRYPT_CONVERSATIONS is disabled. Student conversations must be "
                    "encrypted at rest for COPPA/FERPA compliance."
                )

        # =================================================================
        # HIGH: CORS Configuration
        # =================================================================
        if is_prod:
            wildcard_origins = [o for o in self.CORS_ORIGINS if "*" in o]
            if wildcard_origins:
                errors.append(
                    f"CORS origins contain wildcards in production: {wildcard_origins}. "
                    "Set explicit allowed origins."
                )

        # =================================================================
        # HIGH: Flower Monitoring Credentials
        # =================================================================
        if self.FLOWER_ENABLED:
            if not self.FLOWER_PASSWORD or self.FLOWER_PASSWORD == "admin":
                errors.append(
                    "Flower monitoring UI has weak/missing credentials. "
                    "Set FLOWER_USER and FLOWER_PASSWORD environment variables."
                )

        # =================================================================
        # CRITICAL: Internal API Key (server-to-server auth)
        # =================================================================
        internal_key = os.getenv("INTERNAL_API_KEY", "snflwr-internal-dev-key")
        _KNOWN_INSECURE_KEYS = {
            "snflwr-internal-dev-key",
            "CHANGE-THIS-generate-with-secrets-token-hex-32",
        }
        if internal_key in _KNOWN_INSECURE_KEYS or len(internal_key) < 32:
            if is_prod or is_prod_like:
                errors.append(
                    "INTERNAL_API_KEY is insecure (default, placeholder, or too short). "
                    "This key grants admin-level API access. "
                    "Set INTERNAL_API_KEY to a strong random value (>= 32 chars): "
                    "python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            else:
                warnings_list.append(
                    "INTERNAL_API_KEY is insecure (default, placeholder, or too short). "
                    "Set INTERNAL_API_KEY to a strong random value for production."
                )

        # Log warnings
        for warning in warnings_list:
            warnings.warn(warning, RuntimeWarning)

        # Raise on critical errors in production
        if errors and (is_prod or is_prod_like):
            error_msg = "Production security validation failed:\n" + "\n".join(
                f"  - {e}" for e in errors
            )
            raise RuntimeError(error_msg)

        return errors

    def get_info(self):
        return {
            "application": self.APPLICATION_NAME,
            "version": self.VERSION,
            "platform": self.PLATFORM,
            "app_data_dir": str(self.APP_DATA_DIR),
            "production": self.is_production(),
            "production_like": self.is_production_like(),
            "db_type": self.DB_TYPE,
            "db_encryption": self.DB_ENCRYPTION_ENABLED,
            "redis_enabled": self.REDIS_ENABLED,
        }


class ProductionConfigValidator:
    """Standalone validator that returns (errors, warnings) without raising."""

    def validate(self) -> Tuple[List[str], List[str]]:
        """
        Run production security checks.

        Returns:
            Tuple of (errors, warnings) — both are lists of strings.
            Never raises; callers decide how to handle errors.
        """
        errors: List[str] = []
        warnings_list: List[str] = []

        snflwr_env = os.getenv("SNFLWR_ENV", "").lower()
        is_prod = snflwr_env == "production"
        is_prod_like = snflwr_env in {"staging", "production"}

        # Internal API Key
        internal_key = os.getenv("INTERNAL_API_KEY", "snflwr-internal-dev-key")
        _KNOWN_INSECURE_KEYS = {
            "snflwr-internal-dev-key",
            "CHANGE-THIS-generate-with-secrets-token-hex-32",
        }
        if internal_key in _KNOWN_INSECURE_KEYS or len(internal_key) < 32:
            if is_prod or is_prod_like:
                errors.append(
                    "INTERNAL_API_KEY is insecure (default, placeholder, or too short). "
                    "This key grants admin-level API access. "
                    "Set INTERNAL_API_KEY to a strong random value (>= 32 chars): "
                    "python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            else:
                warnings_list.append(
                    "INTERNAL_API_KEY is insecure (default, placeholder, or too short). "
                    "Set INTERNAL_API_KEY to a strong random value for production."
                )

        return errors, warnings_list


@dataclass
class _SafetyConfig:
    PROHIBITED_KEYWORDS = {
        "violence": ["kill", "murder", "weapon", "gun", "knife", "bomb"],
        "self_harm": ["suicide", "kill myself", "self-harm", "cut myself"],
        "sexual": ["sex", "porn", "naked", "nude"],
        "drugs": ["drugs", "cocaine", "heroin", "marijuana", "weed"],
        "personal_info": ["social security", "ssn", "credit card", "address"],
        "bullying": ["bully", "bullying", "harass", "threat"],
        "dangerous_activity": ["how to make bomb", "how to hurt"],
    }
    REDIRECT_TOPICS = {
        "politics": "age-appropriate civic learning",
        "religion": "age-appropriate cultural overviews",
    }

    # Ollama safety model for semantic classifier (Stage 4).
    # These set the *preference order* only — the classifier queries Ollama's
    # local model list at startup and picks the first match that is actually
    # installed.  If neither the preferred model nor any fallback is present,
    # the semantic classifier disables itself and the deterministic stages
    # (1-3, 5) continue to protect.
    SAFETY_MODEL: str = os.getenv("SAFETY_MODEL", "llama-guard3:8b")
    SAFETY_MODEL_FALLBACKS: tuple = tuple(
        s.strip()
        for s in os.getenv("SAFETY_MODEL_FALLBACKS", "llama-guard3:1b").split(",")
        if s.strip()
    )

    # Data retention (COPPA compliance)
    SAFETY_LOG_RETENTION_DAYS = 90
    AUDIT_LOG_RETENTION_DAYS = 365
    SESSION_RETENTION_DAYS = 180
    CONVERSATION_RETENTION_DAYS = 180
    ANALYTICS_RETENTION_DAYS = 730

    # Data cleanup
    DATA_CLEANUP_ENABLED = True
    DATA_CLEANUP_HOUR = 2

    # Encryption
    ENCRYPT_INCIDENT_LOGS = True
    ENCRYPT_PERSONAL_DATA = True
    ENCRYPT_CONVERSATIONS: bool = (
        os.getenv("ENCRYPT_CONVERSATIONS", "true").lower() == "true"
    )
    KEY_ROTATION_DAYS = 365

    # Audit logging
    ENABLE_AUDIT_LOGGING = True
    AUDIT_LOG_ALL_ACCESS = True
    AUDIT_LOG_MODIFICATIONS = True
    AUDIT_LOG_DELETIONS = True

    # COPPA compliance
    AGE_VERIFICATION_REQUIRED = True
    SHARE_DATA_WITH_THIRD_PARTIES = False
    ALLOW_DATA_EXPORT = True
    ALLOW_DATA_DELETION = True

    # Parent controls
    REQUIRE_PARENT_CONSENT = True
    PARENT_FULL_CONVERSATION_ACCESS = True
    PARENT_CAN_DELETE_CONVERSATIONS = True
    PARENT_CAN_EXPORT_DATA = True

    # Failed login tracking
    MAX_FAILED_LOGIN_ATTEMPTS = 5
    ACCOUNT_LOCKOUT_DURATION_MINUTES = 30

    # Alert thresholds
    ALERT_THRESHOLD_CRITICAL = 1
    ALERT_THRESHOLD_MAJOR = 3
    ALERT_THRESHOLD_MINOR = 10
    ALERT_TIME_WINDOW_HOURS = 24

    # Session security
    SESSION_TIMEOUT_MINUTES = 60
    MAX_SESSIONS_PER_DEVICE = 3

    # Password requirements
    PASSWORD_MIN_LENGTH = 8
    PASSWORD_REQUIRE_UPPERCASE = True
    PASSWORD_REQUIRE_LOWERCASE = True
    PASSWORD_REQUIRE_NUMBERS = True
    PASSWORD_REQUIRE_SPECIAL = False

    # Grade-based filter levels
    FILTER_LEVELS = {
        "elementary": {
            "strictness": "maximum",
            "block_all_external_links": True,
            "allow_ai_explanations": True,
            "max_conversation_turns": 20,
        },
        "middle": {
            "strictness": "high",
            "block_all_external_links": False,
            "allow_ai_explanations": True,
            "max_conversation_turns": 30,
        },
        "high": {
            "strictness": "moderate",
            "block_all_external_links": False,
            "allow_ai_explanations": True,
            "max_conversation_turns": 50,
        },
    }

    def get_retention_policy(self):
        """Get retention policy summary"""
        return {
            "safety_incidents": self.SAFETY_LOG_RETENTION_DAYS,
            "safety_logs": self.SAFETY_LOG_RETENTION_DAYS,
            "audit_logs": self.AUDIT_LOG_RETENTION_DAYS,
            "sessions": self.SESSION_RETENTION_DAYS,
            "conversations": self.CONVERSATION_RETENTION_DAYS,
            "analytics": self.ANALYTICS_RETENTION_DAYS,
            "compliance": {"framework": "COPPA/FERPA", "data_minimization": True},
        }


system_config = _SystemConfig()
safety_config = _SafetyConfig()


def get_database_url() -> str:
    """
    Get database URL for current configuration

    Returns:
        str: Database URL (sqlite:// or postgresql://)
    """
    if system_config.DB_TYPE == "postgresql":
        return (
            f"postgresql://{system_config.POSTGRES_USER}:{system_config.POSTGRES_PASSWORD}"
            f"@{system_config.POSTGRES_HOST}:{system_config.POSTGRES_PORT}/{system_config.POSTGRES_DB}"
        )
    else:
        # SQLite
        return f"sqlite:///{system_config.DB_PATH}"


# Session defaults for session management tests
SESSION_CONFIG = {
    "idle_timeout_minutes": 30,
    "max_session_hours": 4,
    "max_sessions_per_day": 50,  # Increased to support testing scenarios including pagination tests
}


# Security configuration for CSRF, rate limiting, etc.
# CSRF secret must be stable across restarts and workers. If CSRF_SECRET is
# not explicitly set, derive it from the JWT secret (which is already persisted
# in .env) using HMAC so that it's deterministic but distinct.
def _derive_csrf_secret() -> str:
    explicit = os.getenv("CSRF_SECRET")
    if explicit:
        return explicit
    import hmac as _hmac
    import hashlib as _hashlib

    jwt_key = system_config.JWT_SECRET_KEY
    return _hmac.new(
        jwt_key.encode(), b"snflwr-csrf-secret", _hashlib.sha256
    ).hexdigest()


SECURITY_CONFIG = {
    "csrf_secret": _derive_csrf_secret(),
    "csrf_enabled": os.getenv("CSRF_ENABLED", "true").lower() == "true",
    "csrf_cookie_secure": os.getenv("CSRF_COOKIE_SECURE", "false").lower() == "true",
    "csrf_cookie_samesite": os.getenv("CSRF_COOKIE_SAMESITE", "strict"),
}

# Shared secret for internal server-to-server calls (Open WebUI -> Snflwr API)
_internal_key_from_env = os.getenv("INTERNAL_API_KEY")
if _internal_key_from_env:
    INTERNAL_API_KEY = _internal_key_from_env
else:
    # Hard-fail in any production-like environment. Auto-generating an
    # ephemeral key means OWU's Bearer breaks on every container restart
    # silently — and the proxy auth gate (audit C2) depends on this key.
    _internal_env = os.getenv("ENVIRONMENT", "development").lower()
    if _internal_env in ("production", "prod", "staging"):
        raise RuntimeError(
            "INTERNAL_API_KEY must be set for production deployments.\n"
            "Generate one with:\n"
            "    python -c 'import secrets; print(secrets.token_hex(32))'\n"
            "and set INTERNAL_API_KEY in your .env (or run "
            "scripts/setup_production.py)."
        )
    INTERNAL_API_KEY = secrets.token_hex(32)
    warnings.warn(
        "INTERNAL_API_KEY not set — using auto-generated ephemeral key. "
        "Set INTERNAL_API_KEY in .env for persistent server-to-server auth."
    )

INTERNAL_API_KEY_PREVIOUS: Optional[str] = os.getenv("INTERNAL_API_KEY_PREVIOUS")

INTERNAL_API_KEY_MAX_AGE_DAYS: int = int(
    os.getenv("INTERNAL_API_KEY_MAX_AGE_DAYS", "90")
)

_created_at_raw = os.getenv("INTERNAL_API_KEY_CREATED_AT")
INTERNAL_API_KEY_CREATED_AT: Optional[datetime] = (
    datetime.fromisoformat(_created_at_raw) if _created_at_raw else None
)
