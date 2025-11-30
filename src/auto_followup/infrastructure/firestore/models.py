"""
Firestore Data Models.

Domain models representing Firestore documents.
Uses dataclasses for immutability and type safety.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class FollowupStatus(str, Enum):
    """Status of a followup task."""
    PENDING = "pending"
    FAILED = "failed"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EmailDraft:
    """
    Represents an email draft document from Firestore.
    
    Attributes:
        doc_id: Firestore document ID.
        odoo_contact_id: Odoo contact identifier.
        sent_at: Timestamp when the email was sent.
        draft_status: Current status of the draft.
        recipient_email: Email address of the recipient.
        company_name: Name of the recipient's company.
        contact_first_name: First name of the contact.
        raw_data: Original document data for additional fields.
    """
    doc_id: str
    odoo_contact_id: Optional[str] = None
    sent_at: Optional[datetime] = None
    draft_status: Optional[str] = None
    recipient_email: Optional[str] = None
    company_name: Optional[str] = None
    contact_first_name: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: Dict[str, Any]) -> "EmailDraft":
        """
        Create an EmailDraft from Firestore document data.
        
        Args:
            doc_id: Document ID.
            data: Document data dictionary.
            
        Returns:
            EmailDraft instance.
        """
        sent_at = data.get("sent_at")
        if sent_at and hasattr(sent_at, "timestamp"):
            sent_at = datetime.fromtimestamp(sent_at.timestamp())
        
        return cls(
            doc_id=doc_id,
            odoo_contact_id=data.get("odoo_contact_id"),
            sent_at=sent_at,
            draft_status=data.get("draft_status"),
            recipient_email=data.get("recipient_email"),
            company_name=data.get("company_name"),
            contact_first_name=data.get("contact_first_name"),
            raw_data=data,
        )
    
    @property
    def is_sent(self) -> bool:
        """Check if the draft has been sent."""
        return self.draft_status == "sent"


@dataclass(frozen=True)
class FollowupTask:
    """
    Represents a followup task document from Firestore.
    
    Attributes:
        doc_id: Firestore document ID.
        draft_id: Reference to the original draft.
        followup_number: Which followup in the sequence (1-4).
        days_after_sent: Days after original email was sent.
        scheduled_date: Date when the followup should be processed.
        status: Current status of the followup.
        created_at: When the task was created.
        processed_at: When the task was processed.
        error_message: Error message if processing failed.
    """
    doc_id: str
    draft_id: str
    followup_number: int
    days_after_sent: int
    scheduled_date: datetime
    status: FollowupStatus = FollowupStatus.PENDING
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: Dict[str, Any]) -> "FollowupTask":
        """
        Create a FollowupTask from Firestore document data.
        
        Args:
            doc_id: Document ID.
            data: Document data dictionary.
            
        Returns:
            FollowupTask instance.
        """
        def parse_datetime(value: Any) -> Optional[datetime]:
            if value is None:
                return None
            if hasattr(value, "timestamp"):
                return datetime.fromtimestamp(value.timestamp())
            if isinstance(value, datetime):
                return value
            return None
        
        return cls(
            doc_id=doc_id,
            draft_id=data.get("draft_id", ""),
            followup_number=data.get("followup_number", 0),
            days_after_sent=data.get("days_after_sent", 0),
            scheduled_date=parse_datetime(data.get("scheduled_date")) or datetime.now(),
            status=FollowupStatus(data.get("status", "pending")),
            created_at=parse_datetime(data.get("created_at")),
            processed_at=parse_datetime(data.get("processed_at")),
            error_message=data.get("error_message"),
        )
    
    def to_firestore(self) -> Dict[str, Any]:
        """
        Convert to Firestore document data.
        
        Returns:
            Dictionary suitable for Firestore storage.
        """
        data = {
            "draft_id": self.draft_id,
            "followup_number": self.followup_number,
            "days_after_sent": self.days_after_sent,
            "scheduled_date": self.scheduled_date,
            "status": self.status.value,
        }
        
        if self.created_at:
            data["created_at"] = self.created_at
        if self.processed_at:
            data["processed_at"] = self.processed_at
        if self.error_message:
            data["error_message"] = self.error_message
        
        return data


@dataclass(frozen=True)
class ScheduleResult:
    """Result of scheduling followups for a draft."""
    draft_id: str
    scheduled_count: int
    followup_ids: list = field(default_factory=list)
    skipped_reason: Optional[str] = None
    
    @property
    def success(self) -> bool:
        return self.scheduled_count > 0


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single followup."""
    followup_id: str
    draft_id: str
    followup_number: int
    success: bool
    error_message: Optional[str] = None
