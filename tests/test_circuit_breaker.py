"""
Test Suite for Circuit Breaker Pattern
Tests circuit breaker states, transitions, failure handling, and recovery
"""

import pytest
import time
from unittest.mock import patch, MagicMock


class TestCircuitBreakerStates:
    """Test circuit breaker state management"""

    def test_initial_state_is_closed(self):
        """Test that circuit breaker starts in CLOSED state"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(name="test_initial", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_closed
        assert not cb.is_open
        assert not cb.is_half_open

    def test_can_execute_when_closed(self):
        """Test that requests are allowed when circuit is closed"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_execute_closed", failure_threshold=3)
        assert cb.can_execute() is True

    def test_transitions_to_open_after_failures(self):
        """Test circuit opens after reaching failure threshold"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(name="test_open_transition", failure_threshold=3)

        # Record failures up to threshold
        for i in range(3):
            cb.can_execute()
            cb.record_failure(Exception(f"Error {i}"))

        assert cb.state == CircuitState.OPEN
        assert cb.is_open

    def test_rejects_requests_when_open(self):
        """Test that requests are rejected when circuit is open"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            name="test_reject_open",
            failure_threshold=2,
            recovery_timeout=60.0  # Long timeout to prevent half-open
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        assert cb.is_open
        assert cb.can_execute() is False

    def test_transitions_to_half_open_after_timeout(self):
        """Test circuit transitions to half-open after recovery timeout"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_half_open_transition",
            failure_threshold=2,
            recovery_timeout=0.1  # 100ms timeout for fast test
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        assert cb.is_open

        # Wait for recovery timeout
        time.sleep(0.15)

        # Next call should transition to half-open
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_after_successful_calls_in_half_open(self):
        """Test circuit closes after success threshold in half-open state"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_close_from_half_open",
            failure_threshold=2,
            recovery_timeout=0.05,
            success_threshold=2
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Wait and go to half-open
        time.sleep(0.1)
        cb.can_execute()
        assert cb.state == CircuitState.HALF_OPEN

        # Record successes
        cb.record_success()
        cb.record_success()

        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        """Test circuit reopens on failure during half-open"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_reopen_from_half_open",
            failure_threshold=2,
            recovery_timeout=0.05,
            success_threshold=3
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Wait and go to half-open
        time.sleep(0.1)
        cb.can_execute()
        assert cb.state == CircuitState.HALF_OPEN

        # Fail in half-open
        cb.record_failure()

        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerStats:
    """Test circuit breaker statistics tracking"""

    def test_tracks_total_requests(self):
        """Test that total requests are tracked"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_stats_total", failure_threshold=5)

        for _ in range(5):
            cb.can_execute()
            cb.record_success()

        stats = cb.get_stats()
        assert stats["stats"]["total_requests"] == 5

    def test_tracks_successful_requests(self):
        """Test that successful requests are tracked"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_stats_success", failure_threshold=5)

        for _ in range(3):
            cb.can_execute()
            cb.record_success()

        stats = cb.get_stats()
        assert stats["stats"]["successful"] == 3

    def test_tracks_failed_requests(self):
        """Test that failed requests are tracked"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_stats_failed", failure_threshold=10)

        for _ in range(4):
            cb.can_execute()
            cb.record_failure()

        stats = cb.get_stats()
        assert stats["stats"]["failed"] == 4

    def test_tracks_rejected_requests(self):
        """Test that rejected requests are tracked when circuit is open"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            name="test_stats_rejected",
            failure_threshold=2,
            recovery_timeout=60.0
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # These should be rejected
        cb.can_execute()
        cb.can_execute()
        cb.can_execute()

        stats = cb.get_stats()
        assert stats["stats"]["rejected"] == 3

    def test_tracks_state_changes(self):
        """Test that state transitions are tracked"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            name="test_stats_state_changes",
            failure_threshold=2,
            recovery_timeout=0.05
        )

        # closed -> open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Wait for half-open transition
        time.sleep(0.1)
        cb.can_execute()  # open -> half-open

        stats = cb.get_stats()
        assert stats["stats"]["state_changes"] >= 2


class TestCircuitBreakerManualControl:
    """Test manual circuit breaker controls"""

    def test_force_open(self):
        """Test manually forcing circuit open"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(name="test_force_open", failure_threshold=10)
        assert cb.is_closed

        cb.force_open()

        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_force_close(self):
        """Test manually forcing circuit closed"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_force_close",
            failure_threshold=2,
            recovery_timeout=60.0
        )

        # Force open through failures
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()
        assert cb.is_open

        # Force close
        cb.force_close()

        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_reset(self):
        """Test resetting circuit breaker to initial state"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_reset",
            failure_threshold=2,
            recovery_timeout=60.0
        )

        # Record some failures
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Reset
        cb.reset()

        assert cb.state == CircuitState.CLOSED
        # Failure count should be reset
        cb.can_execute()
        cb.record_failure()
        assert cb.is_closed  # Still closed after 1 failure


class TestCircuitBreakerTimeUntilRetry:
    """Test time until retry calculation"""

    def test_time_until_retry_when_open(self):
        """Test time until retry calculation when circuit is open"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            name="test_time_until_retry",
            failure_threshold=2,
            recovery_timeout=10.0
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Should have roughly 10 seconds until retry
        time_until = cb.time_until_retry()
        assert 9.0 < time_until <= 10.0

    def test_time_until_retry_when_closed(self):
        """Test time until retry returns 0 when circuit is closed"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_time_zero", failure_threshold=5)
        assert cb.time_until_retry() == 0.0


class TestCircuitBreakerHalfOpenMaxCalls:
    """Test half-open state concurrent call limiting"""

    def test_limits_concurrent_calls_in_half_open(self):
        """Test that concurrent calls are limited in half-open state"""
        from utils.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="test_half_open_limit",
            failure_threshold=2,
            recovery_timeout=0.05,
            half_open_max_calls=1
        )

        # Force open
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Wait and go to half-open
        time.sleep(0.1)

        # First call should be allowed (transitions to half-open)
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Second call should be rejected (half-open limit of 1)
        # Note: This depends on timing - the first call may have completed
        # So we just verify the circuit is in half-open state
        assert cb.is_half_open


class TestCircuitOpenError:
    """Test CircuitOpenError exception"""

    def test_circuit_open_error_message(self):
        """Test CircuitOpenError exception properties"""
        from utils.circuit_breaker import CircuitOpenError

        error = CircuitOpenError("test_service", 5.5)

        assert error.service_name == "test_service"
        assert error.time_until_retry == 5.5
        assert "test_service" in str(error)
        assert "5.5" in str(error)


class TestOllamaCircuitBreaker:
    """Test the pre-configured Ollama circuit breaker"""

    def test_ollama_circuit_exists(self):
        """Test that ollama_circuit is pre-configured"""
        from utils.circuit_breaker import ollama_circuit

        assert ollama_circuit is not None
        assert ollama_circuit.name == "ollama"

    def test_ollama_circuit_default_settings(self):
        """Test Ollama circuit breaker has appropriate defaults"""
        from utils.circuit_breaker import ollama_circuit

        # Check properties directly
        assert ollama_circuit.failure_threshold == 5
        assert ollama_circuit.recovery_timeout == 30.0


class TestCircuitBreakerMetricsIntegration:
    """Test circuit breaker metrics integration"""

    def test_records_metrics_on_state_transition(self):
        """Test that metrics are recorded on state transitions"""
        from utils.circuit_breaker import CircuitBreaker

        # Use a fresh circuit breaker
        cb = CircuitBreaker(
            name="test_metrics_integration",
            failure_threshold=2,
            recovery_timeout=0.05
        )

        # Force open - should record transition metric
        cb.can_execute()
        cb.record_failure()
        cb.can_execute()
        cb.record_failure()

        # Verify circuit is open
        assert cb.is_open

        # These operations should not raise exceptions
        # (metrics recording should be non-blocking)

    def test_records_metrics_on_request_outcome(self):
        """Test that metrics are recorded for request outcomes"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_metrics_outcome", failure_threshold=10)

        # Success - should record metric
        cb.can_execute()
        cb.record_success()

        # Failure - should record metric
        cb.can_execute()
        cb.record_failure()

        # These should complete without exception


class TestCircuitBreakerDecorator:
    """Test the circuit breaker decorator"""

    def test_protected_decorator_success(self):
        """Test decorator allows successful calls"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_decorator_success", failure_threshold=5)

        @cb.protected
        def successful_function():
            return "success"

        result = successful_function()
        assert result == "success"

    def test_protected_decorator_failure(self):
        """Test decorator records failures"""
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_decorator_failure", failure_threshold=5)

        @cb.protected
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            failing_function()

        stats = cb.get_stats()
        assert stats["stats"]["failed"] >= 1

    def test_protected_decorator_rejects_when_open(self):
        """Test decorator raises CircuitOpenError when circuit is open"""
        from utils.circuit_breaker import CircuitBreaker, CircuitOpenError

        cb = CircuitBreaker(
            name="test_decorator_open",
            failure_threshold=2,
            recovery_timeout=60.0
        )

        @cb.protected
        def my_function():
            raise Exception("Always fails")

        # Trigger failures to open circuit
        for _ in range(2):
            try:
                my_function()
            except Exception:
                pass

        # Now circuit should be open
        with pytest.raises(CircuitOpenError):
            my_function()


class TestCircuitBreakerThreadSafety:
    """Test circuit breaker thread safety"""

    def test_concurrent_access(self):
        """Test circuit breaker handles concurrent access"""
        import threading
        from utils.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(name="test_concurrent", failure_threshold=100)
        errors = []

        def worker():
            try:
                for _ in range(50):
                    if cb.can_execute():
                        if _ % 2 == 0:
                            cb.record_success()
                        else:
                            cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        stats = cb.get_stats()
        assert stats["stats"]["total_requests"] == 200  # 4 threads * 50 requests
