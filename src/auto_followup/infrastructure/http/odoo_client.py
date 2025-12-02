"""
Odoo CRM API Client.

Handles communication with the Odoo CRM API for retrieving contact information.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from auto_followup.config import settings
from auto_followup.core.exceptions import OdooError
from auto_followup.infrastructure.circuit_breaker import (
    get_circuit_breaker,
    CircuitBreakerConfig,
)
from auto_followup.infrastructure.logging import get_logger, log_duration
from auto_followup.infrastructure.metrics import get_metrics


logger = get_logger(__name__)


@dataclass(frozen=True)
class OdooLead:
    """Lead information from Odoo CRM."""
    odoo_id: int
    first_name: str
    last_name: str
    email: str
    website: str
    partner_name: str
    function: str
    description: str
    x_external_id: str
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "OdooLead":
        """Create from Odoo search_read response."""
        # Split contact_name into first_name and last_name
        contact_name = data.get("contact_name", "")
        name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        return cls(
            odoo_id=data.get("id", 0),
            first_name=first_name,
            last_name=last_name,
            email=data.get("email_normalized", ""),
            website=data.get("website", ""),
            partner_name=data.get("partner_name", ""),
            function=data.get("function", ""),
            description=data.get("description", ""),
            x_external_id=data.get("x_external_id", ""),
        )


class OdooClient:
    """
    Client for Odoo CRM API.
    
    Provides methods to retrieve and update contact information.
    Uses connection pooling and retry logic for resilience.
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """
        Initialize Odoo client.
        
        Args:
            base_url: Odoo API base URL.
            api_key: API key for authentication.
            timeout: Request timeout in seconds.
        """
        self._base_url = (base_url or settings.odoo.base_url).rstrip("/")
        self._api_key = api_key or settings.odoo.secret
        self._timeout = timeout or settings.odoo.timeout_seconds
        self._session: Optional[requests.Session] = None
    
    @property
    def session(self) -> requests.Session:
        """Get or create HTTP session with retry logic."""
        if self._session is None:
            self._session = requests.Session()
            
            retry_strategy = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST", "PUT"],
            )
            
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=10,
                pool_maxsize=10,
            )
            
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
            
            self._session.headers.update({
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        
        return self._session
    
    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the Odoo API.
        
        Args:
            method: HTTP method.
            endpoint: API endpoint (relative to base URL).
            **kwargs: Additional request arguments.
            
        Returns:
            Response JSON data.
            
        Raises:
            OdooError: If request fails.
        """
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        
        try:
            response = self.session.request(
                method,
                url,
                timeout=self._timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout as e:
            get_metrics().external_requests_total.inc(service="odoo", status="timeout")
            logger.error(
                f"Odoo API timeout: {endpoint}",
                extra={"extra_fields": {
                    "endpoint": endpoint,
                    "timeout": self._timeout,
                }}
            )
            raise OdooError(f"Odoo API timeout: {e}") from e
            
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"Odoo API HTTP error: {e.response.status_code}",
                extra={"extra_fields": {
                    "endpoint": endpoint,
                    "status_code": e.response.status_code,
                    "response_body": e.response.text[:500] if e.response.text else None,
                }}
            )
            raise OdooError(
                f"Odoo API error {e.response.status_code}: {e.response.text}"
            ) from e
            
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Odoo API request failed: {e}",
                extra={"extra_fields": {
                    "endpoint": endpoint,
                    "error_type": type(e).__name__,
                }}
            )
            raise OdooError(f"Odoo API request failed: {e}") from e
    
    @log_duration("odoo_get_lead")
    def get_lead_by_external_id(self, x_external_id: str) -> Optional[OdooLead]:
        """
        Get lead information by x_external_id using search_read.
        
        Args:
            x_external_id: External ID (Pharow ID).
            
        Returns:
            OdooLead instance or None if not found.
            
        Raises:
            OdooError: If lead retrieval fails.
        """
        logger.info(
            f"Fetching lead from Odoo by x_external_id: {x_external_id}",
            extra={"extra_fields": {"x_external_id": x_external_id}}
        )
        
        payload = {
            "domain": [["x_external_id", "ilike", x_external_id]],
            "fields": [
                "id", "email_normalized", "website", "contact_name",
                "partner_name", "function", "description", "x_external_id"
            ]
        }
        
        try:
            response = self._request(
                "POST",
                "/json/2/crm.lead/search_read",
                json=payload,
            )
            
            if not response or len(response) == 0:
                logger.warning(
                    f"No lead found in Odoo for x_external_id: {x_external_id}",
                    extra={"extra_fields": {"x_external_id": x_external_id}}
                )
                get_metrics().external_requests_total.inc(service="odoo", status="not_found")
                return None
            
            lead = OdooLead.from_api_response(response[0])
            
            logger.info(
                f"Successfully fetched lead from Odoo: odoo_id={lead.odoo_id}, email={lead.email}",
                extra={"extra_fields": {
                    "x_external_id": x_external_id,
                    "odoo_id": lead.odoo_id,
                    "email": lead.email,
                }}
            )
            
            get_metrics().external_requests_total.inc(service="odoo", status="success")
            return lead
            
        except OdooError:
            get_metrics().external_requests_total.inc(service="odoo", status="error")
            raise
    
    def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None
    
    def __enter__(self) -> "OdooClient":
        return self
    
    def __exit__(self, *args: Any) -> None:
        self.close()


# Global client instance
_odoo_client: Optional[OdooClient] = None


def get_odoo_client() -> OdooClient:
    """Get global Odoo client instance."""
    global _odoo_client
    if _odoo_client is None:
        _odoo_client = OdooClient()
    return _odoo_client
