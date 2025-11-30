"""
Followup Processor Service.

Handles processing of due followup tasks.
"""

from datetime import datetime, timezone
from typing import List, Optional

from auto_followup.core.exceptions import (
    DraftNotFoundError,
    ExternalServiceError,
)
from auto_followup.infrastructure.firestore import (
    DraftRepository,
    FollowupRepository,
    FollowupStatus,
    FollowupTask,
    ProcessingResult,
)
from auto_followup.infrastructure.http import (
    FollowupEmailRequest,
    get_mail_writer_client,
    MailWriterClient,
)
from auto_followup.infrastructure.logging import get_logger, log_duration


logger = get_logger(__name__)


class ProcessorService:
    """
    Service for processing followup tasks.
    
    Responsible for:
    - Finding due followups
    - Calling mail-writer service
    - Updating followup status
    """
    
    def __init__(
        self,
        draft_repository: Optional[DraftRepository] = None,
        followup_repository: Optional[FollowupRepository] = None,
        mail_writer_client: Optional[MailWriterClient] = None,
    ) -> None:
        self._draft_repo = draft_repository or DraftRepository()
        self._followup_repo = followup_repository or FollowupRepository()
        self._mail_writer = mail_writer_client or get_mail_writer_client()
    
    def _build_email_request(
        self,
        task: FollowupTask,
    ) -> FollowupEmailRequest:
        """
        Build email request from followup task and draft data.
        
        Args:
            task: The followup task.
            
        Returns:
            FollowupEmailRequest instance.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
        """
        draft = self._draft_repo.get_by_id(task.draft_id)
        
        return FollowupEmailRequest(
            draft_id=task.draft_id,
            followup_number=task.followup_number,
            odoo_contact_id=draft.odoo_contact_id or "",
            recipient_email=draft.recipient_email or "",
            company_name=draft.company_name,
            contact_first_name=draft.contact_first_name,
        )
    
    @log_duration("process_single_followup")
    def process_followup(self, task: FollowupTask) -> ProcessingResult:
        """
        Process a single followup task.
        
        Args:
            task: The followup task to process.
            
        Returns:
            ProcessingResult with outcome.
        """
        logger.info(
            f"Processing followup {task.doc_id}",
            extra={"extra_fields": {
                "followup_id": task.doc_id,
                "draft_id": task.draft_id,
                "followup_number": task.followup_number,
            }}
        )
        
        try:
            email_request = self._build_email_request(task)
            
            self._mail_writer.generate_followup(email_request)
            
            self._followup_repo.update_status(
                task.doc_id,
                FollowupStatus.DONE,
            )
            
            logger.info(
                f"Successfully processed followup {task.doc_id}",
                extra={"extra_fields": {
                    "followup_id": task.doc_id,
                    "draft_id": task.draft_id,
                    "followup_number": task.followup_number,
                }}
            )
            
            return ProcessingResult(
                followup_id=task.doc_id,
                draft_id=task.draft_id,
                followup_number=task.followup_number,
                success=True,
            )
            
        except (DraftNotFoundError, ExternalServiceError) as e:
            error_message = str(e)
            
            self._followup_repo.update_status(
                task.doc_id,
                FollowupStatus.FAILED,
                error_message=error_message,
            )
            
            logger.error(
                f"Failed to process followup {task.doc_id}: {error_message}",
                extra={"extra_fields": {
                    "followup_id": task.doc_id,
                    "draft_id": task.draft_id,
                    "followup_number": task.followup_number,
                    "error_type": type(e).__name__,
                }}
            )
            
            return ProcessingResult(
                followup_id=task.doc_id,
                draft_id=task.draft_id,
                followup_number=task.followup_number,
                success=False,
                error_message=error_message,
            )
    
    @log_duration("process_pending_followups")
    def process_due_followups(
        self,
        before: Optional[datetime] = None,
    ) -> List[ProcessingResult]:
        """
        Process all followups that are due.
        
        Args:
            before: Process followups scheduled before this time.
                    Defaults to current UTC time.
            
        Returns:
            List of ProcessingResult for each processed followup.
        """
        cutoff = before or datetime.now(timezone.utc)
        results: List[ProcessingResult] = []
        
        logger.info(
            f"Processing followups due before {cutoff.isoformat()}",
            extra={"extra_fields": {"cutoff": cutoff.isoformat()}}
        )
        
        for task in self._followup_repo.get_due_followups(
            status=FollowupStatus.PENDING,
            before=cutoff,
        ):
            result = self.process_followup(task)
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        failure_count = len(results) - success_count
        
        logger.info(
            f"Processed {len(results)} followups: {success_count} success, {failure_count} failed",
            extra={"extra_fields": {
                "total_count": len(results),
                "success_count": success_count,
                "failure_count": failure_count,
            }}
        )
        
        return results
