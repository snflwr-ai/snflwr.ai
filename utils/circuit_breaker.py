"""
Circuit Breaker Pattern for External Service Calls

Prevents cascading failures by failing fast when a service is unhealthy.
Implements the three-state circuit breaker pattern:
- CLOSED: Normal operation, requests go through
- OPEN: Service is failing, requests fail immediately without calling service
- HALF_OPEN: Testing if service has recovered

Usage:
    breaker = CircuitBreaker("ollama", failure_threshold=5, recovery_timeout=30)

    # Sync usage
    if breaker.can_execute():
        try:
            result = call_external_service()
            breaker.record_success()
        except Exception as e:
            breaker.record_failure()
            raise
    else:
        raise CircuitOpenError("Ollama circuit is open")

    # Async usage with decorator
    @breaker.async_protected
    async def call_ollama():
        ...
"""

import time
import threading
from enum import Enum
from typing import Callable, Optional, Any
from functools import wraps
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)

# Import metrics (lazy to avoid circular imports)
_metrics_available = False
try:
    from utils.metrics import (
        record_circuit_breaker_state,
        record_circuit_breaker_transition,
        record_circuit_breaker_request,
        circuit_breaker_failure_count
    )
    _metrics_available = True
except ImportError:
    pass


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitOpenError(Exception):
    """Raised when circuit is open and calls are blocked"""
    def __init__(self, service_name: str, time_until_retry: float):
        self.service_name = service_name
        self.time_until_retry = time_until_retry
        super().__init__(
            f"Circuit breaker for '{service_name}' is OPEN. "
            f"Service is unavailable. Retry in {time_until_retry:.1f}s"
        )


@dataclass
class CircuitStats:
    """Statistics for circuit breaker monitoring"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0  # Requests blocked by open circuit
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    state_changes: int = 0


class CircuitBreaker:
    """
    Thread-safe circuit breaker for external service calls.

    Args:
        name: Identifier for this circuit (e.g., "ollama", "redis")
        failure_threshold: Number of consecutive failures to open circuit
        recovery_timeout: Seconds to wait before testing recovery
        success_threshold: Consecutive successes in half-open to close circuit
        half_open_max_calls: Max concurrent calls allowed in half-open state
    """

    # Global registry of all circuit breakers
    _instances: dict = {}
    _registry_lock = threading.Lock()

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        half_open_max_calls: int = 1
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = threading.RLock()
        self._stats = CircuitStats()

        # Register this instance
        with self._registry_lock:
            self._instances[name] = self

        logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"threshold={failure_threshold}, recovery={recovery_timeout}s"
        )

    @classmethod
    def get(cls, name: str) -> Optional['CircuitBreaker']:
        """Get a circuit breaker by name"""
        with cls._registry_lock:
            return cls._instances.get(name)

    @classmethod
    def get_all_stats(cls) -> dict:
        """Get stats for all circuit breakers"""
        with cls._registry_lock:
            return {
                name: breaker.get_stats()
                for name, breaker in cls._instances.items()
            }

    @property
    def state(self) -> CircuitState:
        """Current circuit state"""
        with self._lock:
            return self._state

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

    def can_execute(self) -> bool:
        """
        Check if a request can be executed.
        Returns True if circuit allows the request.
        """
        with self._lock:
            self._stats.total_requests += 1

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if self._should_attempt_recovery():
                    self._transition_to_half_open()
                    return True
                else:
                    self._stats.rejected_requests += 1
                    if _metrics_available:
                        record_circuit_breaker_request(self.name, 'rejected')
                    return False

            if self._state == CircuitState.HALF_OPEN:
                # Only allow limited concurrent calls in half-open
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                else:
                    self._stats.rejected_requests += 1
                    if _metrics_available:
                        record_circuit_breaker_request(self.name, 'rejected')
                    return False

            return False

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to try recovery"""
        if self._last_failure_time is None:
            return True
        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    def time_until_retry(self) -> float:
        """Seconds until circuit will attempt recovery"""
        if self._state != CircuitState.OPEN:
            return 0.0
        if self._last_failure_time is None:
            return 0.0
        elapsed = time.time() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def record_success(self):
        """Record a successful call"""
        with self._lock:
            self._stats.successful_requests += 1
            self._stats.last_success_time = time.time()
            self._stats.consecutive_successes += 1
            self._stats.consecutive_failures = 0

            if _metrics_available:
                record_circuit_breaker_request(self.name, 'success')
                circuit_breaker_failure_count.labels(service=self.name).set(0)

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._success_count += 1

                if self._success_count >= self.success_threshold:
                    self._transition_to_closed()

            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, error: Optional[Exception] = None):
        """Record a failed call"""
        with self._lock:
            self._stats.failed_requests += 1
            self._stats.last_failure_time = time.time()
            self._stats.consecutive_failures += 1
            self._stats.consecutive_successes = 0
            self._last_failure_time = time.time()

            if _metrics_available:
                record_circuit_breaker_request(self.name, 'failure')
                circuit_breaker_failure_count.labels(service=self.name).set(
                    self._stats.consecutive_failures
                )

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls = max(0, self._half_open_calls - 1)
                # Any failure in half-open immediately opens circuit
                self._transition_to_open()
                logger.warning(
                    f"Circuit '{self.name}' recovery failed, reopening circuit"
                )

            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1

                if self._failure_count >= self.failure_threshold:
                    self._transition_to_open()
                    logger.error(
                        f"Circuit '{self.name}' OPENED after {self._failure_count} failures. "
                        f"Error: {error}"
                    )

    def _transition_to_open(self):
        """Transition to OPEN state"""
        old_state = self._state.value
        self._state = CircuitState.OPEN
        self._stats.state_changes += 1
        self._success_count = 0
        logger.warning(
            f"Circuit '{self.name}' is now OPEN. "
            f"Requests will fail fast for {self.recovery_timeout}s"
        )
        if _metrics_available:
            record_circuit_breaker_transition(self.name, old_state, 'open')

    def _transition_to_half_open(self):
        """Transition to HALF_OPEN state"""
        old_state = self._state.value
        self._state = CircuitState.HALF_OPEN
        self._stats.state_changes += 1
        self._success_count = 0
        self._half_open_calls = 0
        logger.info(f"Circuit '{self.name}' is now HALF_OPEN. Testing recovery...")
        if _metrics_available:
            record_circuit_breaker_transition(self.name, old_state, 'half_open')

    def _transition_to_closed(self):
        """Transition to CLOSED state"""
        old_state = self._state.value
        self._state = CircuitState.CLOSED
        self._stats.state_changes += 1
        self._failure_count = 0
        self._success_count = 0
        logger.info(f"Circuit '{self.name}' is now CLOSED. Service recovered.")
        if _metrics_available:
            record_circuit_breaker_transition(self.name, old_state, 'closed')

    def force_open(self):
        """Manually force circuit open (for maintenance)"""
        with self._lock:
            self._transition_to_open()
            self._last_failure_time = time.time()
            logger.warning(f"Circuit '{self.name}' manually forced OPEN")

    def force_close(self):
        """Manually force circuit closed (admin override)"""
        with self._lock:
            self._transition_to_closed()
            logger.warning(f"Circuit '{self.name}' manually forced CLOSED")

    def reset(self):
        """Reset circuit to initial state"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._half_open_calls = 0
            self._stats = CircuitStats()
            logger.info(f"Circuit '{self.name}' reset to initial state")

    def get_stats(self) -> dict:
        """Get current circuit statistics"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "time_until_retry": self.time_until_retry(),
                "stats": {
                    "total_requests": self._stats.total_requests,
                    "successful": self._stats.successful_requests,
                    "failed": self._stats.failed_requests,
                    "rejected": self._stats.rejected_requests,
                    "consecutive_failures": self._stats.consecutive_failures,
                    "state_changes": self._stats.state_changes
                }
            }

    def protected(self, func: Callable) -> Callable:
        """
        Decorator to protect a synchronous function with this circuit breaker.

        Usage:
            @breaker.protected
            def call_external_service():
                ...
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self.can_execute():
                raise CircuitOpenError(self.name, self.time_until_retry())

            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:  # Intentional catch-all: circuit breaker tracks all failure types
                self.record_failure(e)
                raise

        return wrapper

    def async_protected(self, func: Callable) -> Callable:
        """
        Decorator to protect an async function with this circuit breaker.

        Usage:
            @breaker.async_protected
            async def call_external_service():
                ...
        """
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not self.can_execute():
                raise CircuitOpenError(self.name, self.time_until_retry())

            try:
                result = await func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:  # Intentional catch-all: circuit breaker tracks all failure types
                self.record_failure(e)
                raise

        return wrapper


# Pre-configured circuit breaker for Ollama
ollama_circuit = CircuitBreaker(
    name="ollama",
    failure_threshold=5,       # Open after 5 consecutive failures
    recovery_timeout=30.0,     # Wait 30s before testing recovery
    success_threshold=2,       # Need 2 successes to close
    half_open_max_calls=1      # Only 1 test call in half-open
)


# Export public interface
__all__ = [
    'CircuitBreaker',
    'CircuitState',
    'CircuitOpenError',
    'CircuitStats',
    'ollama_circuit'
]
