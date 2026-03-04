"""
snflwr.ai Utilities Module
Shared utility functions and helper classes
"""

from .logger import (
    get_logger,
    log_safety_incident,
    log_performance_metric,
    get_performance_statistics,
    log_system_startup,
    logger_manager
)

from .ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaConnectionError,
    OllamaTimeoutError,
    ollama_client
)

__all__ = [
    # Logging
    'get_logger',
    'log_safety_incident',
    'log_performance_metric',
    'get_performance_statistics',
    'log_system_startup',
    'logger_manager',

    # Ollama Integration
    'OllamaClient',
    'OllamaError',
    'OllamaConnectionError',
    'OllamaTimeoutError',
    'ollama_client',
]
