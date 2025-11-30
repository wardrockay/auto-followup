"""
Application configuration.

Centralizes environment variables, constants, and settings
using dataclasses for type safety and immutability.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class FirestoreSettings:
    """Firestore collection settings."""
    
    draft_collection: str = field(
        default_factory=lambda: os.environ.get("DRAFT_COLLECTION", "email_drafts")
    )
    followup_collection: str = field(
        default_factory=lambda: os.environ.get("FOLLOWUP_COLLECTION", "email_followups")
    )


@dataclass(frozen=True)
class OdooSettings:
    """Odoo API settings."""
    
    base_url: str = field(
        default_factory=lambda: os.environ.get("ODOO_DB_URL", "").rstrip("/")
    )
    secret: str = field(
        default_factory=lambda: os.environ.get("ODOO_SECRET", "")
    )
    timeout_seconds: int = 15
    
    @property
    def is_configured(self) -> bool:
        """Check if Odoo is properly configured."""
        return bool(self.base_url and self.secret)
    
    @property
    def search_read_url(self) -> str:
        """Get the search_read endpoint URL."""
        return f"{self.base_url}/json/2/crm.lead/search_read"


@dataclass(frozen=True)
class MailWriterSettings:
    """Mail-writer service settings."""
    
    base_url: str = field(
        default_factory=lambda: os.environ.get("MAIL_WRITER_URL", "").rstrip("/")
    )
    timeout_seconds: int = 60
    
    @property
    def is_configured(self) -> bool:
        """Check if mail-writer is properly configured."""
        return bool(self.base_url)


@dataclass(frozen=True)
class FollowupScheduleSettings:
    """Follow-up scheduling settings."""
    
    # Business days after initial send for each followup
    schedule_days: Tuple[int, ...] = (3, 7, 10, 180)
    
    # Long-term followup day (kept even if prospect replies)
    long_term_day: int = 180
    
    @property
    def days_to_followup_number(self) -> Dict[int, int]:
        """Mapping of business days to followup sequence number."""
        return {
            3: 1,    # J+3 = first followup
            7: 2,    # J+7 = second followup
            10: 3,   # J+10 = third followup
            180: 4,  # J+180 = long-term followup
        }


@dataclass(frozen=True)
class Settings:
    """Main application settings."""
    
    firestore: FirestoreSettings = field(default_factory=FirestoreSettings)
    odoo: OdooSettings = field(default_factory=OdooSettings)
    mail_writer: MailWriterSettings = field(default_factory=MailWriterSettings)
    followup: FollowupScheduleSettings = field(default_factory=FollowupScheduleSettings)
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", 8080)))
    debug: bool = field(default_factory=lambda: os.environ.get("DEBUG", "false").lower() == "true")


# Singleton settings instance
settings = Settings()
