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
        schedule = settings.followup_schedule
        
        for days_after in schedule.days:
            scheduled_date = add_business_days(sent_at, days_after)
            followup_number = schedule.get_followup_number(days_after)
            
            task = FollowupTask(
                doc_id="",  # Will be assigned by Firestore
                draft_id="",  # Will be set by caller
                followup_number=followup_number,
                days_after_sent=days_after,
                scheduled_date=scheduled_date,
                status=FollowupStatus.PENDING,
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
                days_after_sent=task.days_after_sent,
                scheduled_date=task.scheduled_date,
                status=task.status,
                created_at=task.created_at,
            )
            for task in tasks
        ]
        
        followup_ids = self._followup_repo.create_batch(tasks_with_draft_id)
        
        logger.info(
            f"Scheduled {len(followup_ids)} followups for draft {draft_id}",
            extra={"extra_fields": {
                "draft_id": draft_id,
                "scheduled_count": len(followup_ids),
                "followup_ids": followup_ids,
                "scheduled_dates": [
                    t.scheduled_date.isoformat() for t in tasks_with_draft_id
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
        
        Returns:
            List of ScheduleResult for each processed draft.
        """
        results = []
        processed = 0
        errors = 0
        
        for draft in self._draft_repo.get_sent_drafts():
            try:
                result = self.schedule_for_draft(draft.doc_id)
                results.append(result)
                processed += 1
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
            f"Bulk scheduling complete: {processed} processed, {errors} errors",
            extra={"extra_fields": {
                "processed_count": processed,
                "error_count": errors,
            }}
        )
        
        return results
