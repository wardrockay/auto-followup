"""
Firestore Repositories.

Repository pattern implementation for Firestore collections.
Provides clean abstraction over Firestore operations.
"""

from datetime import datetime, timezone
from typing import Generator, List, Optional

from google.cloud import firestore

from auto_followup.config import settings
from auto_followup.core.exceptions import DraftNotFoundError
from auto_followup.infrastructure.firestore.models import (
    EmailDraft,
    FollowupStatus,
    FollowupTask,
)
from auto_followup.infrastructure.logging import get_logger, log_duration


logger = get_logger(__name__)


class FirestoreClient:
    """Firestore client singleton."""
    
    _instance: Optional[firestore.Client] = None
    
    @classmethod
    def get_client(cls) -> firestore.Client:
        """Get or create Firestore client."""
        if cls._instance is None:
            cls._instance = firestore.Client()
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """Reset client (for testing)."""
        cls._instance = None


class DraftRepository:
    """Repository for email draft documents."""
    
    def __init__(self, client: Optional[firestore.Client] = None) -> None:
        self._client = client or FirestoreClient.get_client()
        self._collection_name = settings.firestore.draft_collection
    
    @property
    def collection(self):
        """Get the drafts collection reference."""
        return self._client.collection(self._collection_name)
    
    @log_duration("fetch_draft")
    def get_by_id(self, draft_id: str) -> EmailDraft:
        """
        Get a draft by its ID.
        
        Args:
            draft_id: The document ID.
            
        Returns:
            EmailDraft instance.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
        """
        doc = self.collection.document(draft_id).get()
        
        if not doc.exists:
            raise DraftNotFoundError(draft_id)
        
        return EmailDraft.from_firestore(doc.id, doc.to_dict())
    
    def exists(self, draft_id: str) -> bool:
        """Check if a draft exists."""
        return self.collection.document(draft_id).get().exists
    
    def get_sent_drafts(self) -> Generator[EmailDraft, None, None]:
        """
        Get all drafts with status 'sent'.
        
        Yields:
            EmailDraft instances for sent drafts.
        """
        query = self.collection.where("draft_status", "==", "sent")
        
        for doc in query.stream():
            yield EmailDraft.from_firestore(doc.id, doc.to_dict())


class FollowupRepository:
    """Repository for followup task documents."""
    
    def __init__(self, client: Optional[firestore.Client] = None) -> None:
        self._client = client or FirestoreClient.get_client()
        self._collection_name = settings.firestore.followup_collection
    
    @property
    def collection(self):
        """Get the followups collection reference."""
        return self._client.collection(self._collection_name)
    
    def get_by_id(self, followup_id: str) -> Optional[FollowupTask]:
        """
        Get a followup by its ID.
        
        Args:
            followup_id: The document ID.
            
        Returns:
            FollowupTask instance or None if not found.
        """
        doc = self.collection.document(followup_id).get()
        
        if not doc.exists:
            return None
        
        return FollowupTask.from_firestore(doc.id, doc.to_dict())
    
    @log_duration("create_followup")
    def create(self, task: FollowupTask) -> str:
        """
        Create a new followup task.
        
        Args:
            task: FollowupTask to create.
            
        Returns:
            Created document ID.
        """
        doc_ref = self.collection.document()
        doc_ref.set(task.to_firestore())
        
        logger.info(
            f"Created followup {doc_ref.id}",
            extra={"extra_fields": {
                "followup_id": doc_ref.id,
                "draft_id": task.draft_id,
                "followup_number": task.followup_number,
                "scheduled_date": task.scheduled_date.isoformat(),
            }}
        )
        
        return doc_ref.id
    
    def create_batch(self, tasks: List[FollowupTask]) -> List[str]:
        """
        Create multiple followup tasks in a batch.
        
        Args:
            tasks: List of FollowupTask instances.
            
        Returns:
            List of created document IDs.
        """
        batch = self._client.batch()
        doc_ids = []
        
        for task in tasks:
            doc_ref = self.collection.document()
            batch.set(doc_ref, task.to_firestore())
            doc_ids.append(doc_ref.id)
        
        batch.commit()
        
        logger.info(
            f"Created {len(doc_ids)} followups in batch",
            extra={"extra_fields": {
                "count": len(doc_ids),
                "followup_ids": doc_ids,
            }}
        )
        
        return doc_ids
    
    def update_status(
        self,
        followup_id: str,
        status: FollowupStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Update followup status.
        
        Args:
            followup_id: Document ID.
            status: New status.
            error_message: Optional error message.
        """
        update_data = {
            "status": status.value,
            "processed_at": datetime.now(timezone.utc),
        }
        
        if error_message:
            update_data["error_message"] = error_message
        
        self.collection.document(followup_id).update(update_data)
        
        logger.info(
            f"Updated followup {followup_id} to {status.value}",
            extra={"extra_fields": {
                "followup_id": followup_id,
                "new_status": status.value,
                "has_error": error_message is not None,
            }}
        )
    
    def get_by_draft_id(self, draft_id: str) -> Generator[FollowupTask, None, None]:
        """
        Get all followups for a draft.
        
        Args:
            draft_id: The draft document ID.
            
        Yields:
            FollowupTask instances.
        """
        query = self.collection.where("draft_id", "==", draft_id)
        
        for doc in query.stream():
            yield FollowupTask.from_firestore(doc.id, doc.to_dict())
    
    def get_pending_for_draft(self, draft_id: str) -> Generator[FollowupTask, None, None]:
        """
        Get pending followups for a draft.
        
        Args:
            draft_id: The draft document ID.
            
        Yields:
            Pending FollowupTask instances.
        """
        query = (
            self.collection
            .where("draft_id", "==", draft_id)
            .where("status", "==", FollowupStatus.PENDING.value)
        )
        
        for doc in query.stream():
            yield FollowupTask.from_firestore(doc.id, doc.to_dict())
    
    def get_due_followups(
        self,
        status: FollowupStatus = FollowupStatus.PENDING,
        before: Optional[datetime] = None,
    ) -> Generator[FollowupTask, None, None]:
        """
        Get followups due for processing.
        
        Args:
            status: Filter by status.
            before: Get followups scheduled before this time.
            
        Yields:
            FollowupTask instances.
        """
        cutoff = before or datetime.now(timezone.utc)
        
        query = (
            self.collection
            .where("status", "==", status.value)
            .where("scheduled_date", "<=", cutoff)
        )
        
        for doc in query.stream():
            yield FollowupTask.from_firestore(doc.id, doc.to_dict())
    
    def get_failed_followups(self) -> Generator[FollowupTask, None, None]:
        """
        Get all failed followups.
        
        Yields:
            Failed FollowupTask instances.
        """
        return self.get_due_followups(status=FollowupStatus.FAILED, before=None)
    
    def cancel_pending_for_draft(self, draft_id: str) -> int:
        """
        Cancel all pending followups for a draft.
        
        Args:
            draft_id: The draft document ID.
            
        Returns:
            Number of cancelled followups.
        """
        cancelled_count = 0
        
        for task in self.get_pending_for_draft(draft_id):
            self.update_status(task.doc_id, FollowupStatus.CANCELLED)
            cancelled_count += 1
        
        logger.info(
            f"Cancelled {cancelled_count} followups for draft {draft_id}",
            extra={"extra_fields": {
                "draft_id": draft_id,
                "cancelled_count": cancelled_count,
            }}
        )
        
        return cancelled_count
    
    def has_existing_followups(self, draft_id: str) -> bool:
        """Check if a draft already has followups scheduled."""
        query = self.collection.where("draft_id", "==", draft_id).limit(1)
        return len(list(query.stream())) > 0
