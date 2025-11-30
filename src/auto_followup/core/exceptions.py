"""
Custom exceptions for the auto-followup service.

Provides a hierarchy of business and infrastructure exceptions
for proper error handling and HTTP status code mapping.
"""

from typing import Optional


class AutoFollowupError(Exception):
    """Base exception for all auto-followup errors."""
    
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# =============================================================================
# Business Errors (4xx)
# =============================================================================

class BusinessError(AutoFollowupError):
    """Base exception for business logic errors (typically 4xx)."""
    pass


class DraftNotFoundError(BusinessError):
    """Raised when a draft document is not found."""
    
    def __init__(self, draft_id: str):
        super().__init__(
            f"Draft not found: {draft_id}",
            {"draft_id": draft_id}
        )
        self.draft_id = draft_id


class DraftNotSentError(BusinessError):
    """Raised when trying to schedule followups for a non-sent draft."""
    
    def __init__(self, draft_id: str, current_status: str):
        super().__init__(
            f"Draft not sent yet: {draft_id} (status: {current_status})",
            {"draft_id": draft_id, "current_status": current_status}
        )


class MissingSentAtError(BusinessError):
    """Raised when sent_at is missing from a sent draft."""
    
    def __init__(self, draft_id: str):
        super().__init__(
            f"sent_at field not found for draft: {draft_id}",
            {"draft_id": draft_id}
        )


class ValidationError(BusinessError):
    """Raised when request validation fails."""
    
    def __init__(self, field: str, message: str):
        super().__init__(
            f"Validation error on '{field}': {message}",
            {"field": field}
        )
        self.field = field


# =============================================================================
# Infrastructure Errors (5xx)
# =============================================================================

class InfrastructureError(AutoFollowupError):
    """Base exception for infrastructure errors (typically 5xx)."""
    pass


class ConfigurationError(InfrastructureError):
    """Raised when a required configuration is missing."""
    
    def __init__(self, config_name: str, message: Optional[str] = None):
        msg = message or f"Configuration missing: {config_name}"
        super().__init__(msg, {"config_name": config_name})
        self.config_name = config_name


class ExternalServiceError(InfrastructureError):
    """Raised when an external service call fails."""
    
    def __init__(
        self,
        service_name: str,
        message: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ):
        super().__init__(
            f"{service_name} error: {message}",
            {
                "service_name": service_name,
                "status_code": status_code,
                "duration_ms": duration_ms,
            }
        )
        self.service_name = service_name
        self.status_code = status_code
        self.duration_ms = duration_ms


class OdooError(ExternalServiceError):
    """Raised when Odoo API call fails."""
    
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ):
        super().__init__("Odoo", message, status_code, duration_ms)


class MailWriterError(ExternalServiceError):
    """Raised when mail-writer service call fails."""
    
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ):
        super().__init__("MailWriter", message, status_code, duration_ms)
