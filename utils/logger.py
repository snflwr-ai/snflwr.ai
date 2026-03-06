# utils/logger.py
"""
Comprehensive Logging System for snflwr.ai
Production-grade structured logging with rotation, filtering, and diagnostics

Features:
- Correlation ID tracking for distributed tracing
- JSON structured logging for production observability
- Automatic context injection (request_id, user_id, session_id)
- Thread-safe operation
- Log rotation with configurable size limits
"""

import logging
import logging.handlers
import sys
import json
import os
import re
import contextvars
from pathlib import Path
from typing import Optional, Dict, Any, Union
from datetime import datetime, timezone
import threading
import traceback

from config import system_config


# =============================================================================
# Context Variables for Request Tracking
# =============================================================================

# These can be set by middleware and will be automatically included in logs
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id', default=None
)
user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'user_id', default=None
)
session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'session_id', default=None
)


def set_correlation_id(request_id: str) -> contextvars.Token:
    """Set the correlation ID for the current context"""
    return correlation_id_var.set(request_id)


def get_correlation_id() -> Optional[str]:
    """Get the correlation ID from the current context"""
    return correlation_id_var.get()


def set_user_context(user_id: str, session_id: Optional[str] = None):
    """Set user context for logging"""
    user_id_var.set(user_id)
    if session_id:
        session_id_var.set(session_id)


class CorrelationIDFilter(logging.Filter):
    """
    Logging filter that adds correlation ID and user context to log records.

    This filter automatically injects:
    - correlation_id: Request tracking ID from X-Request-ID header
    - user_id: Current user ID if set in context
    - session_id: Current session ID if set in context
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add context variables to log record"""
        record.correlation_id = correlation_id_var.get() or '-'
        record.user_id = user_id_var.get() or '-'
        record.session_id = session_id_var.get() or '-'
        return True



class PIISanitizer(logging.Filter):
    """
    Logging filter that scrubs PII patterns from log messages before they are written.

    Redacts:
    - Email addresses -> [EMAIL_REDACTED]
    - Long hex tokens (32+ chars) -> [TOKEN_REDACTED]
    - Password field values (password=, passwd=, pwd=) -> [PASSWORD_REDACTED]
    - IPv4 and IPv6 addresses -> [IP_REDACTED]
    - JWT tokens (eyJ... patterns) -> [JWT_REDACTED]

    Enabled by default. Disable via LOG_PII_SANITIZE=false env var.
    """

    # Compile patterns once at class level for efficiency
    _EMAIL_PATTERN = re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    )
    _TOKEN_PATTERN = re.compile(
        r'\b[0-9a-fA-F]{32,}\b'
    )
    _PASSWORD_PATTERN = re.compile(
        r'(password|passwd|pwd)\s*[=:]\s*\S+',
        re.IGNORECASE
    )
    _IPV4_PATTERN = re.compile(
        r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
        r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
    )
    _IPV6_PATTERN = re.compile(
        r'(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}'
        r'|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}'
        r'|(?:[0-9a-fA-F]{1,4}:){1,6}:'
    )
    _JWT_PATTERN = re.compile(
        r'eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+'
    )

    def __init__(self):
        super().__init__()
        enabled_str = os.getenv('LOG_PII_SANITIZE', 'true').lower()
        self.enabled = enabled_str != 'false'

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize PII from the log record message and args"""
        if not self.enabled:
            return True

        # Sanitize the formatted message
        if record.msg:
            record.msg = self._sanitize(str(record.msg))

        # Sanitize string args so %-formatting also gets cleaned
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )

        return True

    def _sanitize(self, text: str) -> str:
        """Apply all PII redaction patterns to a text string"""
        # Order matters: JWT before generic token (JWT contains long base64 segments)
        text = self._JWT_PATTERN.sub('[JWT_REDACTED]', text)
        text = self._EMAIL_PATTERN.sub('[EMAIL_REDACTED]', text)
        text = self._PASSWORD_PATTERN.sub(
            lambda m: m.group().split('=')[0] + '=[PASSWORD_REDACTED]'
            if '=' in m.group()
            else m.group().split(':')[0] + ':[PASSWORD_REDACTED]',
            text
        )
        text = self._TOKEN_PATTERN.sub('[TOKEN_REDACTED]', text)
        text = self._IPV4_PATTERN.sub('[IP_REDACTED]', text)
        text = self._IPV6_PATTERN.sub('[IP_REDACTED]', text)
        return text


class SnflwrFormatter(logging.Formatter):
    """
    Custom formatter with correlation ID support and structured output.

    Supports two modes:
    - Standard: Human-readable format with correlation ID prefix
    - Structured: JSON format for log aggregation systems (ELK, Datadog, etc.)
    """

    # ANSI Color codes for terminal output
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }

    RESET = '\033[0m'

    # Service metadata for structured logs
    SERVICE_NAME = os.getenv('SERVICE_NAME', 'snflwr-ai')
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    HOSTNAME = os.getenv('HOSTNAME', 'unknown')

    def __init__(self, use_color: bool = False, structured: bool = False,
                 include_extras: bool = True):
        super().__init__()
        self.use_color = use_color
        self.structured = structured
        self.include_extras = include_extras

        if not structured:
            # Include correlation_id in format
            self.fmt = '[%(correlation_id)s] ' + system_config.LOG_FORMAT
            self.datefmt = system_config.LOG_DATE_FORMAT

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with optional color and structure"""

        # Ensure context variables are present
        if not hasattr(record, 'correlation_id'):
            record.correlation_id = correlation_id_var.get() or '-'
        if not hasattr(record, 'user_id'):
            record.user_id = user_id_var.get() or '-'
        if not hasattr(record, 'session_id'):
            record.session_id = session_id_var.get() or '-'

        if self.structured:
            return self._format_structured(record)
        else:
            return self._format_standard(record)

    def _format_structured(self, record: logging.LogRecord) -> str:
        """
        Format as JSON for structured logging.

        Output is compatible with ELK Stack, Datadog, Splunk, etc.
        """
        log_data = {
            '@timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'service': {
                'name': self.SERVICE_NAME,
                'environment': self.ENVIRONMENT,
                'hostname': self.HOSTNAME,
            },
            'source': {
                'module': record.module,
                'function': record.funcName,
                'file': record.pathname,
                'line': record.lineno,
            },
            'trace': {
                'correlation_id': getattr(record, 'correlation_id', None),
                'user_id': getattr(record, 'user_id', None),
                'session_id': getattr(record, 'session_id', None),
            },
        }

        # Add exception info if present
        if record.exc_info:
            log_data['error'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'stack_trace': self.formatException(record.exc_info),
            }

        # Add extra fields from log call
        if self.include_extras and hasattr(record, '__dict__'):
            extras = {}
            skip_keys = {
                'name', 'msg', 'args', 'created', 'filename', 'funcName',
                'levelname', 'levelno', 'lineno', 'module', 'msecs',
                'pathname', 'process', 'processName', 'relativeCreated',
                'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                'correlation_id', 'user_id', 'session_id', 'message',
                'taskName',
            }
            for key, value in record.__dict__.items():
                if key not in skip_keys and not key.startswith('_'):
                    try:
                        # Ensure value is JSON serializable
                        json.dumps(value)
                        extras[key] = value
                    except (TypeError, ValueError):
                        extras[key] = str(value)

            if extras:
                log_data['extra'] = extras

        # Remove None values from trace
        log_data['trace'] = {k: v for k, v in log_data['trace'].items() if v and v != '-'}
        if not log_data['trace']:
            del log_data['trace']

        return json.dumps(log_data, default=str)

    def _format_standard(self, record: logging.LogRecord) -> str:
        """Format as standard text log with correlation ID"""
        formatter = logging.Formatter(self.fmt, self.datefmt)
        formatted = formatter.format(record)

        if self.use_color and sys.stdout.isatty():
            color = self.COLORS.get(record.levelname, '')
            return f"{color}{formatted}{self.RESET}"

        return formatted


class SafetyLogger:
    """Special logger for child safety incidents"""
    
    def __init__(self, log_dir: Path):
        self.log_file = log_dir / "safety_incidents.log"
        self.logger = logging.getLogger("snflwr.safety")
        self._lock = threading.Lock()
    
    def log_incident(self, incident_type: str, child_profile_id: str,
                     content: str, severity: str, metadata: Optional[Dict] = None):
        """Log a safety incident with full details"""
        
        with self._lock:
            incident_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'type': incident_type,
                'profile_id': child_profile_id,
                'content': content[:500],  # Truncate long content
                'severity': severity,
                'metadata': metadata or {}
            }
            
            # Write to dedicated safety log
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(incident_data) + '\n')
                
                # Also log through standard logger
                self.logger.warning(
                    f"Safety incident: {incident_type} (severity: {severity})",
                    extra={'profile_id': child_profile_id}
                )
            except Exception as e:  # Intentional catch-all: incident logger must not crash
                self.logger.error(f"Failed to log safety incident: {e}")


class PerformanceLogger:
    """Logger for performance metrics and diagnostics"""
    
    def __init__(self):
        self.logger = logging.getLogger("snflwr.performance")
        self._metrics: Dict[str, list] = {}
        self._lock = threading.Lock()
    
    def log_metric(self, metric_name: str, value: float, unit: str = "ms"):
        """Log a performance metric"""
        
        with self._lock:
            if metric_name not in self._metrics:
                self._metrics[metric_name] = []
            
            self._metrics[metric_name].append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'value': value,
                'unit': unit
            })
            
            # Keep only recent metrics (last 1000 per metric)
            if len(self._metrics[metric_name]) > 1000:
                self._metrics[metric_name] = self._metrics[metric_name][-1000:]
    
    def get_statistics(self, metric_name: str) -> Optional[Dict]:
        """Get statistics for a metric"""
        
        with self._lock:
            if metric_name not in self._metrics or not self._metrics[metric_name]:
                return None
            
            values = [m['value'] for m in self._metrics[metric_name]]
            
            return {
                'metric': metric_name,
                'count': len(values),
                'min': min(values),
                'max': max(values),
                'avg': sum(values) / len(values),
                'recent': values[-10:]  # Last 10 values
            }


class LoggerManager:
    """Centralized logging management for snflwr.ai"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize logging system"""
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.log_dir = system_config.LOG_DIR
        self.log_level = getattr(logging, system_config.LOG_LEVEL.upper())
        
        # Create log directory
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize loggers
        self._setup_root_logger()
        self._setup_application_logger()
        
        # Special loggers
        self.safety_logger = SafetyLogger(self.log_dir)
        self.performance_logger = PerformanceLogger()
    
    def _setup_root_logger(self):
        """Setup root logger configuration"""
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)

        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Add correlation ID filter and PII sanitizer
        correlation_filter = CorrelationIDFilter()
        pii_sanitizer = PIISanitizer()

        # Console handler (standard output)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(SnflwrFormatter(use_color=False))
        console_handler.addFilter(correlation_filter)
        console_handler.addFilter(pii_sanitizer)
        root_logger.addHandler(console_handler)
    
    def _setup_application_logger(self):
        """Setup main application logger with rotation"""
        app_logger = logging.getLogger("snflwr")
        app_logger.setLevel(self.log_level)
        app_logger.propagate = False  # Don't propagate to root

        # Add correlation ID filter and PII sanitizer to all handlers
        correlation_filter = CorrelationIDFilter()
        pii_sanitizer = PIISanitizer()

        # Determine if we should use structured logging (JSON)
        use_structured = os.getenv('LOG_FORMAT', 'standard').lower() == 'json'

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(SnflwrFormatter(
            use_color=sys.stdout.isatty(),
            structured=use_structured
        ))
        console_handler.addFilter(correlation_filter)
        console_handler.addFilter(pii_sanitizer)
        app_logger.addHandler(console_handler)

        # File handler with rotation (standard format)
        max_bytes = system_config.LOG_MAX_SIZE_MB * 1024 * 1024
        file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "snflwr.log",
            maxBytes=max_bytes,
            backupCount=system_config.LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(self.log_level)
        file_handler.setFormatter(SnflwrFormatter(structured=False))
        file_handler.addFilter(correlation_filter)
        file_handler.addFilter(pii_sanitizer)
        app_logger.addHandler(file_handler)

        # JSON-structured log file for log aggregation systems
        json_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "snflwr.json.log",
            maxBytes=max_bytes,
            backupCount=system_config.LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        json_handler.setLevel(self.log_level)
        json_handler.setFormatter(SnflwrFormatter(structured=True))
        json_handler.addFilter(correlation_filter)
        json_handler.addFilter(pii_sanitizer)
        app_logger.addHandler(json_handler)

        # Error-specific handler (JSON format for analysis)
        error_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "errors.log",
            maxBytes=max_bytes,
            backupCount=system_config.LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(SnflwrFormatter(structured=True))
        error_handler.addFilter(correlation_filter)
        error_handler.addFilter(pii_sanitizer)
        app_logger.addHandler(error_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get a logger instance"""
        return logging.getLogger(f"snflwr.{name}")
    
    def set_level(self, level: str):
        """Change logging level dynamically"""
        numeric_level = getattr(logging, level.upper())
        
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)
        
        app_logger = logging.getLogger("snflwr")
        app_logger.setLevel(numeric_level)
        
        for handler in app_logger.handlers:
            handler.setLevel(numeric_level)
    
    def log_system_info(self):
        """Log system information at startup"""
        logger = self.get_logger("system")

        info = system_config.get_info()
        logger.info("="*60)
        logger.info(f"snflwr.ai v{info['version']}")
        logger.info(f"Platform: {info['platform']}")
        logger.info(f"Database: {info['db_type']} (encrypted: {info['db_encryption']})")
        logger.info(f"Production: {info['production']}")
        logger.info(f"Redis: {info['redis_enabled']}")
        logger.info("="*60)
    
    def cleanup(self):
        """Cleanup logging resources"""
        logging.shutdown()


def mask_email(email: str) -> str:
    """Mask email for safe logging: j***@example.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"


def sanitize_log_value(value) -> str:
    """Sanitize a value for safe inclusion in log messages (CWE-117).

    Replaces newlines and carriage returns to prevent log injection / forging.
    """
    s = str(value) if not isinstance(value, str) else value
    return s.replace('\n', '\\n').replace('\r', '\\r')


# Initialize logger manager singleton
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module
    
    Usage:
        from utils.logger import get_logger
        logger = get_logger(__name__)
    """
    return logger_manager.get_logger(name)


def log_safety_incident(incident_type: str, profile_id: str, 
                        content: str, severity: str, 
                        metadata: Optional[Dict] = None):
    """
    Log a child safety incident
    
    Args:
        incident_type: Type of incident (e.g., 'prohibited_keyword')
        profile_id: Child profile ID
        content: Content that triggered the incident
        severity: 'minor', 'major', or 'critical'
        metadata: Additional context
    """
    logger_manager.safety_logger.log_incident(
        incident_type, profile_id, content, severity, metadata
    )


def log_performance_metric(metric_name: str, value: float, unit: str = "ms"):
    """
    Log a performance metric
    
    Args:
        metric_name: Name of the metric (e.g., 'model_response_time')
        value: Numeric value
        unit: Unit of measurement
    """
    logger_manager.performance_logger.log_metric(metric_name, value, unit)


def get_performance_statistics(metric_name: str) -> Optional[Dict]:
    """Get statistics for a performance metric"""
    return logger_manager.performance_logger.get_statistics(metric_name)


def log_system_startup():
    """Log system startup information"""
    logger_manager.log_system_info()


# Export public interface
__all__ = [
    # Core logger functions
    'get_logger',
    'logger_manager',
    'mask_email',

    # Correlation ID / distributed tracing
    'set_correlation_id',
    'get_correlation_id',
    'set_user_context',
    'correlation_id_var',
    'user_id_var',
    'session_id_var',
    'CorrelationIDFilter',
    'PIISanitizer',

    # Safety logging
    'log_safety_incident',

    # Performance metrics
    'log_performance_metric',
    'get_performance_statistics',

    # System
    'log_system_startup',
]
