"""
Infrastructure Layer.

This layer contains all external dependencies and adapters:
- Logging configuration
- Firestore repositories
- HTTP clients (Odoo, Mail-writer)
"""

from auto_followup.infrastructure.logging import (
    get_logger,
    log_duration,
    log_request_context,
    logger,
    StructuredLogger,
)


__all__ = [
    "get_logger",
    "log_duration",
    "log_request_context",
    "logger",
    "StructuredLogger",
]
