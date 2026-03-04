"""
Secrets Rotation Mechanism

Allows secure rotation of secrets (API keys, tokens, passwords) without
application restarts. Supports:
- Graceful secret transitions with configurable overlap period
- Automatic validation of new secrets before activation
- Rollback capability if new secrets fail
- Audit logging of all secret rotations
- Redis-backed distributed secret storage (optional)

Usage:
    from utils.secrets_rotation import SecretManager, RotatableSecret

    # Initialize the manager
    secret_manager = SecretManager()

    # Register a secret
    secret_manager.register_secret(
        'ollama_api_key',
        initial_value=os.getenv('OLLAMA_API_KEY'),
        validator=validate_ollama_key
    )

    # Get current secret value
    api_key = secret_manager.get_secret('ollama_api_key')

    # Rotate the secret
    await secret_manager.rotate_secret('ollama_api_key', new_value)
"""

import os
import asyncio
import json
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Callable, Awaitable, Union
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps

from utils.logger import get_logger

logger = get_logger(__name__)


class SecretStatus(Enum):
    """Status of a rotatable secret"""
    ACTIVE = "active"
    PENDING_ROTATION = "pending_rotation"
    ROTATING = "rotating"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class SecretVersion:
    """Represents a version of a secret"""
    value: str
    created_at: datetime
    activated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    version: int = 1
    is_active: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary (without exposing the value)"""
        return {
            'version': self.version,
            'created_at': self.created_at.isoformat(),
            'activated_at': self.activated_at.isoformat() if self.activated_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_active': self.is_active,
            'value_hash': hashlib.sha256(self.value.encode()).hexdigest()[:16],
        }


@dataclass
class RotatableSecret:
    """
    A secret that can be rotated without downtime.

    Supports dual-active mode during rotation where both old and new
    secrets are valid for a configurable overlap period.
    """
    name: str
    current_version: Optional[SecretVersion] = None
    previous_version: Optional[SecretVersion] = None
    status: SecretStatus = SecretStatus.ACTIVE
    validator: Optional[Callable[[str], Union[bool, Awaitable[bool]]]] = None
    overlap_seconds: int = 300  # 5 minutes overlap during rotation
    max_versions_retained: int = 2
    rotation_history: list = field(default_factory=list)

    def get_value(self) -> Optional[str]:
        """Get the current active secret value"""
        if self.current_version and self.current_version.is_active:
            return self.current_version.value
        return None

    def get_all_valid_values(self) -> list[str]:
        """
        Get all currently valid secret values.
        During rotation, both old and new secrets may be valid.
        """
        values = []
        now = datetime.now(timezone.utc)

        if self.current_version and self.current_version.is_active:
            values.append(self.current_version.value)

        if self.previous_version:
            if self.previous_version.expires_at and self.previous_version.expires_at > now:
                values.append(self.previous_version.value)

        return values

    def is_valid(self, value: str) -> bool:
        """Check if a given value matches any valid version of this secret"""
        return value in self.get_all_valid_values()


class SecretManager:
    """
    Manages secrets rotation across the application.

    Features:
    - Thread-safe secret access
    - Optional Redis backing for distributed deployments
    - Automatic secret validation before rotation
    - Audit logging
    - Graceful fallback on rotation failure
    """

    def __init__(self, redis_client=None, redis_prefix: str = "secrets:"):
        """
        Initialize the secret manager.

        Args:
            redis_client: Optional Redis client for distributed secret storage
            redis_prefix: Key prefix for Redis storage
        """
        self._secrets: Dict[str, RotatableSecret] = {}
        self._lock = threading.RLock()
        self._redis = redis_client
        self._redis_prefix = redis_prefix
        self._rotation_callbacks: Dict[str, list[Callable]] = {}

    def register_secret(
        self,
        name: str,
        initial_value: Optional[str] = None,
        validator: Optional[Callable[[str], Union[bool, Awaitable[bool]]]] = None,
        overlap_seconds: int = 300,
        env_var: Optional[str] = None,
    ) -> RotatableSecret:
        """
        Register a new rotatable secret.

        Args:
            name: Unique identifier for the secret
            initial_value: Initial secret value (or loaded from env_var)
            validator: Async function to validate secret before activation
            overlap_seconds: How long old secret remains valid after rotation
            env_var: Environment variable to load initial value from

        Returns:
            The registered RotatableSecret
        """
        with self._lock:
            if name in self._secrets:
                logger.warning(f"Secret '{name}' already registered, updating")

            # Load from env var if specified and no initial value provided
            if initial_value is None and env_var:
                initial_value = os.getenv(env_var)

            secret = RotatableSecret(
                name=name,
                validator=validator,
                overlap_seconds=overlap_seconds,
            )

            if initial_value:
                secret.current_version = SecretVersion(
                    value=initial_value,
                    created_at=datetime.now(timezone.utc),
                    activated_at=datetime.now(timezone.utc),
                    is_active=True,
                    version=1,
                )

            self._secrets[name] = secret

            logger.info(
                f"Registered secret",
                extra={
                    'secret_name': name,
                    'has_initial_value': initial_value is not None,
                    'overlap_seconds': overlap_seconds,
                }
            )

            return secret

    def get_secret(self, name: str) -> Optional[str]:
        """
        Get the current value of a secret.

        Args:
            name: Name of the secret

        Returns:
            Current secret value or None if not found/disabled
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                logger.warning(f"Secret '{name}' not found")
                return None

            if secret.status == SecretStatus.DISABLED:
                logger.warning(f"Secret '{name}' is disabled")
                return None

            return secret.get_value()

    def get_all_valid_values(self, name: str) -> list[str]:
        """
        Get all valid values for a secret (useful during rotation period).

        Args:
            name: Name of the secret

        Returns:
            List of all currently valid secret values
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                return []
            return secret.get_all_valid_values()

    def is_valid(self, name: str, value: str) -> bool:
        """
        Check if a value is valid for a given secret.

        Args:
            name: Name of the secret
            value: Value to validate

        Returns:
            True if value matches any valid version of the secret
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                return False
            return secret.is_valid(value)

    async def rotate_secret(
        self,
        name: str,
        new_value: str,
        skip_validation: bool = False,
    ) -> bool:
        """
        Rotate a secret to a new value.

        Args:
            name: Name of the secret to rotate
            new_value: New secret value
            skip_validation: Skip validation (not recommended for production)

        Returns:
            True if rotation successful, False otherwise
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                logger.error(f"Cannot rotate: secret '{name}' not found")
                return False

            if secret.status == SecretStatus.ROTATING:
                logger.error(f"Secret '{name}' is already being rotated")
                return False

            old_status = secret.status
            secret.status = SecretStatus.ROTATING

        try:
            # Validate the new secret if validator is provided
            if not skip_validation and secret.validator:
                logger.info(f"Validating new secret value for '{name}'")

                is_valid = secret.validator(new_value)
                if asyncio.iscoroutine(is_valid):
                    is_valid = await is_valid

                if not is_valid:
                    logger.error(f"New secret value failed validation for '{name}'")
                    with self._lock:
                        secret.status = old_status
                    return False

            # Perform the rotation
            with self._lock:
                now = datetime.now(timezone.utc)

                # Move current to previous
                if secret.current_version:
                    secret.previous_version = secret.current_version
                    secret.previous_version.is_active = False
                    secret.previous_version.expires_at = now + timedelta(
                        seconds=secret.overlap_seconds
                    )

                # Set new as current
                new_version_num = (
                    secret.current_version.version + 1
                    if secret.current_version
                    else 1
                )

                secret.current_version = SecretVersion(
                    value=new_value,
                    created_at=now,
                    activated_at=now,
                    is_active=True,
                    version=new_version_num,
                )

                secret.status = SecretStatus.ACTIVE

                # Record rotation in history
                secret.rotation_history.append({
                    'rotated_at': now.isoformat(),
                    'old_version': new_version_num - 1,
                    'new_version': new_version_num,
                })

                # Keep only last N rotations in history
                if len(secret.rotation_history) > 10:
                    secret.rotation_history = secret.rotation_history[-10:]

            # Persist to Redis if available
            await self._persist_to_redis(name, secret)

            # Execute rotation callbacks
            await self._execute_callbacks(name, new_value)

            logger.info(
                f"Secret rotated successfully",
                extra={
                    'secret_name': name,
                    'new_version': new_version_num,
                    'old_expires_at': (
                        secret.previous_version.expires_at.isoformat()
                        if secret.previous_version
                        else None
                    ),
                }
            )

            return True

        except (ValueError, OSError) as e:
            logger.exception(f"Error rotating secret '{name}': {e}")
            with self._lock:
                secret.status = SecretStatus.FAILED
            return False

    async def rollback_secret(self, name: str) -> bool:
        """
        Rollback a secret to its previous version.

        Args:
            name: Name of the secret to rollback

        Returns:
            True if rollback successful, False otherwise
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                logger.error(f"Cannot rollback: secret '{name}' not found")
                return False

            if not secret.previous_version:
                logger.error(f"Cannot rollback: no previous version for '{name}'")
                return False

            # Swap versions
            old_current = secret.current_version
            secret.current_version = secret.previous_version
            secret.current_version.is_active = True
            secret.current_version.expires_at = None

            secret.previous_version = old_current
            if secret.previous_version:
                secret.previous_version.is_active = False
                secret.previous_version.expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=secret.overlap_seconds
                )

            secret.status = SecretStatus.ACTIVE

        # Persist to Redis if available
        await self._persist_to_redis(name, secret)

        logger.warning(
            f"Secret rolled back",
            extra={
                'secret_name': name,
                'rolled_back_to_version': secret.current_version.version,
            }
        )

        return True

    def on_rotation(self, name: str, callback: Callable[[str], Any]):
        """
        Register a callback to be executed when a secret is rotated.

        Args:
            name: Name of the secret
            callback: Function to call with new secret value
        """
        if name not in self._rotation_callbacks:
            self._rotation_callbacks[name] = []
        self._rotation_callbacks[name].append(callback)

    def get_secret_info(self, name: str) -> Optional[dict]:
        """
        Get metadata about a secret (without exposing the value).

        Args:
            name: Name of the secret

        Returns:
            Dictionary with secret metadata
        """
        with self._lock:
            secret = self._secrets.get(name)
            if not secret:
                return None

            return {
                'name': secret.name,
                'status': secret.status.value,
                'overlap_seconds': secret.overlap_seconds,
                'current_version': (
                    secret.current_version.to_dict()
                    if secret.current_version
                    else None
                ),
                'previous_version': (
                    secret.previous_version.to_dict()
                    if secret.previous_version
                    else None
                ),
                'rotation_count': len(secret.rotation_history),
            }

    def list_secrets(self) -> list[dict]:
        """
        List all registered secrets with their metadata.

        Returns:
            List of secret metadata dictionaries
        """
        with self._lock:
            return [
                self.get_secret_info(name)
                for name in self._secrets.keys()
            ]

    async def _persist_to_redis(self, name: str, secret: RotatableSecret):
        """Persist secret metadata to Redis (values are NOT stored in Redis)"""
        if not self._redis:
            return

        try:
            key = f"{self._redis_prefix}{name}:meta"
            meta = {
                'status': secret.status.value,
                'current_version': secret.current_version.version if secret.current_version else None,
                'last_rotated': datetime.now(timezone.utc).isoformat(),
            }
            self._redis.setex(key, timedelta(days=7), json.dumps(meta))
        except (ValueError, OSError) as e:
            logger.error(f"Failed to persist secret metadata to Redis: {e}")

    async def _execute_callbacks(self, name: str, new_value: str):
        """Execute rotation callbacks"""
        callbacks = self._rotation_callbacks.get(name, [])
        for callback in callbacks:
            try:
                result = callback(new_value)
                if asyncio.iscoroutine(result):
                    await result
            except (ValueError, OSError) as e:
                logger.exception(f"Error in rotation callback for '{name}': {e}")


# Global secret manager instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get the global secret manager instance"""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
    return _secret_manager


def init_secret_manager(redis_client=None) -> SecretManager:
    """
    Initialize the global secret manager with optional Redis backing.

    Args:
        redis_client: Optional Redis client for distributed storage

    Returns:
        The initialized SecretManager
    """
    global _secret_manager
    _secret_manager = SecretManager(redis_client=redis_client)
    return _secret_manager


# Convenience decorators
def uses_secret(secret_name: str, param_name: str = 'secret'):
    """
    Decorator to inject a secret value into a function.

    Args:
        secret_name: Name of the secret to inject
        param_name: Parameter name to inject the secret as

    Example:
        @uses_secret('api_key', 'key')
        async def call_api(key: str):
            # key will be the current value of 'api_key' secret
            pass
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            kwargs[param_name] = get_secret_manager().get_secret(secret_name)
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            kwargs[param_name] = get_secret_manager().get_secret(secret_name)
            return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# Export public interface
__all__ = [
    'SecretStatus',
    'SecretVersion',
    'RotatableSecret',
    'SecretManager',
    'get_secret_manager',
    'init_secret_manager',
    'uses_secret',
]
