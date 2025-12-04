"""
Followup Processor Service.

Handles processing of due followup tasks.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

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
    get_odoo_client,
    MailWriterClient,
    OdooClient,
    OdooLead,
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
        odoo_client: Optional[OdooClient] = None,
    ) -> None:
        self._draft_repo = draft_repository or DraftRepository()
        self._followup_repo = followup_repository or FollowupRepository()
        self._mail_writer = mail_writer_client or get_mail_writer_client()
        self._odoo_client = odoo_client or get_odoo_client()
    
    def _build_email_request(
        self,
        task: FollowupTask,
    ) -> FollowupEmailRequest:
        """
        Build email request from followup task and Odoo data.
        
        Fetches fresh contact data from Odoo CRM and email history from Firestore.
        
        Args:
            task: The followup task.
            
        Returns:
            FollowupEmailRequest instance.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
            ExternalServiceError: If Odoo fetch fails.
        """
        # Get draft for x_external_id
        draft = self._draft_repo.get_by_id(task.draft_id)
        raw = draft.raw_data
        x_external_id = raw.get("x_external_id") or task.draft_id
        
        logger.info(
            f"Building email request for draft_id={task.draft_id}, x_external_id={x_external_id}",
            extra={"extra_fields": {
                "draft_id": task.draft_id,
                "x_external_id": x_external_id,
                "followup_number": task.followup_number,
            }}
        )
        
        # Fetch email history from previous drafts
        email_history = self._get_email_history(x_external_id, task.followup_number)
        
        # Fetch fresh data from Odoo
        odoo_lead = self._odoo_client.get_lead_by_external_id(x_external_id)
        
        if not odoo_lead:
            logger.error(
                f"Lead not found in Odoo for x_external_id={x_external_id}",
                extra={"extra_fields": {
                    "draft_id": task.draft_id,
                    "x_external_id": x_external_id,
                }}
            )
            raise ExternalServiceError(
                f"Lead not found in Odoo for x_external_id: {x_external_id}"
            )
        
        # Validate required fields
        if not odoo_lead.email or "@" not in odoo_lead.email:
            raise ExternalServiceError(
                f"Invalid email in Odoo for x_external_id {x_external_id}: {odoo_lead.email}"
            )
        
        if not odoo_lead.first_name:
            raise ExternalServiceError(
                f"Missing first_name in Odoo for x_external_id {x_external_id}"
            )
        
        if not odoo_lead.last_name:
            raise OdooError(
                f"Missing last_name in Odoo for x_external_id {x_external_id}"
            )
        
        if not odoo_lead.partner_name:
            raise OdooError(
                f"Missing partner_name in Odoo for x_external_id {x_external_id}"
            )
        
        if not odoo_lead.website:
            raise OdooError(
                f"Missing website in Odoo for x_external_id {x_external_id}"
            )
        
        logger.info(
            f"Successfully fetched Odoo data: email={odoo_lead.email}, name={odoo_lead.first_name} {odoo_lead.last_name}",
            extra={"extra_fields": {
                "draft_id": task.draft_id,
                "x_external_id": x_external_id,
                "odoo_id": odoo_lead.odoo_id,
            }}
        )
        
        # Build request with fresh Odoo data
        return FollowupEmailRequest(
            draft_id=task.draft_id,
            first_name=odoo_lead.first_name,
            last_name=odoo_lead.last_name,
            email=odoo_lead.email,
            website=odoo_lead.website or "",
            partner_name=odoo_lead.partner_name,
            x_external_id=odoo_lead.x_external_id,
            followup_number=task.followup_number,
            function=odoo_lead.function or "",
            description=odoo_lead.description or "",
            version_group_id=raw.get("version_group_id", ""),
            odoo_id=odoo_lead.odoo_id,
            reply_to_thread_id=raw.get("reply_to_thread_id", ""),
            reply_to_message_id=raw.get("reply_to_message_id", ""),
            original_subject=raw.get("original_subject", ""),
            email_history=email_history,
        )
    
    def _get_email_history(self, x_external_id: str, current_followup_number: int) -> List[Dict[str, str]]:
        """
        Get email history for previous drafts with the same x_external_id.
        
        Retrieves all sent drafts with lower followup numbers to provide context
        for the current followup generation.
        
        Args:
            x_external_id: External ID (Pharow ID).
            current_followup_number: Current followup number (to exclude future drafts).
            
        Returns:
            List of email history items with subject and body.
        """
        try:
            # Get all drafts with same x_external_id
            drafts = self._draft_repo.get_by_external_id(x_external_id)
            
            email_history = []
            
            for draft in drafts:
                draft_data = draft.raw_data
                
                # Only include sent drafts with lower followup numbers
                if draft.draft_status != "sent":
                    continue
                
                draft_followup_number = draft_data.get("followup_number", 0)
                if draft_followup_number >= current_followup_number:
                    continue
                
                # Extract subject and body
                subject = draft_data.get("original_subject") or draft_data.get("subject", "")
                body = draft_data.get("body", "")
                
                if subject or body:
                    email_history.append({
                        "subject": subject,
                        "body": body
                    })
            
            # Sort by followup_number (oldest first)
            email_history.sort(key=lambda x: x.get("followup_number", 0))
            
            logger.info(
                f"Retrieved {len(email_history)} emails from history for x_external_id={x_external_id}",
                extra={"extra_fields": {
                    "x_external_id": x_external_id,
                    "history_count": len(email_history),
                }}
            )
            
            return email_history
            
        except Exception as e:
            logger.warning(
                f"Failed to retrieve email history for x_external_id={x_external_id}: {str(e)}",
                extra={"extra_fields": {
                    "x_external_id": x_external_id,
                    "error": str(e),
                }}
            )
            return []
    
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
            status=FollowupStatus.SCHEDULED,
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
