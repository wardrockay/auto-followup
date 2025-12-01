"""
Followup Scheduler Service.

Handles scheduling followup tasks for sent email drafts.
"""

from datetime import datetime, timezone
from typing import List, Optional

from auto_followup.config import settings
from auto_followup.core import add_business_days, now_utc
from auto_followup.core.exceptions import (
    DraftNotFoundError,
    DraftNotSentError,
    MissingSentAtError,
)
from auto_followup.infrastructure.firestore import (
    DraftRepository,
    EmailDraft,
    FollowupRepository,
    FollowupStatus,
    FollowupTask,
    ScheduleResult,
)
from auto_followup.infrastructure.logging import get_logger, log_duration


logger = get_logger(__name__)


class SchedulerService:
    """
    Service for scheduling followup tasks.
    
    Responsible for:
    - Validating draft eligibility for followups
    - Calculating scheduled dates using business days
    - Creating followup tasks in Firestore
    """
    
    def __init__(
        self,
        draft_repository: Optional[DraftRepository] = None,
        followup_repository: Optional[FollowupRepository] = None,
    ) -> None:
        self._draft_repo = draft_repository or DraftRepository()
        self._followup_repo = followup_repository or FollowupRepository()
    
    def _validate_draft_for_scheduling(self, draft: EmailDraft) -> None:
        """
        Validate that a draft is eligible for followup scheduling.
        
        Args:
            draft: The email draft to validate.
            
        Raises:
            DraftNotSentError: If draft hasn't been sent.
            MissingSentAtError: If sent draft lacks sent_at timestamp.
        """
        if not draft.is_sent:
            raise DraftNotSentError(
                draft.doc_id,
                draft.draft_status or "unknown"
            )
        
        if draft.sent_at is None:
            raise MissingSentAtError(draft.doc_id)
    
    def _calculate_followup_schedule(
        self,
        sent_at: datetime,
    ) -> List[FollowupTask]:
        """
        Calculate followup tasks based on the schedule configuration.
        
        Args:
            sent_at: When the original email was sent.
            
        Returns:
            List of FollowupTask instances (without doc_id).
        """
        tasks = []
        schedule = settings.followup
        
        for days_after in schedule.schedule_days:
            scheduled_date = add_business_days(sent_at, days_after)
            followup_number = schedule.days_to_followup_number.get(days_after, 0)
            
            task = FollowupTask(
                doc_id="",  # Will be assigned by Firestore
                draft_id="",  # Will be set by caller
                followup_number=followup_number,
                days_after_initial=days_after,
                scheduled_for=scheduled_date,
                status=FollowupStatus.SCHEDULED,
                created_at=now_utc(),
            )
            tasks.append(task)
        
        return tasks
    
    @log_duration("schedule_followups")
    def schedule_for_draft(self, draft_id: str) -> ScheduleResult:
        """
        Schedule followup tasks for a specific draft.
        
        Args:
            draft_id: The draft document ID.
            
        Returns:
            ScheduleResult with scheduling outcome.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
            DraftNotSentError: If draft hasn't been sent.
            MissingSentAtError: If sent_at is missing.
        """
        logger.info(
            f"Scheduling followups for draft {draft_id}",
            extra={"extra_fields": {"draft_id": draft_id}}
        )
        
        draft = self._draft_repo.get_by_id(draft_id)
        
        self._validate_draft_for_scheduling(draft)
        
        if self._followup_repo.has_existing_followups(draft_id):
            logger.info(
                f"Draft {draft_id} already has followups scheduled",
                extra={"extra_fields": {"draft_id": draft_id}}
            )
            return ScheduleResult(
                draft_id=draft_id,
                scheduled_count=0,
                skipped_reason="Followups already scheduled",
            )
        
        tasks = self._calculate_followup_schedule(draft.sent_at)
        
        tasks_with_draft_id = [
            FollowupTask(
                doc_id=task.doc_id,
                draft_id=draft_id,
                followup_number=task.followup_number,
                days_after_initial=task.days_after_initial,
                scheduled_for=task.scheduled_for,
                status=task.status,
                created_at=task.created_at,
            )
            for task in tasks
        ]
        
        followup_ids = self._followup_repo.create_batch(tasks_with_draft_id)
        
        # Update draft with followup_ids
        self._draft_repo.update_followup_ids(draft_id, followup_ids)
        
        logger.info(
            f"Scheduled {len(followup_ids)} followups for draft {draft_id}",
            extra={"extra_fields": {
                "draft_id": draft_id,
                "scheduled_count": len(followup_ids),
                "followup_ids": followup_ids,
                "scheduled_dates": [
                    t.scheduled_for.isoformat() for t in tasks_with_draft_id
                ],
            }}
        )
        
        return ScheduleResult(
            draft_id=draft_id,
            scheduled_count=len(followup_ids),
            followup_ids=followup_ids,
        )
    
    @log_duration("schedule_all_sent_drafts")
    def schedule_all_sent_drafts(self) -> List[ScheduleResult]:
        """
        Schedule followups for all sent drafts that don't have followups yet.
        
        Only processes drafts that:
        - Have status='sent'
        - Don't have no_followup=True
        - Are not themselves followups (is_followup=False, followup_number=0)
        - Don't already have followup tasks scheduled
        
        Returns:
            List of ScheduleResult for each processed draft.
        """
        results = []
        processed = 0
        errors = 0
        skipped = 0
        
        logger.info("Starting bulk followup scheduling for sent drafts without followups")
        
        for draft in self._draft_repo.get_sent_drafts():
            try:
                # Check if followups already exist (this is also checked in schedule_for_draft
                # but we check here too to avoid unnecessary processing)
                if self._followup_repo.has_existing_followups(draft.doc_id):
                    logger.debug(
                        f"Skipping draft {draft.doc_id}: already has followups",
                        extra={"extra_fields": {"draft_id": draft.doc_id}}
                    )
                    skipped += 1
                    continue
                
                result = self.schedule_for_draft(draft.doc_id)
                results.append(result)
                
                if result.success:
                    processed += 1
                else:
                    skipped += 1
                    
            except Exception as e:
                errors += 1
                logger.error(
                    f"Failed to schedule followups for draft {draft.doc_id}: {e}",
                    extra={"extra_fields": {
                        "draft_id": draft.doc_id,
                        "error_type": type(e).__name__,
                    }}
                )
                results.append(ScheduleResult(
                    draft_id=draft.doc_id,
                    scheduled_count=0,
                    skipped_reason=str(e),
                ))
        
        logger.info(
            f"Bulk scheduling complete: {processed} scheduled, {skipped} skipped, {errors} errors",
            extra={"extra_fields": {
                "scheduled_count": processed,
                "skipped_count": skipped,
                "error_count": errors,
                "total_drafts": len(results),
            }}
        )
        
        return results
    
    @log_duration("sync_followup_ids")
    def sync_missing_followup_ids(self) -> List[dict]:
        """
        Synchronize followup_ids for drafts that have followups but missing the field.
        
        Finds all drafts that have followup tasks in the followup collection
        but don't have the followup_ids field populated in the draft document.
        
        Returns:
            List of sync results with draft_id and followup_ids updated.
        """
        results = []
        
        # Get all draft IDs that have followups
        draft_followups_map = self._followup_repo.get_all_draft_ids_with_followups()
        
        logger.info(
            f"Starting sync for {len(draft_followups_map)} drafts with followups",
            extra={"extra_fields": {"total_drafts": len(draft_followups_map)}}
        )
        
        synced_count = 0
        skipped_count = 0
        error_count = 0
        
        for draft_id, followup_ids in draft_followups_map.items():
            try:
                # Get the draft to check current state
                draft = self._draft_repo.get_by_id(draft_id)
                
                # Check if followup_ids already exists
                existing_followup_ids = draft.raw_data.get("followup_ids")
                
                if existing_followup_ids and len(existing_followup_ids) > 0:
                    logger.debug(
                        f"Draft {draft_id} already has followup_ids, skipping",
                        extra={"extra_fields": {
                            "draft_id": draft_id,
                            "existing_count": len(existing_followup_ids)
                        }}
                    )
                    skipped_count += 1
                    results.append({
                        "draft_id": draft_id,
                        "status": "skipped",
                        "reason": "followup_ids already exists",
                        "followup_ids": existing_followup_ids,
                    })
                    continue
                
                # Update draft with followup_ids
                self._draft_repo.update_followup_ids(draft_id, followup_ids)
                synced_count += 1
                
                results.append({
                    "draft_id": draft_id,
                    "status": "synced",
                    "followup_ids": followup_ids,
                    "count": len(followup_ids),
                })
                
            except DraftNotFoundError:
                error_count += 1
                logger.warning(
                    f"Draft {draft_id} not found, but has followups",
                    extra={"extra_fields": {
                        "draft_id": draft_id,
                        "followup_count": len(followup_ids)
                    }}
                )
                results.append({
                    "draft_id": draft_id,
                    "status": "error",
                    "reason": "draft not found",
                    "followup_ids": followup_ids,
                })
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Failed to sync followup_ids for draft {draft_id}: {e}",
                    extra={"extra_fields": {
                        "draft_id": draft_id,
                        "error_type": type(e).__name__,
                    }}
                )
                results.append({
                    "draft_id": draft_id,
                    "status": "error",
                    "reason": str(e),
                    "followup_ids": followup_ids,
                })
        
        logger.info(
            f"Sync complete: {synced_count} synced, {skipped_count} skipped, {error_count} errors",
            extra={"extra_fields": {
                "synced_count": synced_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
            }}
        )
        
        return results
    
    @log_duration("update_followups_scheduled_flags")
    def update_missing_followups_scheduled_flags(self) -> List[dict]:
        """
        Update followups_scheduled flag for drafts that have followup_ids but missing the flag.
        
        Returns:
            List of update results.
        """
        results = []
        updated_count = 0
        error_count = 0
        
        logger.info("Starting update of missing followups_scheduled flags")
        
        for draft in self._draft_repo.get_drafts_with_followup_ids_missing_flag():
            try:
                self._draft_repo.update_followups_scheduled_flag(draft.doc_id)
                updated_count += 1
                
                results.append({
                    "draft_id": draft.doc_id,
                    "status": "updated",
                    "followup_ids": draft.raw_data.get("followup_ids", []),
                })
                
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Failed to update followups_scheduled for draft {draft.doc_id}: {e}",
                    extra={"extra_fields": {
                        "draft_id": draft.doc_id,
                        "error_type": type(e).__name__,
                    }}
                )
                results.append({
                    "draft_id": draft.doc_id,
                    "status": "error",
                    "reason": str(e),
                })
        
        logger.info(
            f"Update complete: {updated_count} updated, {error_count} errors",
            extra={"extra_fields": {
                "updated_count": updated_count,
                "error_count": error_count,
            }}
        )
        
        return results
    
    @log_duration("migrate_pending_to_scheduled")
    def migrate_pending_to_scheduled(self) -> dict:
        """
        Migrate all followups with status 'pending' to 'scheduled'.
        
        Returns:
            Migration result with count.
        """
        logger.info("Starting migration of pending followups to scheduled status")
        
        migrated_count = self._followup_repo.migrate_pending_to_scheduled()
        
        logger.info(
            f"Migration complete: {migrated_count} followups updated",
            extra={"extra_fields": {"migrated_count": migrated_count}}
        )
        
        return {
            "migrated_count": migrated_count,
            "message": f"Successfully migrated {migrated_count} followups from 'pending' to 'scheduled'"
        }
    
    @log_duration("migrate_to_old_schema")
    def migrate_to_old_schema(self) -> dict:
        """
        Migrate followup documents from new schema to old schema.
        Changes days_after_sent -> days_after_initial and scheduled_date -> scheduled_for.
        
        Returns:
            Dictionary with migration results.
        """
        logger.info("Starting migration to old schema")
        
        migrated_count = self._followup_repo.migrate_to_old_schema()
        
        logger.info(
            f"Migration complete: {migrated_count} followups migrated to old schema",
            extra={"extra_fields": {"migrated_count": migrated_count}}
        )
        
        return {
            "migrated_count": migrated_count,
            "message": f"Successfully migrated {migrated_count} followups to old schema (days_after_initial, scheduled_for)"
        }
