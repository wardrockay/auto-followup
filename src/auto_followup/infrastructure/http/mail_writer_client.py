"""
Mail-Writer Service Client.

Handles communication with the mail-writer service for generating followup emails.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from auto_followup.config import settings
from auto_followup.core.exceptions import MailWriterError
from auto_followup.infrastructure.circuit_breaker import (
    circuit_breaker,
    get_circuit_breaker,
    CircuitBreakerConfig,
)
from auto_followup.infrastructure.logging import get_logger, log_duration
from auto_followup.infrastructure.metrics import get_metrics


logger = get_logger(__name__)


@dataclass(frozen=True)
class FollowupEmailRequest:
    """Request data for generating a followup email."""
    draft_id: str
    followup_number: int
    odoo_contact_id: str
    recipient_email: str
    company_name: Optional[str] = None
    contact_first_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to API request payload."""
        payload = {
            "draft_id": self.draft_id,
            "followup_number": self.followup_number,
            "odoo_contact_id": self.odoo_contact_id,
            "recipient_email": self.recipient_email,
        }
        
        if self.company_name:
            payload["company_name"] = self.company_name
        if self.contact_first_name:
            payload["contact_first_name"] = self.contact_first_name
        
        return payload


@dataclass(frozen=True)
class FollowupEmailResponse:
    """Response from mail-writer service."""
    success: bool
    draft_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "FollowupEmailResponse":
        """Create from API response."""
        return cls(
            success=data.get("success", False),
            draft_id=data.get("draft_id"),
            message=data.get("message"),
            error=data.get("error"),
        )


class MailWriterClient:
    """
    Client for the mail-writer service.
    
    Handles followup email generation requests.
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """
        Initialize mail-writer client.
        
        Args:
            base_url: Mail-writer service URL.
            timeout: Request timeout in seconds.
        """
        self._base_url = (base_url or settings.mail_writer.base_url).rstrip("/")
        self._timeout = timeout or settings.mail_writer.timeout_seconds
        self._session: Optional[requests.Session] = None
    
    @property
    def session(self) -> requests.Session:
        """Get or create HTTP session with retry logic."""
        if self._session is None:
            self._session = requests.Session()
            
            retry_strategy = Retry(
                total=2,
                backoff_factor=1.0,
                status_forcelist=[502, 503, 504],
                allowed_methods=["POST"],
            )
            
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=5,
                pool_maxsize=5,
            )
            
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
            
            self._session.headers.update({
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        
        return self._session
    
    @log_duration("mail_writer_generate_followup")
    def generate_followup(
        self,
        request_data: FollowupEmailRequest,
    ) -> FollowupEmailResponse:
        # Use circuit breaker for external call
        cb = get_circuit_breaker(
            "mail-writer",
            CircuitBreakerConfig(failure_threshold=3, timeout_seconds=60.0),
        )
        return cb.call(self._do_generate_followup, request_data)

    def _do_generate_followup(
        self,
        request_data: FollowupEmailRequest,
    ) -> FollowupEmailResponse:
        """
        Request generation of a followup email.
        
        Args:
            request_data: Followup email request data.
            
        Returns:
            FollowupEmailResponse with result.
            
        Raises:
            MailWriterError: If request fails.
        """
        endpoint = f"{self._base_url}/generate-followup"
        payload = request_data.to_dict()
        
        logger.info(
            f"Requesting followup generation",
            extra={"extra_fields": {
                "draft_id": request_data.draft_id,
                "followup_number": request_data.followup_number,
                "recipient_email": request_data.recipient_email,
            }}
        )
        
        try:
            response = self.session.post(
                endpoint,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            
            result = FollowupEmailResponse.from_api_response(response.json())
            
            if not result.success:
                raise MailWriterError(
                    f"Mail-writer returned error: {result.error}"
                )
            
            logger.info(
                f"Followup generated successfully",
                extra={"extra_fields": {
                    "draft_id": request_data.draft_id,
                    "followup_number": request_data.followup_number,
                    "generated_draft_id": result.draft_id,
                }}
            )
            
            # Record success metric
            get_metrics().external_requests_total.inc(service="mail-writer", status="success")
            
            return result
            
        except requests.exceptions.Timeout as e:
            get_metrics().external_requests_total.inc(service="mail-writer", status="timeout")
            logger.error(
                f"Mail-writer timeout",
                extra={"extra_fields": {
                    "draft_id": request_data.draft_id,
                    "timeout": self._timeout,
                }}
            )
            raise MailWriterError(f"Mail-writer timeout: {e}") from e
            
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"Mail-writer HTTP error: {e.response.status_code}",
                extra={"extra_fields": {
                    "draft_id": request_data.draft_id,
                    "status_code": e.response.status_code,
                    "response_body": e.response.text[:500] if e.response.text else None,
                }}
            )
            raise MailWriterError(
                f"Mail-writer error {e.response.status_code}: {e.response.text}"
            ) from e
            
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Mail-writer request failed: {e}",
                extra={"extra_fields": {
                    "draft_id": request_data.draft_id,
                    "error_type": type(e).__name__,
                }}
            )
            raise MailWriterError(f"Mail-writer request failed: {e}") from e
    
    def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None
    
    def __enter__(self) -> "MailWriterClient":
        return self
    
    def __exit__(self, *args: Any) -> None:
        self.close()


# Global client instance
_mail_writer_client: Optional[MailWriterClient] = None


def get_mail_writer_client() -> MailWriterClient:
    """Get global mail-writer client instance."""
    global _mail_writer_client
    if _mail_writer_client is None:
        _mail_writer_client = MailWriterClient()
    return _mail_writer_client
