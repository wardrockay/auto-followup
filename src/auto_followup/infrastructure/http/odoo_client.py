"""
Odoo CRM API Client.

Handles communication with the Odoo CRM API for retrieving contact information.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from auto_followup.config import settings
from auto_followup.core.exceptions import OdooError
from auto_followup.infrastructure.logging import get_logger, log_duration


logger = get_logger(__name__)


@dataclass(frozen=True)
class OdooContact:
    """Contact information from Odoo."""
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    company_name: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    raw_data: Dict[str, Any] = None
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "OdooContact":
        """Create from API response."""
        return cls(
            id=str(data.get("id", "")),
            email=data.get("email"),
            name=data.get("name"),
            company_name=data.get("company_name"),
            phone=data.get("phone"),
            mobile=data.get("mobile"),
            raw_data=data,
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
        self._base_url = (base_url or settings.odoo.url).rstrip("/")
        self._api_key = api_key or settings.odoo.api_key
        self._timeout = timeout or settings.odoo.timeout
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
    
    @log_duration("odoo_get_contact")
    def get_contact(self, contact_id: str) -> OdooContact:
        """
        Get contact information by ID.
        
        Args:
            contact_id: Odoo contact ID.
            
        Returns:
            OdooContact instance.
            
        Raises:
            OdooError: If contact retrieval fails.
        """
        logger.info(
            f"Fetching Odoo contact {contact_id}",
            extra={"extra_fields": {"contact_id": contact_id}}
        )
        
        response = self._request("GET", f"/contacts/{contact_id}")
        
        return OdooContact.from_api_response(response)
    
    @log_duration("odoo_get_contact_info")
    def get_contact_info_for_followup(
        self,
        contact_id: str,
    ) -> Dict[str, Any]:
        """
        Get contact info formatted for followup email generation.
        
        Args:
            contact_id: Odoo contact ID.
            
        Returns:
            Dictionary with contact information for mail-writer.
        """
        contact = self.get_contact(contact_id)
        
        return {
            "contact_id": contact.id,
            "email": contact.email,
            "name": contact.name,
            "company_name": contact.company_name,
            "phone": contact.phone or contact.mobile,
        }
    
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
