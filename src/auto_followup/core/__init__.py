"""Core package - Pure business logic with no external dependencies."""

from auto_followup.core.business_days import (
    add_business_days,
    get_french_holidays,
    is_business_day,
    next_business_day,
    now_utc,
)
from auto_followup.core.exceptions import (
    AutoFollowupError,
    BusinessError,
    ConfigurationError,
    DraftNotFoundError,
    DraftNotSentError,
    ExternalServiceError,
    InfrastructureError,
    MailWriterError,
    MissingSentAtError,
    OdooError,
    ValidationError,
)

__all__ = [
    # Business days
    "add_business_days",
    "get_french_holidays",
    "is_business_day",
    "next_business_day",
    "now_utc",
    # Exceptions
    "AutoFollowupError",
    "BusinessError",
    "ConfigurationError",
    "DraftNotFoundError",
    "DraftNotSentError",
    "ExternalServiceError",
    "InfrastructureError",
    "MailWriterError",
    "MissingSentAtError",
    "OdooError",
    "ValidationError",
]
