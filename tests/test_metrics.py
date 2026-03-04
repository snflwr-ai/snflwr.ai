"""
Test Suite for Prometheus Metrics Module
Tests metric definitions, helper functions, and metric recording
"""

import pytest
from unittest.mock import patch, MagicMock
import time

pytest.importorskip("prometheus_client", reason="prometheus_client not installed")


class TestMetricsModule:
    """Test the metrics module imports and basic functionality"""

    def test_metrics_import(self):
        """Test that metrics module can be imported"""
        from utils.metrics import (
            http_requests_total,
            http_request_duration_seconds,
            circuit_breaker_state,
            cache_operations_total,
            rate_limiter_requests_total,
            llm_requests_total,
            safety_checks_total
        )
        assert http_requests_total is not None
        assert http_request_duration_seconds is not None
        assert circuit_breaker_state is not None
        assert cache_operations_total is not None
        assert rate_limiter_requests_total is not None
        assert llm_requests_total is not None
        assert safety_checks_total is not None

    def test_get_metrics_returns_bytes(self):
        """Test that get_metrics returns prometheus format"""
        from utils.metrics import get_metrics
        metrics_output = get_metrics()
        assert isinstance(metrics_output, bytes)
        # Should contain HELP and TYPE declarations
        assert b'# HELP' in metrics_output or b'# TYPE' in metrics_output

    def test_get_content_type(self):
        """Test that content type is correct for Prometheus"""
        from utils.metrics import get_content_type
        content_type = get_content_type()
        assert 'text/plain' in content_type or 'text/openmetrics' in content_type

    def test_init_app_info(self):
        """Test app info initialization"""
        from utils.metrics import init_app_info, get_metrics
        init_app_info(version="1.0.0", environment="test")
        metrics = get_metrics().decode()
        assert 'snflwr_app_info' in metrics


class TestHTTPMetrics:
    """Test HTTP request metrics"""

    def test_record_request(self):
        """Test recording an HTTP request"""
        from utils.metrics import record_request, http_requests_total

        # Get initial value
        initial = http_requests_total.labels(
            method="GET", endpoint="/test", status="200"
        )._value.get()

        # Record a request
        record_request("GET", "/test", 200)

        # Verify counter incremented
        new_value = http_requests_total.labels(
            method="GET", endpoint="/test", status="200"
        )._value.get()
        assert new_value == initial + 1

    def test_track_request_duration_context_manager(self):
        """Test request duration tracking context manager"""
        from utils.metrics import track_request_duration, http_requests_in_progress

        with track_request_duration("POST", "/api/test"):
            # Simulate some work
            time.sleep(0.01)

        # After context manager, in_progress should be decremented
        # This test just verifies no exceptions are raised


class TestCircuitBreakerMetrics:
    """Test circuit breaker metrics"""

    def test_record_circuit_breaker_state(self):
        """Test recording circuit breaker state"""
        from utils.metrics import record_circuit_breaker_state, circuit_breaker_state

        record_circuit_breaker_state("test_service", "closed")
        value = circuit_breaker_state.labels(service="test_service")._value.get()
        assert value == 0  # closed = 0

        record_circuit_breaker_state("test_service", "open")
        value = circuit_breaker_state.labels(service="test_service")._value.get()
        assert value == 1  # open = 1

        record_circuit_breaker_state("test_service", "half_open")
        value = circuit_breaker_state.labels(service="test_service")._value.get()
        assert value == 2  # half_open = 2

    def test_record_circuit_breaker_transition(self):
        """Test recording circuit breaker state transitions"""
        from utils.metrics import (
            record_circuit_breaker_transition,
            circuit_breaker_state_transitions_total
        )

        initial = circuit_breaker_state_transitions_total.labels(
            service="test_svc", from_state="closed", to_state="open"
        )._value.get()

        record_circuit_breaker_transition("test_svc", "closed", "open")

        new_value = circuit_breaker_state_transitions_total.labels(
            service="test_svc", from_state="closed", to_state="open"
        )._value.get()
        assert new_value == initial + 1

    def test_record_circuit_breaker_request(self):
        """Test recording circuit breaker request outcomes"""
        from utils.metrics import record_circuit_breaker_request, circuit_breaker_requests_total

        initial_success = circuit_breaker_requests_total.labels(
            service="test_cb", result="success"
        )._value.get()
        initial_failure = circuit_breaker_requests_total.labels(
            service="test_cb", result="failure"
        )._value.get()

        record_circuit_breaker_request("test_cb", "success")
        record_circuit_breaker_request("test_cb", "failure")

        assert circuit_breaker_requests_total.labels(
            service="test_cb", result="success"
        )._value.get() == initial_success + 1
        assert circuit_breaker_requests_total.labels(
            service="test_cb", result="failure"
        )._value.get() == initial_failure + 1


class TestCacheMetrics:
    """Test cache operation metrics"""

    def test_record_cache_operation_hit(self):
        """Test recording cache hit"""
        from utils.metrics import record_cache_operation, cache_operations_total

        initial = cache_operations_total.labels(
            operation="get", result="hit"
        )._value.get()

        record_cache_operation("get", "hit", 0.001)

        assert cache_operations_total.labels(
            operation="get", result="hit"
        )._value.get() == initial + 1

    def test_record_cache_operation_miss(self):
        """Test recording cache miss"""
        from utils.metrics import record_cache_operation, cache_operations_total

        initial = cache_operations_total.labels(
            operation="get", result="miss"
        )._value.get()

        record_cache_operation("get", "miss", 0.002)

        assert cache_operations_total.labels(
            operation="get", result="miss"
        )._value.get() == initial + 1

    def test_record_cache_operation_with_duration(self):
        """Test that cache operation duration is recorded"""
        from utils.metrics import record_cache_operation, cache_operation_duration_seconds

        # Record with duration
        record_cache_operation("set", "success", 0.05)

        # Verify histogram was updated (check that observe was called)
        # Histograms don't have simple _value, so we just verify no exception


class TestRateLimiterMetrics:
    """Test rate limiter metrics"""

    def test_record_rate_limit_check_allowed(self):
        """Test recording allowed rate limit check"""
        from utils.metrics import record_rate_limit_check, rate_limiter_requests_total

        initial = rate_limiter_requests_total.labels(
            result="allowed"
        )._value.get()

        record_rate_limit_check(True)

        assert rate_limiter_requests_total.labels(
            result="allowed"
        )._value.get() == initial + 1

    def test_record_rate_limit_check_rejected(self):
        """Test recording rejected rate limit check"""
        from utils.metrics import record_rate_limit_check, rate_limiter_requests_total

        initial = rate_limiter_requests_total.labels(
            result="rejected"
        )._value.get()

        record_rate_limit_check(False)

        assert rate_limiter_requests_total.labels(
            result="rejected"
        )._value.get() == initial + 1


class TestLLMMetrics:
    """Test LLM/Ollama metrics"""

    def test_record_llm_request(self):
        """Test recording LLM request"""
        from utils.metrics import record_llm_request, llm_requests_total, llm_tokens_total

        initial_requests = llm_requests_total.labels(
            model="llama3", operation="generate", result="success"
        )._value.get()
        initial_prompt_tokens = llm_tokens_total.labels(
            model="llama3", type="prompt"
        )._value.get()

        record_llm_request(
            model="llama3",
            operation="generate",
            result="success",
            duration=1.5,
            prompt_tokens=100,
            completion_tokens=50
        )

        assert llm_requests_total.labels(
            model="llama3", operation="generate", result="success"
        )._value.get() == initial_requests + 1
        assert llm_tokens_total.labels(
            model="llama3", type="prompt"
        )._value.get() == initial_prompt_tokens + 100


class TestSafetyMetrics:
    """Test safety pipeline metrics"""

    def test_record_safety_check(self):
        """Test recording safety check"""
        from utils.metrics import record_safety_check, safety_checks_total

        initial = safety_checks_total.labels(
            layer="input_filter", result="pass"
        )._value.get()

        record_safety_check("input_filter", "pass", 0.005)

        assert safety_checks_total.labels(
            layer="input_filter", result="pass"
        )._value.get() == initial + 1

    def test_record_safety_incident(self):
        """Test recording safety incident"""
        from utils.metrics import record_safety_incident, safety_incidents_total

        initial = safety_incidents_total.labels(
            severity="high", type="blocked_content"
        )._value.get()

        record_safety_incident("high", "blocked_content")

        assert safety_incidents_total.labels(
            severity="high", type="blocked_content"
        )._value.get() == initial + 1


class TestTrackLLMRequestDecorator:
    """Test the track_llm_request decorator"""

    def test_decorator_on_sync_function(self):
        """Test decorator works on sync functions"""
        from utils.metrics import track_llm_request, llm_requests_total

        @track_llm_request(model="test_model", operation="test_op")
        def sync_function():
            return "result"

        initial = llm_requests_total.labels(
            model="test_model", operation="test_op", result="success"
        )._value.get()

        result = sync_function()

        assert result == "result"
        assert llm_requests_total.labels(
            model="test_model", operation="test_op", result="success"
        )._value.get() == initial + 1

    def test_decorator_on_sync_function_error(self):
        """Test decorator records errors on sync functions"""
        from utils.metrics import track_llm_request, llm_requests_total

        @track_llm_request(model="error_model", operation="error_op")
        def error_function():
            raise ValueError("Test error")

        initial = llm_requests_total.labels(
            model="error_model", operation="error_op", result="error"
        )._value.get()

        with pytest.raises(ValueError):
            error_function()

        assert llm_requests_total.labels(
            model="error_model", operation="error_op", result="error"
        )._value.get() == initial + 1

    @pytest.mark.asyncio
    async def test_decorator_on_async_function(self):
        """Test decorator works on async functions"""
        from utils.metrics import track_llm_request, llm_requests_total

        @track_llm_request(model="async_model", operation="async_op")
        async def async_function():
            return "async_result"

        initial = llm_requests_total.labels(
            model="async_model", operation="async_op", result="success"
        )._value.get()

        result = await async_function()

        assert result == "async_result"
        assert llm_requests_total.labels(
            model="async_model", operation="async_op", result="success"
        )._value.get() == initial + 1
