"""
Structured JSON Logger for Google Cloud Run.

Provides structured logging compatible with Google Cloud Logging:
- JSON output on stdout
- Google Cloud severity levels
- Automatic contextual fields (request_id, duration_ms, etc.)
- Cloud Run request correlation
"""

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

from flask import Flask, g, request


F = TypeVar("F", bound=Callable[..., Any])


class JsonFormatter(logging.Formatter):
    """JSON formatter for Google Cloud Logging."""
    
    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }
    
    SENSITIVE_PATTERNS = frozenset([
        "password", "secret", "token", "api_key", "apikey",
        "authorization", "auth", "credential", "private",
    ])
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "severity": self.SEVERITY_MAP.get(record.levelno, "INFO"),
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
        }
        
        self._add_flask_context(log_entry)
        self._add_extra_fields(record, log_entry)
        self._add_exception_info(record, log_entry)
        self._add_source_location(record, log_entry)
        
        return json.dumps(log_entry, ensure_ascii=False, default=str)
    
    def _add_flask_context(self, log_entry: Dict[str, Any]) -> None:
        """Add Flask request context to log entry."""
        try:
            for attr in ("request_id", "task_id", "endpoint"):
                if hasattr(g, attr) and getattr(g, attr):
                    log_entry[attr] = getattr(g, attr)
        except RuntimeError:
            pass  # Outside Flask context
    
    def _add_extra_fields(self, record: logging.LogRecord, log_entry: Dict[str, Any]) -> None:
        """Add extra fields passed to the log."""
        if hasattr(record, "extra_fields") and record.extra_fields:
            for key, value in record.extra_fields.items():
                if not self._is_sensitive(key):
                    log_entry[key] = self._sanitize_value(value)
    
    def _add_exception_info(self, record: logging.LogRecord, log_entry: Dict[str, Any]) -> None:
        """Add exception info if present."""
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
    
    def _add_source_location(self, record: logging.LogRecord, log_entry: Dict[str, Any]) -> None:
        """Add source location for warnings and above."""
        if record.levelno >= logging.WARNING:
            log_entry["source"] = {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            }
    
    def _is_sensitive(self, key: str) -> bool:
        """Check if a key corresponds to sensitive data."""
        key_lower = key.lower()
        return any(pattern in key_lower for pattern in self.SENSITIVE_PATTERNS)
    
    def _sanitize_value(self, value: Any) -> Any:
        """Truncate long string values."""
        if isinstance(value, str) and len(value) > 1000:
            return value[:1000] + "... [truncated]"
        return value


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter for adding structured fields to logs."""
    
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        extra = kwargs.get("extra", {})
        extra_fields = extra.pop("extra_fields", {})
        
        if self.extra:
            extra_fields = {**self.extra, **extra_fields}
        
        kwargs["extra"] = {**extra, "extra_fields": extra_fields}
        return msg, kwargs
    
    def with_fields(self, **fields: Any) -> "StructuredLogger":
        """Create a new logger with additional fields."""
        new_extra = {**self.extra, **fields}
        return StructuredLogger(self.logger, new_extra)


def get_logger(name: str = "app") -> StructuredLogger:
    """
    Create and configure a structured JSON logger.
    
    Args:
        name: Logger name.
        
    Returns:
        Configured StructuredLogger instance.
    """
    base_logger = logging.getLogger(name)
    
    if not base_logger.handlers:
        base_logger.setLevel(logging.DEBUG)
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(JsonFormatter())
        
        base_logger.addHandler(handler)
        base_logger.propagate = False
    
    return StructuredLogger(base_logger, {})


def log_request_context(app: Flask) -> None:
    """
    Flask middleware to add request context to logs.
    
    Args:
        app: Flask application instance.
    """
    @app.before_request
    def before_request() -> None:
        trace_header = request.headers.get("X-Cloud-Trace-Context", "")
        g.request_id = trace_header.split("/")[0] or str(uuid.uuid4())[:8]
        g.task_id = request.headers.get("X-CloudTasks-TaskName")
        g.endpoint = request.endpoint
        g.start_time = time.time()
    
    @app.after_request
    def after_request(response):
        duration_ms = None
        if hasattr(g, "start_time"):
            duration_ms = int((time.time() - g.start_time) * 1000)
        
        request_logger = get_logger("request")
        request_logger.info(
            f"{request.method} {request.path} -> {response.status_code}",
            extra={"extra_fields": {
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }}
        )
        
        return response


def log_duration(operation: str) -> Callable[[F], F]:
    """
    Decorator to measure and log operation duration.
    
    Args:
        operation: Operation name for logging.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(func.__module__)
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start) * 1000)
                logger.info(
                    f"{operation} completed",
                    extra={"extra_fields": {
                        "operation": operation,
                        "duration_ms": duration_ms,
                        "status": "success",
                    }}
                )
                return result
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                logger.error(
                    f"{operation} failed: {e}",
                    extra={"extra_fields": {
                        "operation": operation,
                        "duration_ms": duration_ms,
                        "status": "error",
                        "error_type": type(e).__name__,
                    }}
                )
                raise
        return wrapper  # type: ignore
    return decorator


# Global application logger
logger = get_logger("auto-followup")
