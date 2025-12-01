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
    
    def update_followup_ids(self, draft_id: str, followup_ids: List[str]) -> None:
        """
        Update draft document with followup task IDs.
        
        Args:
            draft_id: The draft document ID.
            followup_ids: List of followup task document IDs.
        """
        self.collection.document(draft_id).update({
            "followup_ids": followup_ids,
            "followups_scheduled": True
        })
        
        logger.info(
            f"Updated draft {draft_id} with {len(followup_ids)} followup IDs",
            extra={"extra_fields": {
                "draft_id": draft_id,
                "followup_ids": followup_ids,
            }}
        )
    
    def get_drafts_with_followup_ids_missing_flag(self) -> Generator[EmailDraft, None, None]:
        """
        Get all drafts that have followup_ids but missing followups_scheduled flag.
        
        Yields:
            EmailDraft instances that need the followups_scheduled flag updated.
        """
        # Get all drafts - we'll filter in Python since Firestore can't easily query for missing fields
        for doc in self.collection.stream():
            data = doc.to_dict()
            
            # Check if has followup_ids but not followups_scheduled
            followup_ids = data.get("followup_ids")
            followups_scheduled = data.get("followups_scheduled")
            
            if followup_ids and len(followup_ids) > 0 and not followups_scheduled:
                yield EmailDraft.from_firestore(doc.id, data)
    
    def update_followups_scheduled_flag(self, draft_id: str) -> None:
        """
        Update draft document with followups_scheduled flag.
        
        Args:
            draft_id: The draft document ID.
        """
        self.collection.document(draft_id).update({
            "followups_scheduled": True
        })
        
        logger.info(
            f"Updated draft {draft_id} with followups_scheduled flag",
            extra={"extra_fields": {"draft_id": draft_id}}
        )
    
    def get_sent_drafts(self) -> Generator[EmailDraft, None, None]:
        """
        Get all drafts with status 'sent' that are eligible for followups.
        
        Excludes:
        - Drafts with no_followup=True
        - Drafts that are themselves followups (is_followup=True or followup_number > 0)
        - Drafts that already have followup_ids (already processed)
        
        Yields:
            EmailDraft instances for sent drafts eligible for followups.
        """
        query = self.collection.where("status", "==", "sent")
        
        for doc in query.stream():
            data = doc.to_dict()
            
            # Skip drafts marked as no_followup
            if data.get("no_followup", False):
                logger.debug(
                    f"Skipping draft {doc.id}: no_followup=True",
                    extra={"extra_fields": {"draft_id": doc.id}}
                )
                continue
            
            # Skip drafts that are themselves followups
            if data.get("is_followup", False) or data.get("followup_number", 0) > 0:
                logger.debug(
                    f"Skipping draft {doc.id}: is a followup",
                    extra={"extra_fields": {"draft_id": doc.id}}
                )
                continue
            
            # Skip drafts that already have followup_ids field
            followup_ids = data.get("followup_ids")
            if followup_ids and len(followup_ids) > 0:
                logger.debug(
                    f"Skipping draft {doc.id}: already has followup_ids",
                    extra={"extra_fields": {"draft_id": doc.id, "followup_ids_count": len(followup_ids)}}
                )
                continue
            
            yield EmailDraft.from_firestore(doc.id, data)


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
        Get pending/scheduled followups for a draft.
        
        Args:
            draft_id: The draft document ID.
            
        Yields:
            Pending/Scheduled FollowupTask instances.
        """
        # Query for both 'scheduled' (new) and 'pending' (legacy)
        for status_value in [FollowupStatus.SCHEDULED.value, FollowupStatus.PENDING.value]:
            query = (
                self.collection
                .where("draft_id", "==", draft_id)
                .where("status", "==", status_value)
            )
            
            for doc in query.stream():
                yield FollowupTask.from_firestore(doc.id, doc.to_dict())
    
    def get_due_followups(
        self,
        status: FollowupStatus = FollowupStatus.SCHEDULED,
        before: Optional[datetime] = None,
    ) -> Generator[FollowupTask, None, None]:
        """
        Get followups due for processing.
        
        Args:
            status: Filter by status (default: SCHEDULED).
            before: Get followups scheduled before this time.
            
        Yields:
            FollowupTask instances.
        """
        cutoff = before or datetime.now(timezone.utc)
        
        # Query for both 'scheduled' (new) and 'pending' (legacy) if status is SCHEDULED
        statuses_to_query = [status.value]
        if status == FollowupStatus.SCHEDULED:
            statuses_to_query.append(FollowupStatus.PENDING.value)
        
        for status_value in statuses_to_query:
            query = (
                self.collection
                .where("status", "==", status_value)
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
    
    def migrate_pending_to_scheduled(self) -> int:
        """
        Migrate all followups with status 'pending' to 'scheduled'.
        
        Returns:
            Number of followups migrated.
        """
        batch = self._client.batch()
        count = 0
        batch_size = 0
        
        query = self.collection.where("status", "==", "pending")
        
        for doc in query.stream():
            batch.update(doc.reference, {"status": "scheduled"})
            count += 1
            batch_size += 1
            
            # Commit in batches of 500 (Firestore limit)
            if batch_size >= 500:
                batch.commit()
                batch = self._client.batch()
                batch_size = 0
        
        # Commit remaining
        if batch_size > 0:
            batch.commit()
        
        logger.info(
            f"Migrated {count} followups from 'pending' to 'scheduled'",
            extra={"extra_fields": {"migrated_count": count}}
        )
        
        return count
    
    def get_all_draft_ids_with_followups(self) -> dict[str, List[str]]:
        """
        Get all draft IDs that have followups and their followup IDs.
        
        Returns:
            Dictionary mapping draft_id to list of followup_ids.
        """
        draft_followups = {}
        
        for doc in self.collection.stream():
            data = doc.to_dict()
            draft_id = data.get("draft_id")
            
            if draft_id:
                if draft_id not in draft_followups:
                    draft_followups[draft_id] = []
                draft_followups[draft_id].append(doc.id)
        
        logger.info(
            f"Found {len(draft_followups)} drafts with followups",
            extra={"extra_fields": {"draft_count": len(draft_followups)}}
        )
        
        return draft_followups
    
    def migrate_to_old_schema(self) -> int:
        """
        Migrate followup documents from new schema to old schema.
        Changes days_after_sent -> days_after_initial and scheduled_date -> scheduled_for.
        
        Returns:
            Number of followups migrated.
        """
        batch = self._client.batch()
        count = 0
        batch_size = 0
        
        # Find all followups with new schema fields
        for doc in self.collection.stream():
            data = doc.to_dict()
            
            # Check if document uses new schema
            if "days_after_sent" in data or "scheduled_date" in data:
                updates = {}
                
                # Migrate days_after_sent -> days_after_initial
                if "days_after_sent" in data:
                    updates["days_after_initial"] = data["days_after_sent"]
                    updates["days_after_sent"] = firestore.DELETE_FIELD
                
                # Migrate scheduled_date -> scheduled_for
                if "scheduled_date" in data:
                    updates["scheduled_for"] = data["scheduled_date"]
                    updates["scheduled_date"] = firestore.DELETE_FIELD
                
                if updates:
                    batch.update(doc.reference, updates)
                    count += 1
                    batch_size += 1
                    
                    # Commit in batches of 500 (Firestore limit)
                    if batch_size >= 500:
                        batch.commit()
                        batch = self._client.batch()
                        batch_size = 0
        
        # Commit remaining
        if batch_size > 0:
            batch.commit()
        
        logger.info(
            f"Migrated {count} followups to old schema",
            extra={"extra_fields": {"migrated_count": count}}
        )
        
        return count
