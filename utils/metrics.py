"""
Prometheus Metrics for snflwr.ai

Provides observability metrics for monitoring the application in production.
Metrics are exposed at /metrics endpoint in Prometheus format.

Metric Types:
- Counter: Monotonically increasing values (requests, errors)
- Gauge: Values that can go up and down (active connections, cache size)
- Histogram: Distribution of values (latencies, request sizes)
- Summary: Similar to histogram but with quantiles

Usage:
    from utils.metrics import (
        http_requests_total,
        http_request_duration_seconds,
        record_request
    )

    # In middleware or endpoint
    with http_request_duration_seconds.labels(
        method="POST", endpoint="/chat"
    ).time():
        response = await process_request()

    http_requests_total.labels(
        method="POST", endpoint="/chat", status="200"
    ).inc()
"""

import time
from functools import wraps
from typing import Callable, Optional
from contextlib import contextmanager

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
)

from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# HTTP Request Metrics
# =============================================================================

http_requests_total = Counter(
    "snflwr_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "snflwr_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

http_requests_in_progress = Gauge(
    "snflwr_http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)


# =============================================================================
# Circuit Breaker Metrics
# =============================================================================

circuit_breaker_state = Gauge(
    "snflwr_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["service"],
)

circuit_breaker_state_transitions_total = Counter(
    "snflwr_circuit_breaker_state_transitions_total",
    "Total circuit breaker state transitions",
    ["service", "from_state", "to_state"],
)

circuit_breaker_requests_total = Counter(
    "snflwr_circuit_breaker_requests_total",
    "Total requests through circuit breaker",
    ["service", "result"],  # result: success, failure, rejected
)

circuit_breaker_failure_count = Gauge(
    "snflwr_circuit_breaker_failure_count",
    "Current consecutive failure count",
    ["service"],
)


# =============================================================================
# Rate Limiter Metrics
# =============================================================================

rate_limiter_requests_total = Counter(
    "snflwr_rate_limiter_requests_total",
    "Total rate limiter checks",
    ["result"],  # result: allowed, rejected
)

rate_limiter_current_count = Gauge(
    "snflwr_rate_limiter_current_count",
    "Current request count in rate limit window",
    ["identifier"],
)


# =============================================================================
# Redis Cache Metrics
# =============================================================================

cache_operations_total = Counter(
    "snflwr_cache_operations_total",
    "Total cache operations",
    [
        "operation",
        "result",
    ],  # operation: get, set, delete; result: hit, miss, success, error
)

cache_operation_duration_seconds = Histogram(
    "snflwr_cache_operation_duration_seconds",
    "Cache operation duration in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)

cache_connection_pool_size = Gauge(
    "snflwr_cache_connection_pool_size",
    "Redis connection pool size",
    ["pool_type"],  # pool_type: active, available
)

redis_sentinel_failovers_total = Counter(
    "snflwr_redis_sentinel_failovers_total", "Total Redis Sentinel failovers"
)

redis_sentinel_slaves = Gauge(
    "snflwr_redis_sentinel_slaves", "Number of Redis Sentinel slave nodes"
)


# =============================================================================
# Ollama/LLM Metrics
# =============================================================================

llm_requests_total = Counter(
    "snflwr_llm_requests_total",
    "Total LLM requests",
    ["model", "operation", "result"],  # operation: generate, chat, embed
)

llm_request_duration_seconds = Histogram(
    "snflwr_llm_request_duration_seconds",
    "LLM request duration in seconds",
    ["model", "operation"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

llm_tokens_total = Counter(
    "snflwr_llm_tokens_total",
    "Total tokens processed",
    ["model", "type"],  # type: prompt, completion
)

llm_queue_size = Gauge("snflwr_llm_queue_size", "Current LLM request queue size")


# =============================================================================
# Safety Pipeline Metrics
# =============================================================================

safety_checks_total = Counter(
    "snflwr_safety_checks_total",
    "Total safety pipeline checks",
    ["layer", "result"],  # layer: input_filter, topic_blocker, etc; result: pass, block
)

safety_check_duration_seconds = Histogram(
    "snflwr_safety_check_duration_seconds",
    "Safety check duration in seconds",
    ["layer"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1),
)

safety_incidents_total = Counter(
    "snflwr_safety_incidents_total", "Total safety incidents", ["severity", "type"]
)


# =============================================================================
# Session Metrics
# =============================================================================

active_sessions = Gauge("snflwr_active_sessions", "Number of active user sessions")

session_operations_total = Counter(
    "snflwr_session_operations_total",
    "Total session operations",
    ["operation", "result"],  # operation: create, validate, refresh, expire
)


# =============================================================================
# Database Metrics
# =============================================================================

db_operations_total = Counter(
    "snflwr_db_operations_total",
    "Total database operations",
    ["operation", "table", "result"],
)

db_operation_duration_seconds = Histogram(
    "snflwr_db_operation_duration_seconds",
    "Database operation duration in seconds",
    ["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

db_connection_pool_size = Gauge(
    "snflwr_db_connection_pool_size",
    "Database connection pool size",
    ["state"],  # state: active, idle, total
)


# =============================================================================
# Celery Task Metrics
# =============================================================================

celery_task_failures = Counter(
    "snflwr_celery_task_failures_total", "Total Celery task failures", ["task_name"]
)

celery_task_retries = Counter(
    "snflwr_celery_task_retries_total", "Total Celery task retries", ["task_name"]
)

celery_task_successes = Counter(
    "snflwr_celery_task_successes_total", "Total Celery task successes", ["task_name"]
)

celery_task_duration_seconds = Histogram(
    "snflwr_celery_task_duration_seconds",
    "Celery task duration in seconds",
    ["task_name"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0),
)

celery_dead_letter_queue_size = Gauge(
    "snflwr_celery_dead_letter_queue_size", "Number of tasks in dead letter queue"
)


# =============================================================================
# Application Info
# =============================================================================

app_info = Info("snflwr_app", "snflwr.ai application information")


# =============================================================================
# Helper Functions
# =============================================================================


def init_app_info(version: str = "1.0.0", environment: str = "production"):
    """Initialize application info metric"""
    app_info.info(
        {"version": version, "environment": environment, "python_version": "3.11"}
    )


def get_metrics() -> bytes:
    """Generate Prometheus metrics output"""
    return generate_latest(REGISTRY)


def get_content_type() -> str:
    """Get Prometheus content type for HTTP response"""
    return CONTENT_TYPE_LATEST


@contextmanager
def track_request_duration(method: str, endpoint: str):
    """Context manager to track HTTP request duration"""
    http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(
            duration
        )
        http_requests_in_progress.labels(method=method, endpoint=endpoint).dec()


def record_request(method: str, endpoint: str, status: int):
    """Record an HTTP request"""
    http_requests_total.labels(
        method=method, endpoint=endpoint, status=str(status)
    ).inc()


def record_circuit_breaker_state(service: str, state: str):
    """Record circuit breaker state change"""
    state_values = {"closed": 0, "open": 1, "half_open": 2}
    circuit_breaker_state.labels(service=service).set(state_values.get(state, -1))


def record_circuit_breaker_transition(service: str, from_state: str, to_state: str):
    """Record circuit breaker state transition"""
    circuit_breaker_state_transitions_total.labels(
        service=service, from_state=from_state, to_state=to_state
    ).inc()
    record_circuit_breaker_state(service, to_state)


def record_circuit_breaker_request(service: str, result: str):
    """Record circuit breaker request outcome"""
    circuit_breaker_requests_total.labels(service=service, result=result).inc()


def record_cache_operation(operation: str, result: str, duration: float = None):
    """Record cache operation"""
    cache_operations_total.labels(operation=operation, result=result).inc()
    if duration is not None:
        cache_operation_duration_seconds.labels(operation=operation).observe(duration)


def record_llm_request(
    model: str,
    operation: str,
    result: str,
    duration: float = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
):
    """Record LLM request metrics"""
    llm_requests_total.labels(model=model, operation=operation, result=result).inc()

    if duration is not None:
        llm_request_duration_seconds.labels(model=model, operation=operation).observe(
            duration
        )

    if prompt_tokens > 0:
        llm_tokens_total.labels(model=model, type="prompt").inc(prompt_tokens)

    if completion_tokens > 0:
        llm_tokens_total.labels(model=model, type="completion").inc(completion_tokens)


def record_safety_check(layer: str, result: str, duration: float = None):
    """Record safety pipeline check"""
    safety_checks_total.labels(layer=layer, result=result).inc()
    if duration is not None:
        safety_check_duration_seconds.labels(layer=layer).observe(duration)


def record_safety_incident(severity: str, incident_type: str):
    """Record safety incident"""
    safety_incidents_total.labels(severity=severity, type=incident_type).inc()


def record_rate_limit_check(allowed: bool):
    """Record rate limiter check"""
    rate_limiter_requests_total.labels(
        result="allowed" if allowed else "rejected"
    ).inc()


def track_llm_request(model: str, operation: str):
    """Decorator to track LLM request metrics"""

    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                record_llm_request(model, operation, "success", duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                record_llm_request(model, operation, "error", duration)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                record_llm_request(model, operation, "success", duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                record_llm_request(model, operation, "error", duration)
                raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# =============================================================================
# Export public interface
# =============================================================================

__all__ = [
    # HTTP metrics
    "http_requests_total",
    "http_request_duration_seconds",
    "http_requests_in_progress",
    # Circuit breaker metrics
    "circuit_breaker_state",
    "circuit_breaker_state_transitions_total",
    "circuit_breaker_requests_total",
    "circuit_breaker_failure_count",
    # Rate limiter metrics
    "rate_limiter_requests_total",
    "rate_limiter_current_count",
    # Cache metrics
    "cache_operations_total",
    "cache_operation_duration_seconds",
    "cache_connection_pool_size",
    "redis_sentinel_failovers_total",
    "redis_sentinel_slaves",
    # LLM metrics
    "llm_requests_total",
    "llm_request_duration_seconds",
    "llm_tokens_total",
    "llm_queue_size",
    # Safety metrics
    "safety_checks_total",
    "safety_check_duration_seconds",
    "safety_incidents_total",
    # Session metrics
    "active_sessions",
    "session_operations_total",
    # Database metrics
    "db_operations_total",
    "db_operation_duration_seconds",
    "db_connection_pool_size",
    # Celery metrics
    "celery_task_failures",
    "celery_task_retries",
    "celery_task_successes",
    "celery_task_duration_seconds",
    "celery_dead_letter_queue_size",
    # App info
    "app_info",
    # Helper functions
    "init_app_info",
    "get_metrics",
    "get_content_type",
    "track_request_duration",
    "record_request",
    "record_circuit_breaker_state",
    "record_circuit_breaker_transition",
    "record_circuit_breaker_request",
    "record_cache_operation",
    "record_llm_request",
    "record_safety_check",
    "record_safety_incident",
    "record_rate_limit_check",
    "track_llm_request",
]
